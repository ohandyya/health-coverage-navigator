"""`CorpusIndex` — the chunks, the BM25 index over them, and the four search primitives.

This is the layer the tools are thin wrappers around, and it is deliberately free of any
`pydantic_ai` import: `make eval-retrieval` and every retrieval test drive these methods directly,
with no model, no key, and no network. The tools in `tools.py` add the trace and the run-scoped
bookkeeping; the searching itself is here.

**Indexing is over `Chunk.retrieval_text`, retrieval returns `Chunk.text`.** docs/chunking.md §6
settles that split: the context header (`NCD 30.3 > Acupuncture > Indications...`) is real lexical
signal — "deductible" in a glossary chunk's title is a genuine hit — but it is not part of the
source document, so it must not reach a quotation. The header is a computed property and is never
stored, which is what keeps that distinction free.
"""

import re
from collections.abc import Sequence
from functools import lru_cache

from health_coverage_navigator.agent.bm25 import Bm25Index
from health_coverage_navigator.agent.models import ChunkHit, CorpusOverview, DocumentSummary
from health_coverage_navigator.chunking.models import Chunk, ChunkSet
from health_coverage_navigator.corpus import CORPUS_NAMES, CorpusName, chunks_path

#: Ceiling on a `grep_corpus` pattern. A regex is arbitrary user (here, model) input compiled and
#: run against 6,722 chunks, and Python's engine has no timeout — so a nested-quantifier pattern
#: can wedge a request thread. A length cap does not make catastrophic backtracking impossible, but
#: it removes the room to construct one by accident, which is the realistic case.
MAX_PATTERN_CHARS = 200

#: Hard ceiling on how many hits any one tool call may return, whatever `k` or `limit` was asked
#: for. Each hit can be 1,200 characters, so an unbounded `k` is a context-window problem before it
#: is a latency one.
MAX_HITS = 10


class ChunksNotBuiltError(RuntimeError):
    """`chunks.jsonl` is missing for at least one corpus.

    A real, expected state rather than a bug: `chunks.jsonl` is git-ignored (docs/chunking.md §7),
    so a fresh clone has none until `make chunk` runs. It is raised — rather than degraded around —
    because every alternative is worse: answering from a partial corpus produces a confident answer
    with a silently missing source, and falling back to canned text would let stub output be
    mistaken for real output, which is the exact failure `HealthResponse.stub` exists to prevent.
    """


