"""BM25 over an in-memory inverted index. Standard library only, no dependencies.

**BM25 is a ranking formula, not a storage engine** — that is the whole reason Phase 1a needs no
database (docs/progress.md, 2026-08-14, superseding an earlier DuckDB-FTS proposal). Term
frequencies in a `dict`, postings in a `dict`, ~100 lines. Measured over the real 6,722 chunks:
~200 ms to build, 3-4 ms per query.

A BM25 *library* was rejected as well: `rank-bm25` is unmaintained and `bm25s` drags in
numpy/scipy, and neither earns a dependency over this much stdlib — which also keeps `k1` and `b`
directly in hand, which docs/chunking.md §5 hands forward as the thing to tune against the gold
set.

**`k1` and `b` are query-time arguments, not build-time state.** That is what makes a parameter
sweep cheap: `make eval-retrieval` re-scores the same index for every candidate value instead of
rebuilding it. It is also why nothing here reads `config.yaml` — the caller passes the values, so
this module has no opinion and no import of the configuration.
"""

import heapq
import math
import re
from collections import Counter
from collections.abc import Iterable

#: Alphanumeric runs. Deliberately not a word-character class: `\w` keeps underscores, which only
#: ever appear here inside doc ids that leaked into text, and never carries retrieval signal.
_TOKEN_RE = re.compile(r"[a-z0-9]+")

#: A small closed-class list, applied to documents and queries alike so the two stay comparable.
#: Kept deliberately short: BM25 already discounts common terms through IDF, so a long list mostly
#: risks deleting a term that is meaningful in this domain. Every candidate was checked against the
#: gold set's wording first — which is why "part" (Medicare Part A/B/C/D) and "plan" are absent
#: despite being frequent, and why negations ("no", "not") are kept, since an NCD's coverage rule
#: can turn on one.
_STOPWORD_TEXT = """
a an and are as at be been by do does for from had has have how i if in into is it its
me my of on or that the their then there these they this to was were what when where which
who why will with you your
"""

STOPWORDS = frozenset(_STOPWORD_TEXT.split())


def tokenize(text: str) -> list[str]:
    """Lowercase, split on alphanumeric runs, drop stopwords.

    No stemming. A stdlib Porter implementation is another ~120 lines of surface area whose effect
    on this corpus is unmeasured, and Phase 1b's embeddings address vocabulary mismatch far better
    than suffix-stripping would. If lexical recall turns out to be the bottleneck, measure it with
    `make eval-retrieval` before adding one.
    """
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in STOPWORDS]


class Bm25Index:
    """An inverted index plus the Okapi BM25 scoring function.

    Documents are addressed internally by position, and the caller's ids are held alongside. That
    indirection is what lets `search()` take a `keep` set of positions — the mechanism behind
    `search_corpus(source=...)`, which must filter *before* the top-k cut rather than after it, or
    a source filter would silently return fewer than `k` results.

    One index spans all three corpora on purpose. A per-source index would compute IDF over a
    different vocabulary for each, so the same chunk would score differently depending on which
    filter the agent happened to pass — a confound in exactly the comparison Phase 1b exists to
    make.
    """

    __slots__ = ("_avgdl", "_doc_len", "_ids", "_postings")

    def __init__(
        self,
        ids: list[str],
        doc_len: list[int],
        postings: dict[str, list[tuple[int, int]]],
    ) -> None:
        self._ids = ids
        self._doc_len = doc_len
        self._postings = postings
        # Falls back to 1.0 rather than 0.0 when there is nothing to average, so the length
        # normalisation term in `search()` can never divide by zero. Only reachable for an empty
        # index or one whose every document is pure stopwords; both are degenerate, and a
        # ZeroDivisionError raised from inside a scoring loop is a bad way to find out.
        self._avgdl = (sum(doc_len) / len(doc_len)) if doc_len else 0.0
        self._avgdl = self._avgdl or 1.0

    @classmethod
    def build(cls, entries: Iterable[tuple[str, str]]) -> "Bm25Index":
        """Index `(id, text)` pairs in the order given.

        Insertion order is preserved and never re-sorted, so a run over the same `chunks.jsonl` is
        byte-for-byte reproducible — the same determinism rule `chunking/pipeline.py` states, and
        for the same reason: an eval score that moves because a `dict` iterated differently is
        unattributable.
        """
        ids: list[str] = []
        doc_len: list[int] = []
        postings: dict[str, list[tuple[int, int]]] = {}

        for position, (doc_id, text) in enumerate(entries):
            tokens = tokenize(text)
            ids.append(doc_id)
            doc_len.append(len(tokens))
            for term, tf in Counter(tokens).items():
                postings.setdefault(term, []).append((position, tf))

        return cls(ids, doc_len, postings)

    def __len__(self) -> int:
        return len(self._ids)

    @property
    def avgdl(self) -> float:
        return self._avgdl

    @property
    def vocabulary_size(self) -> int:
        return len(self._postings)

    def document_frequency(self, term: str) -> int:
        return len(self._postings.get(term, ()))

    def idf(self, term: str) -> float:
        """Okapi IDF with the `+0.5` smoothing, in the `log(1 + x)` form.

        The unsmoothed variant goes *negative* for a term appearing in more than half the corpus,
        which lets a common word actively push a document down the ranking — visible here as a
        chunk being penalised for containing "coverage". `log1p` keeps it non-negative.
        """
        df = self.document_frequency(term)
        if df == 0:
            return 0.0
        return math.log1p((len(self._ids) - df + 0.5) / (df + 0.5))

    def search(
        self,
        query: str,
        *,
        k: int,
        k1: float,
        b: float,
        keep: frozenset[int] | None = None,
    ) -> list[tuple[str, float]]:
        """The top `k` `(id, score)` pairs, highest first.

        Ties break on the document id rather than on position, so the ranking does not depend on
        corpus file order. Two chunks with an identical score are genuinely indistinguishable to
        BM25, and letting insertion order decide would make a re-chunk look like a retrieval
        regression.

        Returns `[]` for a query that tokenizes to nothing (all stopwords, or punctuation only)
        rather than falling back to something arbitrary — an empty result is the honest answer and
        the agent can reformulate.
        """
        terms = tokenize(query)
        if not terms or k <= 0 or not self._ids:
            return []

        scores: dict[int, float] = {}
        for term, qtf in Counter(terms).items():
            postings = self._postings.get(term)
            if not postings:
                continue
            idf = self.idf(term)
            if idf <= 0.0:
                continue
            # A term repeated in the query counts once per occurrence. Okapi's full query-side
            # saturation needs a third parameter (k3) that is conventionally set high enough to be
            # a no-op, so this is the same thing without the knob.
            weight = idf * qtf
            for position, tf in postings:
                if keep is not None and position not in keep:
                    continue
                norm = 1.0 - b + b * (self._doc_len[position] / self._avgdl)
                scores[position] = scores.get(position, 0.0) + weight * (
                    tf * (k1 + 1.0) / (tf + k1 * norm)
                )

        if not scores:
            return []
        top = heapq.nsmallest(k, scores.items(), key=lambda kv: (-kv[1], self._ids[kv[0]]))
        return [(self._ids[position], score) for position, score in top]
