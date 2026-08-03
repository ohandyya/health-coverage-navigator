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