class CorpusIndex:
    """Every chunk across the three text corpora, indexed for lexical retrieval."""

    __slots__ = ("_bm25", "_by_doc", "_by_id", "_chunks", "_source_positions")

    def __init__(self, chunks: Sequence[Chunk]) -> None:
        self._chunks = list(chunks)
        self._by_id = {c.id: c for c in self._chunks}

        by_doc: dict[str, list[Chunk]] = {}
        source_positions: dict[str, set[int]] = {}
        for position, chunk in enumerate(self._chunks):
            by_doc.setdefault(chunk.doc_id, []).append(chunk)
            source_positions.setdefault(chunk.source, set()).add(position)

        self._by_doc = by_doc
        # Frozen per source so `search()` can filter before the top-k cut. Built once here rather
        # than per call, because a source-filtered search would otherwise pay a full scan of the
        # chunk list to rebuild the same set every time.
        self._source_positions = {s: frozenset(p) for s, p in source_positions.items()}
        self._bm25 = Bm25Index.build((c.id, c.retrieval_text) for c in self._chunks)

    @classmethod
    def load(cls, sources: Sequence[CorpusName] = CORPUS_NAMES) -> "CorpusIndex":
        """Read every corpus's `chunks.jsonl`, or say which one is missing and how to fix it."""
        missing = [s for s in sources if not chunks_path(s).is_file()]
        if missing:
            raise ChunksNotBuiltError(
                f"no chunks for {', '.join(missing)} — chunks.jsonl is git-ignored, so a fresh "
                f"clone has none. Run `make chunk` to build them."
            )
        chunks: list[Chunk] = []
        for source in sources:
            chunks.extend(ChunkSet.load(source).chunks)
        return cls(chunks)

    # ---- introspection ----

    def __len__(self) -> int:
        return len(self._chunks)

    @property
    def chunks(self) -> Sequence[Chunk]:
        return self._chunks

    @property
    def bm25(self) -> Bm25Index:
        return self._bm25

    def chunk(self, chunk_id: str) -> Chunk | None:
        return self._by_id.get(chunk_id)

    # ---- the four primitives ----

    def list_documents(self, source: CorpusName | None = None, limit: int = 20) -> CorpusOverview:
        """What is in the corpus at all.

        The honest basis for answering *"what can you tell me about?"*, and the orienting move when
        a search comes back empty and the question is whether the topic is absent or the wording
        was wrong.
        """
        chunks = self._filtered(source)
        counts: dict[str, int] = {}
        summaries: dict[str, DocumentSummary] = {}
        for chunk in chunks:
            existing = summaries.get(chunk.doc_id)
            if existing is None:
                summaries[chunk.doc_id] = DocumentSummary(
                    doc_id=chunk.doc_id, source=chunk.source, title=chunk.title, chunks=1
                )
                counts[chunk.source] = counts.get(chunk.source, 0) + 1
            else:
                existing.chunks += 1

        listing = list(summaries.values())
        capped = max(0, limit)
        return CorpusOverview(
            total_documents=len(listing),
            total_chunks=len(chunks),
            documents_by_source=counts,
            documents=listing[:capped],
            truncated=len(listing) > capped,
        )

    def grep(
        self,
        pattern: str,
        source: CorpusName | None = None,
        ignore_case: bool = True,
        limit: int = 10,
    ) -> list[ChunkHit]:
        """Literal / regex match over chunk text, in corpus order.

        For exact strings BM25 is bad at: a defined term, an NCD number, a statutory phrase.
        Matching is against `Chunk.text` and not `retrieval_text`, so a pattern cannot accidentally
        hit the synthesised context header and report a match that is not in the document.

        Raises `re.error` for a malformed pattern; the tool wrapper turns that into a retry.
        """
        if len(pattern) > MAX_PATTERN_CHARS:
            raise ValueError(
                f"pattern is {len(pattern)} characters; keep it under {MAX_PATTERN_CHARS}. "
                f"Use search_corpus for a long natural-language query."
            )
        cap = min(max(limit, 0), MAX_HITS)
        if cap == 0:
            return []
        compiled = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
        hits: list[ChunkHit] = []
        for chunk in self._filtered(source):
            if compiled.search(chunk.text):
                hits.append(ChunkHit.of(chunk))
                if len(hits) >= cap:
                    break
        return hits

    def search(
        self,
        query: str,
        k: int,
        *,
        k1: float,
        b: float,
        source: CorpusName | None = None,
    ) -> list[ChunkHit]:
        """Ranked lexical retrieval — the general-purpose tool."""
        keep = self._source_positions.get(source) if source is not None else None
        if source is not None and keep is None:
            return []
        ranked = self._bm25.search(query, k=min(max(k, 0), MAX_HITS), k1=k1, b=b, keep=keep)
        return [ChunkHit.of(self._by_id[chunk_id], score) for chunk_id, score in ranked]

    def get_chunk(self, chunk_id: str, before: int = 1, after: int = 1) -> list[ChunkHit]:
        """One chunk plus its neighbours in the same document, in reading order.

        The recovery move for a hit that lands mid-definition. Chunks overlap by 320 characters
        (docs/chunking.md §4), so neighbours re-read some text — that is the design, not a defect:
        it is what guarantees a passage under 320 characters is wholly inside at least one chunk.

        Returns `[]` for an unknown id rather than raising, because the likeliest cause is a
        mistyped id and an empty result is a clearer signal to the agent than an exception.
        """
        chunk = self._by_id.get(chunk_id)
        if chunk is None:
            return []
        siblings = self._by_doc[chunk.doc_id]
        # By id, not `list.index(chunk)`: `Chunk` is a pydantic model, so `index()` would compare
        # every field of every sibling — including 1,200-character texts — to find a position the
        # id already identifies uniquely.
        at = next(i for i, c in enumerate(siblings) if c.id == chunk.id)
        start = max(0, at - max(before, 0))
        end = min(len(siblings), at + max(after, 0) + 1)
        return [ChunkHit.of(c) for c in siblings[start:end]]

    # ---- internals ----

    def _filtered(self, source: CorpusName | None) -> list[Chunk]:
        if source is None:
            return self._chunks
        return [c for c in self._chunks if c.source == source]


@lru_cache(maxsize=1)
def get_corpus_index() -> CorpusIndex:
    """The process-wide index, built once.

    Cached for the same reason `get_config()` is: the API lifespan, the eval runner, and the CLI
    all want the same object, and building it is ~200 ms and ~30 MB. Not called at import time
    anywhere — a missing `chunks.jsonl` must fail where a caller can report it, not while a module
    is loading.
    """
    return CorpusIndex.load()
