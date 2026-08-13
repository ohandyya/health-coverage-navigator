"""Corpus -> chunks, plus the manifest that makes the (git-ignored) output reproducible.

`build_chunks()` is a **pure function** of the corpus file and the parameters: same inputs, same
bytes out. That is not decoration — it is what lets `chunks.jsonl` stay out of git while remaining
a trustworthy artifact, and what lets an eval score name the chunk snapshot it was measured
against. Four rules keep it true, and breaking any of them breaks the manifest:

  * iterate the corpus in file order; never let set/dict iteration order reach the output,
  * no clock, no randomness, no filesystem ordering inside the build,
  * every regex precompiled at module scope (see `splitter.py`, `strategies.py`),
  * every tunable flows from one `ChunkParams`, never read ad hoc.

The manifest (`chunks_meta.json`) *is* committed. It records the input hash, the parameters, and
the output hash, so a reviewer can tell whether a working tree's chunks match the ones an eval ran
against, and `--check` turns that into a build failure. Its `snapshot_id` is the pin Phase 1b will
tag a LanceDB dataset version with (docs/lancedb.md).
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from health_coverage_navigator.chunking.models import Chunk
from health_coverage_navigator.chunking.params import CHUNKER_VERSION, DEFAULT_PARAMS, ChunkParams
from health_coverage_navigator.chunking.splitter import split_span
from health_coverage_navigator.chunking.strategies import build_context, doc_metadata, plan_sections
from health_coverage_navigator.corpus import (
    CorpusName,
    chunks_meta_path,
    chunks_path,
    corpus_path,
    load_corpus,
)
from health_coverage_navigator.paths import REPO_ROOT

TOOL = "health-coverage-navigator/0.1 (chunking)"

STRATEGY_NAMES: dict[CorpusName, str] = {
    "healthcare_gov": "prose_ladder",
    "medicare_ncd": "ncd_headings",
    "medicare_pubs": "page_ladder",
}


@dataclass(frozen=True)
class BuildResult:
    """Chunks for one source, plus what the manifest needs to describe the run."""

    source: CorpusName
    chunks: list[Chunk]
    params: ChunkParams
    doc_count: int
    skipped_ids: list[str]

    def serialize(self) -> str:
        """The exact bytes of `chunks.jsonl`. Hashed for the manifest, so it must be canonical."""
        return "".join(json.dumps(c.model_dump(), ensure_ascii=False) + "\n" for c in self.chunks)


def build_chunks(source: CorpusName, params: ChunkParams = DEFAULT_PARAMS) -> BuildResult:
    """Chunk one corpus in memory. Touches no output file."""
    docs = load_corpus(source)
    ctx = build_context(source, docs)

    chunks: list[Chunk] = []
    skipped: list[str] = []
    for doc in docs:
        text = doc["text"]
        if len(" ".join(text.split())) < params.min_doc_chars:
            skipped.append(doc["id"])
            continue

        meta = doc_metadata(source, doc)
        ordinal = 0
        for section in plan_sections(source, doc, ctx):
            for start, end in split_span(text, params, section.start, section.end):
                chunks.append(
                    Chunk(
                        id=f"{source}:{doc['id']}#{ordinal:03d}",
                        doc_id=doc["id"],
                        source=source,
                        ordinal=ordinal,
                        char_start=start,
                        char_end=end,
                        text=text[start:end],
                        n_chars=end - start,
                        title=doc["title"],
                        url=doc["url"],
                        heading=section.heading,
                        **meta,
                    )
                )
                ordinal += 1

    return BuildResult(source, chunks, params, len(docs), skipped)


def build_manifest(result: BuildResult) -> dict:
    """The committed sidecar. Counts and hashes only — deliberately no prose.

    Explanatory text in a file under `data/` is what tripped the licensing scanner's blocking
    `CDT` marker during the part_d_spuf work; the reasoning lives in docs/chunking.md, outside the
    scanned tree.
    """
    source = result.source
    payload = result.serialize()
    in_path = corpus_path(source)
    in_bytes = in_path.read_bytes()
    input_sha = hashlib.sha256(in_bytes).hexdigest()
    params_sha = result.params.fingerprint()
    sizes = sorted(c.n_chars for c in result.chunks)

    return {
        "chunked_at": datetime.now(UTC).isoformat(),
        "source": source,
        "tool": TOOL,
        "chunker_version": CHUNKER_VERSION,
        "strategy": STRATEGY_NAMES[source],
        "params": result.params.model_dump(),
        "params_sha256": params_sha,
        "input": {
            "path": _rel(in_path),
            "sha256": input_sha,
            "bytes": len(in_bytes),
            "doc_count": result.doc_count,
        },
        "output": {
            "path": _rel(chunks_path(source)),
            "sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
            "chunk_count": len(result.chunks),
        },
        "docs_skipped": {
            "count": len(result.skipped_ids),
            "reason": "whitespace-normalized text shorter than min_doc_chars",
            "ids": result.skipped_ids,
        },
        "chunk_chars": {
            "min": sizes[0] if sizes else 0,
            "median": _pct(sizes, 0.50),
            "p90": _pct(sizes, 0.90),
            "max": sizes[-1] if sizes else 0,
            "total": sum(sizes),
        },
        "snapshot_id": snapshot_id(source, input_sha, params_sha),
    }


def snapshot_id(source: CorpusName, input_sha: str, params_sha: str) -> str:
    """A short, stable name for "this corpus, chunked these ways".

    Deliberately excludes the output hash so it can be computed *before* chunking — an eval run
    can name the snapshot it wants, and `--check` can verify it was actually produced.
    """
    digest = hashlib.sha256(f"{input_sha}{params_sha}{CHUNKER_VERSION}".encode()).hexdigest()
    return f"{source}@{digest[:12]}"


def write_chunks(result: BuildResult) -> dict:
    """Write `chunks.jsonl` and refresh the manifest. Returns the manifest that was written."""
    out_path = chunks_path(result.source)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # .part + rename, per the part_d_spuf lesson: a crash must never leave a truncated file that a
    # later run's manifest vouches for.
    tmp = out_path.with_suffix(".jsonl.part")
    tmp.write_text(result.serialize(), encoding="utf-8")
    tmp.replace(out_path)

    manifest = build_manifest(result)
    meta_path = chunks_meta_path(result.source)
    if _manifest_changed(meta_path, manifest):
        meta_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def _manifest_changed(meta_path: Path, manifest: dict) -> bool:
    """True unless the on-disk manifest matches apart from its timestamp.

    A re-run that changes nothing must not produce a git diff — a "re-runnable" step that dirties
    the tree every time is one nobody re-runs.
    """
    if not meta_path.exists():
        return True
    try:
        existing = json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return True
    return _without_timestamp(existing) != _without_timestamp(manifest)


def _without_timestamp(manifest: dict) -> dict:
    return {k: v for k, v in manifest.items() if k != "chunked_at"}


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _pct(sizes: list[int], q: float) -> int:
    if not sizes:
        return 0
    return sizes[min(len(sizes) - 1, int(len(sizes) * q))]


def _summarize(results: list[BuildResult]) -> str:
    lines = [f"{'source':<16}{'docs':>7}{'chunks':>9}{'median':>9}{'p90':>7}{'max':>7}{'skip':>7}"]
    for r in results:
        sizes = sorted(c.n_chars for c in r.chunks)
        lines.append(
            f"{r.source:<16}{r.doc_count:>7}{len(r.chunks):>9}"
            f"{_pct(sizes, 0.50):>9}{_pct(sizes, 0.90):>7}{sizes[-1] if sizes else 0:>7}"
            f"{len(r.skipped_ids):>7}"
        )
    total = sum(len(r.chunks) for r in results)
    lines.append(f"{'total':<16}{sum(r.doc_count for r in results):>7}{total:>9}")
    return "\n".join(lines)
