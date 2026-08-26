---
name: upgrade-deps
description: Upgrade this repo's Python dependencies end to end — branch, prove the gates were green first, run the uv upgrade, triage what breaks, bump the pyproject floors, and commit. Use when asked to update or upgrade packages/dependencies, refresh uv.lock, check what's outdated, or bump a specific package.
---

# Upgrade the Python dependencies

The mechanics are three commands. **The work is everywhere else:** proving the gates were green
*before* you touched anything, telling a test failure apart from an application-code failure (they
have different permission rules — §7), and never reaching green by loosening a gate.

**Scope: Python only.** `pyproject.toml` and `uv.lock`. Do **not** upgrade `frontend/package.json`
— `make check-all` exercises the frontend gate, so a Python bump that breaks the contract still
gets caught, but npm is a separate blast radius and a separate request. If the user wants it, say
so and stop; do not fold it in.

**This skill is the standing permission to commit** that [CLAUDE.md](../../../CLAUDE.md)'s *Version
control* section otherwise withholds — one commit series, on this branch, for this upgrade. It is
**not** permission to push, to open a PR, or to commit anything else in the session.

## 1. Branch first

```bash
git status --porcelain          # must be empty — stop and ask if it is not
git fetch origin
git switch -c deps/$(date +%Y-%m-%d) origin/main
```

From **fresh `origin/main`**, not from `HEAD`. Every commit on main in this repo arrived as a
squashed PR, and a dependency bump stacked on in-flight work cannot be reviewed or reverted on its
own.

A dirty tree is a hard stop. Half the value of this skill is the before/after gate comparison, and
uncommitted work makes that comparison meaningless — you would not know whether a red gate came from
the upgrade or from what was already there.

## 2. Prove the baseline is green — before changing anything

```bash
make check-all      # ruff, format-check, pyright, pytest, tsc, oxlint, vitest, types-check
make smoke          # 1 real model call
```

`make test` is **already inside `check-all`** (`check-all: lint format-check typecheck test
ui-check`). Running it separately is duplicate work — skip it.

`check-all` reaches no provider (`ALLOW_MODEL_REQUESTS = False` suite-wide) and costs nothing.
`smoke` is the opposite: one live call, and with `agent.web_tools: true` in `config.yaml` it needs
`TAVILY_API_KEY` and may spend a Tavily credit. If a key is missing, **say which one and skip that
target** — do not disable the lane in `config.yaml` to get past it, and do not report a skipped
smoke as a passed one.

**If a gate is red here, stop and report it. Do not upgrade.** A pre-existing failure carried into
an upgrade is an hour spent blaming a package bump for something that was already broken. Record
what green looked like — you are going to compare against it in §5.

## 3. See what is actually outdated

Two commands, and they answer different questions. Run both — each one's blind spot is the other's
output.

```bash
uv tree --outdated --depth 1     # the 20 packages this repo declares
uv pip list --outdated           # all 137 installed, transitives included
```

| | Reads | Good for | Blind spot |
| --- | --- | --- | --- |
| `uv tree --outdated --depth 1` | the resolved dependency tree | **the work list** — these are the floors you edit in §8 and the names you pass to `--upgrade-package` | hides transitives entirely, which is where the scary majors hide |
| `uv pip list --outdated` | the installed `.venv` against the index | **the blast radius** — what a direct bump will actually drag in | flat and unattributed: it will not tell you *who* requires a package, and it lists upgrades that no constraint permits |

The second one is why this section is not a one-liner. `pydantic-ai 2.30 → 2.35` reads like a minor
bump in the tree view; `uv pip list --outdated` shows `anthropic 0.122.0 → 1.0.0` and
`mcp 1.29.0 → 2.1.1` sitting underneath it. **A transitive major makes its parent's bump
breaking-shaped**, whatever the parent's own version number says.

Two things not to misread in either output:

- **"Latest" is the index's latest, not what this project can take.** An upper cap, a
  `requires-python` floor, or a parent's own pin can hold a package back — so a row that never moves
  after `uv lock --upgrade` is usually constrained, not stuck. §4 covers which. This matters most for
  transitives: `mcp` goes to 2.x when `pydantic-ai` allows it and not before, and forcing it is not
  yours to do.
- **`uv pip list` describes `.venv`, so it lies if the venv is stale.** Run `uv sync` first if you
  are unsure it matches the lock.

After the upgrade, `git diff uv.lock` is the authoritative inventory of what actually moved.

