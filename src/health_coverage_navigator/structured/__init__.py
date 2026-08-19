"""The Phase 1-c relational lane: DuckDB over the vendored Parquet mirrors.

Design, and the reasoning behind every decision here: [docs/relational-tool.md](
../../../docs/relational-tool.md).

Deliberately empty of re-exports, for the same reason as `vectors/__init__.py`: importing
`structured.catalog` — which the health endpoint does, to report whether the lane is built — must
not execute `store.py` and open a database.

| module | holds |
|---|---|
| `catalog.py` | what is on disk, whether it matches the committed `catalog.json`, and the |
|              | per-table correctness notes. Knows nothing about DuckDB |
| `models.py` | what the tools return, and therefore what the model reads |
| `store.py` | `StructuredStore` — the connection, the views, the macros, the SQL guard. |
|             | The only importer of duckdb |

Nothing in this package imports `pydantic_ai` or anything from `agent/`, so the store and its
guard are tested with no model, no key, and no network.
"""
