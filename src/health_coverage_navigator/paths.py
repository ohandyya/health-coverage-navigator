"""Central filesystem layout for the repo.

Resolved once from this file's location so every module (ingestion scripts,
the eval loader, the future FastAPI app) agrees on where things live instead
of each re-deriving `Path(__file__).parent...`.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Non-secret, committed configuration (`config.py`). Committed on purpose: it is an *input* to
#: every eval score, so it has to be reviewable and pinnable to a git SHA. Secrets live in `.env`,
#: which is git-ignored — that split is the whole design, see `config.py`.
CONFIG_PATH = REPO_ROOT / "config.yaml"

DATA_DIR = REPO_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"

#: The LanceDB vector store (Phase 1b). Git-ignored — it is ~41 MB of float32 derived from the
#: committed corpora plus a paid embedding call, and `make embed` rebuilds it. What *is* committed
#: is the manifest below, which is what makes the ignored directory reproducible and what lets
#: `VectorIndex.open` refuse a store built against different chunks. Note the path: an earlier
#: draft of docs/lancedb.md used `data/lancedb` while .gitignore only carried `.lancedb/`, which
#: matches neither — the two are kept in step here on purpose.
VECTOR_STORE_DIR = DATA_DIR / "lancedb"

#: The vector manifest (committed). Cross-corpus rather than per-source — one table spans all
#: three text corpora so a single query ranks against one embedding space — which is why it sits
#: beside the per-source directories rather than inside one.
VECTOR_META_PATH = PROCESSED_DIR / "vectors_meta.json"

EVALS_DIR = REPO_ROOT / "evals"
GOLD_SET_PATH = EVALS_DIR / "gold" / "questions.yaml"

#: Eval run outputs, one JSON per run. Git-ignored: a run is a measurement, not a source of
#: truth, and the runner rebuilds one on demand (docs/frontend_plan.md §5.2).
EVAL_RUNS_DIR = DATA_DIR / "eval_runs"

FRONTEND_DIR = REPO_ROOT / "frontend"

#: The compiled single-page app. Git-ignored, and absent during `make dev` (Vite serves the UI
#: itself) and on a fresh clone — so the FastAPI app must degrade gracefully rather than assume
#: this exists. See `api/app.py`.
FRONTEND_DIST = FRONTEND_DIR / "dist"
