"""Central filesystem layout for the repo.

Resolved once from this file's location so every module (ingestion scripts,
the eval loader, the future FastAPI app) agrees on where things live instead
of each re-deriving `Path(__file__).parent...`.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

DATA_DIR = REPO_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"

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