Sort what you find into two tiers, because §10 commits them separately:

| Tier | Examples of the shape | Why it is its own tier |
| --- | --- | --- |
| **Routine** | `1.2.3 → 1.2.9`, `4.15.0 → 4.16.2` | Almost always inert. Bulk of the diff. |
| **Breaking-shaped** | major bump (`2.x → 3.x`), **or a minor on a `0.x`** (`0.7.27 → 0.8.0`) | Pre-1.0 minors are breaking by convention. These are the ones worth reverting alone. |

Before upgrading anything in the breaking-shaped tier, read its changelog — `WebSearch` for
"<package> <new version> changelog" or check its GitHub releases. You want the migration note in
hand *before* you are staring at a stack trace.

## 4. Run the upgrade

```bash
uv lock --upgrade --dry-run     # preview: what would move, nothing written
uv lock --upgrade               # re-resolve within pyproject's constraints
uv sync --exact                 # install it, and remove what the old resolution left behind
git diff --stat uv.lock
```

`--dry-run` first, always. It is free, and it turns §3's two lists into the resolver's actual
answer — including the transitives that only move because a parent moved.

`--exact` rather than a bare `uv sync`: a plain sync *adds* and *upgrades* but leaves extraneous
packages sitting in `.venv`. A dropped dependency then still imports on this machine and fails on a
fresh clone — green gates, broken repo. Use `--exact` and that cannot happen.

**Why the `pyproject.toml` edit comes later, not now.** Every runtime constraint in this repo is a
bare floor (`openai>=3.0.0`) with no upper cap, so `uv lock --upgrade` *already* resolves to the
newest release of everything, majors included. Raising a floor first changes nothing about what gets
installed — which is exactly why the floors are bookkeeping (§8), applied after the gates pass.

`pyproject.toml` is load-bearing in only three cases, and only these justify editing it *before*
re-locking:

1. **A real upper cap.** `[build-system] requires = ["uv_build>=0.11.6,<0.12.0"]` is the one that
   exists here. `uv lock --upgrade` cannot cross it; moving to 0.12 means editing the cap, and a
   build-backend bump is breaking-shaped by definition.
2. **`requires-python`.** A package whose new release drops 3.12 resolves to the old version
   silently, with no error and no note. If `uv tree --outdated` keeps offering an upgrade that
   `--upgrade` will not take, this is usually why.
3. A dependency's own metadata caps something transitively — read `uv lock --upgrade`'s resolution
   output, which names the constraint that pinned it back.

To move one package alone (bisecting in §6, or the user named one):

```bash
uv lock --upgrade-package pydantic-ai && uv sync
```

## 5. Re-run the same gates

```bash
make check-all
make smoke
```

Same two commands as §2, so the comparison is exact. Then the three checks `check-all` deliberately
excludes, when the upgrade touched their machinery:

| Run | When | Cost |
| --- | --- | --- |
| `make smoke-abstain` | `openai` or `pydantic-ai` moved — the grounding guardrail is a model behaviour | 1 call |
| `make smoke-web` | `tavily-python` or `httpx2` moved. Fixture-based tests pass with a broken live client; this is the only thing that catches it | 1 call + 1 credit |
| `make chunk-check` / `make embed-check` | `pypdf`, `beautifulsoup4`, `pyarrow`, `lancedb`, or `duckdb` moved — these read and write the committed data manifests | free / free |

`make scan` is **not** needed: an upgrade writes nothing under `data/`. If it somehow did, that is a
finding, not a step.

## 6. Bisect, do not guess

When `check-all` goes red after a bulk upgrade, find the package before you write a single fix:

```bash
git checkout uv.lock && uv sync --exact               # back to the green baseline
uv lock --upgrade-package <suspect> && uv sync --exact
make check-all
```

Start with the breaking-shaped tier from §3 and the failure's own evidence — a `ruff` code in the
output points at `ruff`, a `ValidationError` at `pydantic`. Do not walk all twenty alphabetically.

Known breakage shapes in *this* repo, with where to look:

