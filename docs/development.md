# Development — commands, gates, and toolchain

Everything needed to run, test, and check this repo. [`CLAUDE.md`](../CLAUDE.md) carries only the
one-line summary and points here; this file is the detail.

A `uv`-managed Python project: src layout, package `health_coverage_navigator`, Python 3.12.

## Python

| Command | What it does |
|---|---|
| `uv sync` | Create/update `.venv` and install from `pyproject.toml` / `uv.lock`. |
| `uv run health-coverage-navigator` | Run the CLI entry point (`src/health_coverage_navigator/__init__.py:main`). |
| `uv add <package>` | Add a runtime dependency (updates `pyproject.toml` + `uv.lock`). |
| `uv add --dev <package>` | Add a dev-only dependency (pytest, ruff, …). |
| `uv run pytest` | Run the suite in `tests/` (configured under `[tool.pytest.ini_options]`). |
| `uv run pytest path/to/test.py::test_name` | Run a single test. |
| `uv run ruff check .` / `uv run ruff format .` | Lint / format (configured under `[tool.ruff]`). |

## Make targets

A `Makefile` wraps every gate — `make help` lists them all.

| Target | What it does |
|---|---|
| `make check-all` | ruff, pyright, pytest, **and** the frontend gate (`tsc`, lint, Vitest). |
| `make dev` | Both servers (uvicorn + Vite). |
| `make serve` | Single process — FastAPI serving the built bundle. |
| `make types` / `make types-check` | OpenAPI → TypeScript codegen (`frontend/src/api/schema.d.ts`). |
| `make ui-install` / `ui-dev` / `ui-build` / `ui-test` | Frontend equivalents. |
| `make chunk` / `make chunk-check` | Rebuild `chunks.jsonl`; verify committed manifests still describe it. |
| `make eval` | Run the gold set → `data/eval_runs/`. |
| `make scan` | Secrets / PII / licensing scan — run before publishing anything under `data/`. |

`make check-all` runs green on a fresh clone: the frontend gate **skips with a message** when
`frontend/node_modules` is absent rather than failing.

## Toolchain facts that have bitten before

- **Pyright's `include` covers both `src` and `tests`.** A type bug in a test file is a real
  typecheck failure, not something that only surfaces if the file happens to be open in an editor.
  It covered only `src` for weeks, and real bugs sat in `tests/` behind a green check.
- **The frontend requires Node 22** (pinned in `.nvmrc`; Vite 8 needs `^20.19.0 || >=22.12.0`).
  If nvm is installed, the `ui-*` targets load it automatically — so an interactive `node` can
  disagree with what `make` uses.
- **TypeScript is pinned to `~5.9`, not the 6.x `create-vite` scaffolds.** `openapi-typescript`
  peer-requires 5.x, and that codegen is the contract-enforcement mechanism between Python and
  TypeScript, so it wins. Revisit when `openapi-typescript` supports 6.

Frontend stack rationale in full: [`frontend_plan.md`](frontend_plan.md) §1 and §7.
