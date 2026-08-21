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
| `make check-all` | ruff, pyright, pytest, **and** the frontend gate (`tsc`, lint, Vitest, `types-check`). |
| `make dev` | Both servers (uvicorn + Vite). |
| `make serve` | Single process — FastAPI serving the built bundle. |
| `make types` / `make types-check` | OpenAPI → TypeScript codegen (`frontend/src/api/schema.d.ts`). |
| `make ui-install` / `ui-dev` / `ui-build` / `ui-test` | Frontend equivalents. |
| `make chunk` / `make chunk-check` | Rebuild `chunks.jsonl`; verify committed manifests still describe it. |
| `make embed` / `make embed-check` | Build the LanceDB vector store; verify it matches its manifest. **~$0.03, ~50 s**, idempotent. |
| `make smoke` | One real question through the live agent — checks the streaming path. Runs the lanes `config.yaml` turns on (`toolset`, `structured_tools`, `web_tools`), so with the web lane on it needs `TAVILY_API_KEY` and may spend a credit. **1 model call.** |
| `make smoke-abstain` | Same lanes, out-of-corpus: the agent must decline rather than invent sources. |
| `make smoke-web` | Force-opens the web lane and asks what only the web can answer — requires `web_search` fired and cited a real URL. **1 model call + 1 Tavily credit.** |
| `make eval` | Run the gold set through the agent → `data/eval_runs/`. **35 model calls**, ~90 s at the default `--concurrency 3`. |
| `make eval-retrieval` | Score BM25 retrieval alone — free, instant, no key. The `bm25_b`/`k1` sweep loop. |
| `make eval-retrieval-vector` | Score semantic retrieval alone — deterministic, ~30 embedding calls. **The lexical-vs-vector decision is taken here**, not on an agent A/B. |
| `make eval-lexical` / `make eval-vector` | The agent restricted to one toolset. **35 model calls each.** |
| `make eval-judge` | `eval` plus an LLM judge over answer correctness. **70 model calls.** |
| `make eval-stub` | Re-measure the Phase 0 canned answerer, the baseline real scores are read against. |
| `make scan` | Secrets / PII / licensing scan — run before publishing anything under `data/`. |

`make check-all` runs green on a fresh clone: the frontend gate **skips with a message** when
`frontend/node_modules` is absent rather than failing.

## Toolchain facts that have bitten before

- **`pytest-asyncio` runs in `auto` mode**, so an `async def test_` just runs with no decorator.
  Only `tests/test_evals.py` is async, because the answerer/grader seam and `run_gold_set` are; the
  agent tests deliberately stay synchronous and call `asyncio.run` at the boundary
  (`tests/conftest.py`), since they exercise a synchronous API.
- **Pyright's `include` covers `src`, `tests`, and `scripts`.** A type bug in a test file is a real
  typecheck failure, not something that only surfaces if the file happens to be open in an editor.
  It covered only `src` for weeks, and real bugs sat in `tests/` behind a green check.
- **The frontend requires Node 22** (pinned in `.nvmrc`; Vite 8 needs `^20.19.0 || >=22.12.0`).
  If nvm is installed, the `ui-*` targets load it automatically — so an interactive `node` can
  disagree with what `make` uses.
- **TypeScript is pinned to `~5.9`, not the 6.x `create-vite` scaffolds.** `openapi-typescript`
  peer-requires 5.x, and that codegen is the contract-enforcement mechanism between Python and
  TypeScript, so it wins. Revisit when `openapi-typescript` supports 6.
- **The test suite never reaches a model provider.** `tests/conftest.py` sets
  `ALLOW_MODEL_REQUESTS = False` suite-wide, so `make check-all` needs no `OPENAI_API_KEY` and
  costs nothing. Only the `smoke*` and `eval*` targets spend money, and they are outside
  `check-all`. That invariant is why the live check is `make smoke` rather than a pytest marker —
  see [`agent.md`](agent.md) §9.
- **The agent needs the OpenAI Responses API, not Chat Completions.** The `gpt-5.6-*` family
  returns a hard 400 for function tools on `/v1/chat/completions`; `agent/runtime.py` builds an
  `OpenAIResponsesModel`. See [`agent.md`](agent.md) §8.
- **Do not run the three agent evals back to back.** One run is ~210k tokens against a
  200k/minute allowance, so the third starts inside a rate limit: `eval-lexical`, `eval-vector`,
  `eval` in sequence put 16 of 35 questions into an ERR row and scored 0.400 where a paced re-run
  scored 0.800. Pause between them, or drop to `--concurrency 2`. **Read a run's error list before
  its score** — a TPM-starved run looks like a quality regression in every column at once.
- **`make eval`'s speed is capped by tokens per minute, not by concurrency.** One agent run is
  ~6,000 tokens, so the 35-question set is ~210k — more than a 200k TPM allowance permits inside one
  minute at *any* setting. `--concurrency 5` finished in 46 s and failed 16 questions on 429s,
  dropping recall@5 from 0.867 to 0.433; the default of 3 takes ~90 s and fails none. **Read a run's
  error list before trusting its score.**

Frontend stack rationale in full: [`frontend_plan.md`](frontend_plan.md) §1 and §7.