| Package moved | What it breaks, and where |
| --- | --- |
| **ruff** (minor) | New lint rules fire, and `ruff format` output shifts — a formatting-only diff can touch every file. Both are tooling, not behaviour: fix directly (§7), and give the format churn its own commit so it never hides a real change. Nothing to bump in `.pre-commit-config.yaml` — its hooks are `uv run ruff`, so they follow the lock automatically. |
| **pyright** | Stricter inference surfaces new errors. Some are noise from a changed stub; some are **real bugs that were always there**. Read each one before deciding which. |
| **pydantic-ai** | The agent's toolset registration, output schema, and step limits (`agent/runtime.py`, [docs/agent.md](../../../docs/agent.md)). Also `ALLOW_MODEL_REQUESTS` — the suite's no-provider invariant lives in `pydantic_ai.models`. **Its transitive fan-out is the real risk**: it pulls `pydantic-ai-slim`, `pydantic-core`, `anthropic`, `mcp`, `logfire`, and `genai-prices`, several of which cross majors on their own schedule. Check `uv pip list --outdated` before and after. |
| **openai** | `agent/runtime.py` builds an `OpenAIResponsesModel` on purpose: the `gpt-5.6-*` family hard-400s on function tools via Chat Completions ([docs/development.md](../../../docs/development.md)). Never "simplify" that back to a chat model to fix a signature change. |
| **fastapi** / **pydantic** | The frozen API contract. If `openapi.json` renders differently, `types-check` fails inside `ui-check` and `schema.d.ts` is stale — regenerate with `make types` and follow [`sync-frontend`](../sync-frontend/SKILL.md). A serialization change that **reshapes** `citations`, `claims`, `trace`, or `abstained` is a contract break: stop and ask (§7). |
| **pytest** / **pytest-asyncio** | `asyncio_mode = "auto"` and `asyncio_default_fixture_loop_scope` are config keys that get renamed and deprecated across releases. A deprecation warning here is a real signal — fix the key, do not filter the warning. |
| **uvicorn** / **uvicorn[standard]** | Nothing offline covers the server. `make dev` binds `127.0.0.1` — if the flags changed, the Makefile target is what breaks, and only running it shows that. |

## 7. The permission line: tests you fix, application code you ask about

This is the rule the skill exists to enforce. **Fix directly** — no need to ask:

- Anything under `tests/` — assertions, fixtures, `conftest.py`.
- Lint and formatter churn from a `ruff` bump.
- Renamed tool configuration keys in `pyproject.toml` (`[tool.pytest.ini_options]`, `[tool.ruff]`).
- A deprecated import path swapped for its documented replacement, where behaviour is identical.

**Stop, explain, and ask** before editing anything under `src/` or `scripts/` that changes runtime
behaviour. Present it as: the failure, the upstream change that caused it, the fix you propose, and
what it changes for a user of the app. Then wait. Specifically always ask before:

- Touching the agent's tool definitions, prompt, output schema, or step limits.
- Any change to the API contract's *shape* — `source_type`, `citations`, `claims`, `trace`, or
  `abstained` as a first-class boolean. [CLAUDE.md](../../../CLAUDE.md): later phases add *values*
  to these fields, never reshape them.
- Changing anything in `config.yaml`, or adding a field to `Secrets`. Both are hard invariants in
  [docs/configuration.md](../../../docs/configuration.md), and an upgrade is not a licence to bend
  one.

**Never reach green by weakening a gate.** No `# type: ignore`, no `# noqa`, no `@pytest.mark.skip`,
no widening `Any`, no removing an assertion, no filtering a warning that is telling the truth. If
that is the only path, the honest outcomes are:

- **Pin the package back** and say why — `"ruff>=0.15.20,<0.16"` with a comment naming what blocks
  it and what would unblock it. This is a legitimate result, not a failure. It is still a decision:
  propose it, do not just do it.
- **Report the upgrade as blocked** and leave the rest of the batch upgraded.

Either way, the user hears about it in §11. A silenced gate does not.

## 8. Bump the floors in `pyproject.toml`

Only now that the gates are green. **No `uv` command bumps constraints in bulk** — `uv version`
reads and writes *this project's own* version, not its dependencies', so do not reach for it. Per
package, though, `uv add` will do the edit and the re-lock together:

```bash
uv add --bounds lower openai@latest                 # rewrites the line to >=3.3.1, re-locks
uv add --bounds lower --group dev ruff@latest        # dev-group packages need --group
```

`--bounds lower` is what produces `>=X` with no upper cap, matching every line already in this file;
the default would write something else. Prefer this over hand-editing — it cannot typo a version.
Then **read the diff**: `uv add` rewrites the whole dependency line, so confirm it changed the
version and nothing else (a dropped extra like `uvicorn[standard]`, a lost marker, or a reordered
list is a real risk).

