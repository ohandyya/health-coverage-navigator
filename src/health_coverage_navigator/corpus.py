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
