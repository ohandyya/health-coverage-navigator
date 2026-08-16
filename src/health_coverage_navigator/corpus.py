"""Shared vocabulary for the three text corpora.

The corpora that get chunked and embedded — `healthcare_gov`, `medicare_ncd`, `medicare_pubs` —
were deliberately given a common `id`/`source`/`url`/`title`/`bite`/`text` field vocabulary by
their three ingestion scripts so one chunker could span them (see data/README.md). This module is
where that contract lives in code, rather than being restated by every consumer.

Note what is *not* here: `exchange_puf` and `part_d_spuf`. Those are lossless columnar mirrors,
never chunked and never embedded, and they have no `text` field to chunk. `CorpusName` naming only
the three text corpora is the type-level statement of that rule.

Records are returned as plain dicts, not a model. The shared fields are the same across all three,
but the *useful* fields are not — a chunk's citation label needs `page` on one corpus,
`section_number` on another — and those per-source readers live in `chunking/strategies.py`, which
is per-source by construction. A model over the shared six would have to be bypassed for exactly
the fields that matter. `load_corpus()` enforces the shared contract instead.
"""

import json
from pathlib import Path
from typing import Literal

from health_coverage_navigator.paths import PROCESSED_DIR

CorpusName = Literal["healthcare_gov", "medicare_ncd", "medicare_pubs"]

CORPUS_NAMES: tuple[CorpusName, ...] = ("healthcare_gov", "medicare_ncd", "medicare_pubs")

#: Every record in every text corpus carries these. Chunking depends on all six.
SHARED_FIELDS = ("id", "source", "url", "title", "bite", "text")


def corpus_path(source: CorpusName) -> Path:
    """The normalized, app-ready corpus for one source (committed to git)."""
    return PROCESSED_DIR / source / "corpus.jsonl"


def chunks_path(source: CorpusName) -> Path:
    """The chunked corpus for one source (git-ignored; rebuilt by `make chunk`)."""
    return PROCESSED_DIR / source / "chunks.jsonl"


def chunks_meta_path(source: CorpusName) -> Path:
    """The chunk manifest for one source (committed — it is what makes the chunks
    reproducible without committing them)."""
    return PROCESSED_DIR / source / "chunks_meta.json"


def chunker_snapshots() -> dict[str, str]:
    """The `snapshot_id` of each corpus's committed chunk manifest.

    Pins a measurement to the corpus and chunk parameters it was taken under. Without it, a recall
    number that moved between two runs is ambiguous between "the retriever changed" and "the chunks
    changed" — precisely the comparison Phase 1b exists to make.

    Lives here rather than in `evals/runner.py` (where it started) because Phase 1b gave it a second
    caller with a stronger need: the vector store records these ids and **refuses to open** against
    a corpus that no longer matches, since a chunk id from a different snapshot is one the in-memory
    index cannot resolve.
    """
    snapshots: dict[str, str] = {}
    for source in CORPUS_NAMES:
        path = chunks_meta_path(source)
        if path.is_file():
            snapshots[source] = json.loads(path.read_text(encoding="utf-8"))["snapshot_id"]
    return snapshots


def load_corpus(source: CorpusName) -> list[dict]:
    """Read one corpus in file order, checking the invariants chunking relies on.

    File order is preserved deliberately: it is what makes chunk output deterministic, and the
    ingestion scripts already write in a sorted, stable order.
    """
    path = corpus_path(source)
    docs: list[dict] = []
    seen: set[str] = set()
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            rec = json.loads(line)
            missing = [field for field in SHARED_FIELDS if field not in rec]
            if missing:
                raise ValueError(f"{path}:{lineno}: record is missing {missing}")
            if rec["id"] in seen:
                raise ValueError(f"{path}:{lineno}: duplicate doc id {rec['id']!r}")
            seen.add(rec["id"])
            docs.append(rec)
    return docs


def load_doc_index() -> dict[str, dict]:
    """All three corpora keyed by doc id, for `GET /api/corpus/{doc_id}`.

    Flat across sources rather than nested by source, because a citation carries a bare `doc_id`
    and the API resolves it without being told which corpus it came from. That only works because
    doc ids are globally unique — a property `load_corpus()` does **not** enforce (it checks
    uniqueness only *within* a source), so this function checks it, and
    `tests/test_corpus.py::test_doc_ids_are_globally_unique` checks it against the live corpus.
    Without that, a future ingestion refresh could make the endpoint silently serve the wrong
    document, which for a citation drill-down is the worst failure available.

    Loading all 2,056 documents costs ~23 ms and ~12 MB, which is why the API does this once in
    its lifespan rather than building a byte-offset sidecar index. Revisit if the corpus grows an
    order of magnitude.
    """
    index: dict[str, dict] = {}
    for source in CORPUS_NAMES:
        for rec in load_corpus(source):
            doc_id = rec["id"]
            if doc_id in index:
                raise ValueError(
                    f"doc id {doc_id!r} appears in both {index[doc_id]['source']!r} "
                    f"and {source!r}; doc ids must be unique across all corpora"
                )
            index[doc_id] = rec
    return index