Hand-editing is the fallback when `uv add` would disturb a line you want left alone — a cap, an
extra, an environment marker:

```toml
-    "openai>=3.0.0",
+    "openai>=3.3.1",
```

Read the new versions off `uv.lock` (or `uv tree --depth 1`), not off the `--outdated` output from
§3 — the resolver may have chosen something older than the index's latest. Direct dependencies only:
transitive versions belong to the lock, and pinning one in `pyproject.toml` makes this repo
responsible for a constraint it does not own.

Then confirm the edit changed no resolution:

```bash
uv lock --check      # must report the lock is up to date
```

If it wants to re-resolve, a floor you wrote is above what is locked — you mistyped a version.

## 9. Update a doc only if the upgrade invalidated one

Do not write a changelog. Do not touch [docs/progress.md](../../../docs/progress.md) — a dependency
bump is not a phase, and progress.md records what is *built*.

Update only when the upgrade made an existing sentence false. The likely ones live in
[docs/development.md](../../../docs/development.md) → *Toolchain facts that have bitten before*:
the `pytest-asyncio` auto-mode note, the Responses-API note, a pin whose rationale you just changed.
A stale fact in that section is worse than no fact, because it is written to be trusted.

The glossary rule does not apply here — package upgrades introduce no domain terms.

## 10. Commit

```bash
make scan-staged        # only if anything under data/ is somehow staged — it should not be
git add pyproject.toml uv.lock
git commit
```

**Stage explicit paths. Never `git add -A` or `git add .`** — `docs/human_worklog.md` is the
author's file, and a `git add -A` has already swept it into an unrelated commit
([CLAUDE.md](../../../CLAUDE.md), and the incident is in progress.md).

Split the series so a revert can be surgical:

1. **Routine bumps** — one commit for the whole patch/minor tier.
2. **One commit per breaking-shaped bump**, with the code or test changes it forced, in the same
   commit. This is the point of the split: `git revert` on that one commit undoes the package *and*
   its fallout together.
3. **Formatter churn from a `ruff` bump on its own**, never mixed with a real change.

Message body says what moved and what it cost — the diff already shows the version numbers, so spend
the prose on the parts a reader cannot see:

```
chore(deps): upgrade tavily-python 0.7.27 -> 0.8.0

`search()` now returns `results` as a model rather than a dict, so
`web/client.py` reads attributes instead of keys. Fixtures in
tests/fixtures/tavily/ regenerated to the new shape.

Gates: check-all green, smoke-web green (tool fired, real URL cited).

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
```

**Then stop.** No `git push`, no `gh pr create` — the user opens the PR.

## 11. Report

Short. A clean upgrade of eight patch releases is worth five lines.

1. **Verdict**, one line: what upgraded, and whether the gates are green.
2. **The table** — package, old → new, and for anything breaking-shaped, what it cost.
3. **Anything held back**, with the reason and what would unblock it. Never omit this to make the
   result look tidy.
4. **Anything you asked permission for** and are still waiting on.
5. **Which gates you actually ran**, naming any you skipped and why (a missing key, most likely).
   A skipped `smoke` is not a passed `smoke`.
6. **How to push the branch**, with the real branch name substituted — never a placeholder:

   ```bash
   git push -u origin deps/2026-08-26
   ```

   Still do not run it yourself (§10); the user pushes and opens the PR. **The `-u` is
   load-bearing.** §1 created the branch with `git switch -c … origin/main`, which sets its
   upstream to **`origin/main`** — not to a branch of its own name. A bare `git push` is therefore
   refused for the name mismatch, and the first fix Git suggests in that error,
   `git push origin HEAD:main`, would land the whole upgrade series directly on `main`, bypassing
   the squashed-PR flow every commit in this repo has gone through. Say so when you hand over the
   command. `-u` re-points tracking at the new remote branch, so the next plain `git push` is
   correct.

## When to abandon

Sunk cost is the failure mode here. Say so and stop when: a single package needs more than a couple
of rounds of real source changes; a bump would reshape the frozen API contract; or the fix requires
a design decision that belongs to a phase in [docs/plan.md](../../../docs/plan.md).

Abandoning is cheap and correct — commit the bumps that *did* work, leave the one that did not,
and hand the user the changelog link and the one question that decides it. Nine of ten packages
upgraded with one clearly-stated blocker is a good outcome. A green gate bought with a `# noqa` is
not.
