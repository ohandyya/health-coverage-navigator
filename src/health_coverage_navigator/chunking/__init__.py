"""Chunk the three text corpora into retrievable, citable units.

`data/processed/<source>/corpus.jsonl` -> `data/processed/<source>/chunks.jsonl`. Pure, offline,
deterministic: no network, no clock in the output, same bytes for the same inputs.

Run it with `make chunk` (or `python -m health_coverage_navigator.chunking`); import
`build_chunks` to get the same chunks in memory without touching disk, which is how the tests and
the Phase 1a retriever consume it.
"""

from health_coverage_navigator.chunking.models import Chunk, ChunkSet
from health_coverage_navigator.chunking.params import DEFAULT_PARAMS, ChunkParams
from health_coverage_navigator.chunking.pipeline import build_chunks, write_chunks

__all__ = [
    "DEFAULT_PARAMS",
    "Chunk",
    "ChunkParams",
    "ChunkSet",
    "build_chunks",
    "write_chunks",
]
