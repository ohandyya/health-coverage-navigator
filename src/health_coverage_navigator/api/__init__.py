"""The FastAPI application: the frozen HTTP contract and the routes that serve it.

Deliberately docstring-only — no re-exports, unlike `chunking/__init__.py`. `evals/models.py`
imports `SourceType` from `api/models.py`, and `api/routes/evals.py` imports from `evals`. If this
file re-exported `create_app`, then importing `health_coverage_navigator.api.models` would execute
`app.py` first, which reaches `routes/evals.py`, which reaches back into a half-initialised
`api.models`. An empty package module is what keeps that cycle impossible rather than merely
unlikely.
"""
