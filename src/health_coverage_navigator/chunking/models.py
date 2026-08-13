"""The chunk record: a verbatim slice of one corpus document, with its provenance attached.

Two properties of this schema are load-bearing and should not be traded away for convenience:

**`text` is verbatim.** `parent["text"][char_start:char_end] == chunk.text`, always. That is what
lets tests prove no content was dropped or duplicated, and it is the pre-plumbing for Phase 4's
per-claim highlighting, which needs to point at a span of the source document. Nothing derived —
no title prefix, no normalization — is ever baked into it. The derived forms are properties
(`retrieval_text`, `context_header`, `citation_label`), computed on demand and never stored, so a
change to a citation format is not a re-chunk of the whole corpus.

**`doc_id` is stored, not merely parseable out of `id`.** Mapping a retrieved chunk back to its
parent document is the eval runner's primary operation for recall@k (docs/progress.md), and making
it re-parse a string to do its main job is a defect waiting to happen. The parse is validated
redundancy, not the mechanism.

Per-source fields (`page`, `section_number`, ...) are flat and optional rather than a `meta: dict`.
docs/lancedb.md names metadata filtering (`.where("plan_year = 2026")`) as a reason LanceDB was
chosen, and a dict lands in Arrow as an unfilterable blob; five optional columns are not a schema
problem. `_check_shape` enforces that each one only appears on the source it belongs to.
"""

import json
from pathlib import Path
from typing import Self

from pydantic import BaseModel, model_validator

from health_coverage_navigator.corpus import CorpusName, chunks_path

#: Rough characters-per-token for cost estimates only. See params.py for why no tokenizer.
CHARS_PER_TOKEN = 4


class Chunk(BaseModel):
    id: str
    doc_id: str
    source: CorpusName
    ordinal: int
    char_start: int
    char_end: int
    text: str
    n_chars: int
    title: str
    url: str
    heading: str | None = None
    page: int | None = None
    section_number: str | None = None
    plan_year: int | None = None
    effective_date: str | None = None

    @model_validator(mode="after")
    def _check_shape(self) -> Self:
        if self.id != f"{self.source}:{self.doc_id}#{self.ordinal:03d}":
            raise ValueError(f"{self.id}: id does not match source/doc_id/ordinal")
        if not 0 <= self.ordinal < 1000:
            raise ValueError(f"{self.id}: ordinal {self.ordinal} outside the 3-digit id format")
        if self.char_end <= self.char_start:
            raise ValueError(
                f"{self.id}: empty or inverted span {self.char_start}..{self.char_end}"
            )
        if self.n_chars != self.char_end - self.char_start or self.n_chars != len(self.text):
            raise ValueError(f"{self.id}: n_chars disagrees with the span or the text")
        # Guarantees docs/lancedb.md's `if chunk["text"].strip()` guard can never silently drop a
        # row: a chunk that would fail it cannot be constructed in the first place.
        if not self.text.strip():
            raise ValueError(f"{self.id}: text is blank")
        if self.text != self.text.strip():
            raise ValueError(f"{self.id}: text has leading or trailing whitespace")
        if self.source != "medicare_pubs" and (self.page is not None or self.plan_year is not None):
            raise ValueError(f"{self.id}: page/plan_year are medicare_pubs-only")
        if self.source != "medicare_ncd" and (
            self.section_number is not None or self.effective_date is not None
        ):
            raise ValueError(f"{self.id}: section_number/effective_date are medicare_ncd-only")
        if self.source == "healthcare_gov" and self.heading is not None:
            raise ValueError(f"{self.id}: healthcare_gov text carries no recoverable headings")
        return self

    @property
    def approx_tokens(self) -> int:
        return self.n_chars // CHARS_PER_TOKEN

    @property
    def context_header(self) -> str:
        """The breadcrumb prepended at index/embed time — never stored, never cited.

        A bare mid-document slice loses the one thing that says what it is about: a glossary
        chunk's title *is* the term being defined, and an NCD chunk's heading says whether the
        text is a coverage rule or a cross-reference. ` > ` matches the separator medicare_pubs
        already uses in its own `section` field.
        """
        if self.source == "medicare_ncd":
            parts = [f"NCD {self.section_number}", self.title, self.heading]
        elif self.source == "medicare_pubs":
            parts = [self.title, self.heading, f"p. {self.page}"]
        else:
            parts = [self.title, f"healthcare.gov{self.url}"]
        return " > ".join(p for p in parts if p)

    @property
    def retrieval_text(self) -> str:
        """What gets indexed (Phase 1a BM25) and embedded (Phase 1b) — not what gets cited."""
        return f"{self.context_header}\n{self.text}"

    @property
    def citation_label(self) -> str:
        """The human-readable string an answer cites this chunk as."""
        if self.source == "medicare_ncd":
            label = f"NCD {self.section_number}, {self.title}"
            return f"{label} — {self.heading}" if self.heading else label
        if self.source == "medicare_pubs":
            return f"{self.title}, p. {self.page}"
        return f"{self.title} (healthcare.gov{self.url})"


class ChunkSet(BaseModel):
    chunks: list[Chunk]

    def by_source(self, source: CorpusName) -> list[Chunk]:
        return [c for c in self.chunks if c.source == source]

    def by_doc(self, doc_id: str) -> list[Chunk]:
        return [c for c in self.chunks if c.doc_id == doc_id]

    def for_doc_ids(self, doc_ids: list[str]) -> list[Chunk]:
        """Every chunk belonging to any of `doc_ids` — the recall@k mapping the gold set needs."""
        wanted = set(doc_ids)
        return [c for c in self.chunks if c.doc_id in wanted]

    def doc_ids(self) -> list[str]:
        """Parent doc ids in first-seen order (chunk order is corpus file order)."""
        seen: dict[str, None] = {}
        for c in self.chunks:
            seen.setdefault(c.doc_id, None)
        return list(seen)

    @classmethod
    def load(cls, source: CorpusName, path: Path | None = None) -> "ChunkSet":
        target = path or chunks_path(source)
        with open(target, encoding="utf-8") as f:
            return cls(chunks=[Chunk.model_validate(json.loads(line)) for line in f])
