# Progress

Status of the build against [plan.md](plan.md) and [frontend_plan.md](frontend_plan.md).
**Those docs say what to build; this one says what is built.** They never record completion;
this one never records design.

**How to read this:** the *Current state* block below is the one-read answer to "where am I".
The *Log* is append-only, newest first — read down as far as you need for the reasoning behind
the current state. A log entry is true as of its date and is never edited.

**What does not belong here:** anything git history or the code already says. No file
inventories, no restating what a script does. The value of this file is entirely in what cannot
be reconstructed — decisions, rejected alternatives, dead ends, and where work stopped
mid-stream.

---

## Current state

*Updated 2026-08-14.*

- **Phase:** **0 is complete, both halves**, and **Phase 1a's groundwork is in but the agent is
  not.** Phase 0 delivered five bulk sources, the `make scan` guardrail, 35 gold questions, 6,722
  chunks, the frozen contract, a FastAPI app serving canned answers over JSON and SSE, an eval
  runner with HTTP-triggered runs, and a React UI rendering all of it. Since then: `pydantic-ai`
  and `pydantic-settings` are installed, configuration is split into `settings.py` (secrets, from
  `.env`) and `config.py` (everything else, from a committed `config.yaml`), and `ChunkParams` has
  been consolidated into the latter. **No code in this repo calls an LLM yet.**
- **Next up:** **the Phase 1a agent itself** —
  [plan.md](plan.md#phase-1-a--rag-without-a-vector-database-full-text-search). The seams are cut
  and the config is now there to hang it on: replace `api/stub.py`'s `stub_answer()` with a
  PydanticAI agent behind a single `retrieve` tool over `data/processed/*/chunks.jsonl` —
  **stdlib BM25, no database of any kind** (settled 2026-08-14; see the log) — pass the
  real answerer to `evals/runner.py` instead of `stub_answer_fn`, and set `stub=False` in
  `create_app()` so the UI's stub banner and the runs' `runner: "stub"` label both go away on their
  own. The chat UI, the streaming plumbing, the eval dashboard, and the contract should not need to
  change — if they do, that is a signal worth stopping on. Two things to carry in:
  **`Agent("openai:...")` will not see `Secrets`** (pass `OpenAIProvider(api_key=...)` explicitly —
  see the log), and the eval run record should start carrying `Config.fingerprint()` and the
  resolved model, since the default model is a floating alias.
- **Open questions:**
  - **`make chunk`'s `--max-chars` / `--overlap-chars` flags override `config.yaml` and are recorded
    nowhere.** The same invisible-override hole the config split just closed, on a smaller scale.
    Either drop the flags or have the manifest record that an override was used.
  - **nvm is installed at `~/.nvm` but not wired into the shell profile**, so an interactive
    `node`/`npm` still resolves to the old v20.7.0 while `make ui-*` (which sources nvm itself)
    gets 22. Adding `export NVM_DIR="$HOME/.nvm"; [ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"`
    to `~/.zshrc` fixes it; deliberately not done, since it is a change to the machine rather than
    the repo.
  - `plan.md`'s Exchange PUF paragraph still describes two tables as the ones that matter; the
    build fetches three (Service Area as well, for the ZIP→plan mapping). Minor, and the reason
    is recorded in [exchange_puf_data.md](exchange_puf_data.md) — correct it if the paragraph is
    ever touched for another reason.
  - `part_d_spuf` is a Phase 5 source that landed during Phase 0, on request. Nothing consumes
    it yet and nothing should until Phase 3/5 — but it now exists, so a later phase should not
    re-plan the ingestion, only the modelling layer on top of the mirror.

### Phase 0 checklist

Backend (plan.md, Phase 0):

- [x] HealthCare.gov ingestion — 747 docs (English only; was 803 before the duplicate-id fix)
- [x] Medicare publications ingestion — 83 pubs / 964 pages
- [x] Medicare NCD ingestion — 345 determinations
- [x] Exchange PUF ingestion — 3 tables, plan year 2026 (structured mirror, not a text corpus)
- [x] Part D SPUF ingestion — 7 files, 2026Q2 (structured mirror; Phase 5 source, built early)
- [x] Public-repo guardrail enforced in code — `make scan` (secrets / PII / licensing)
- [x] Chunking step → 6,722 chunks across the three text corpora (`make chunk`)
- [x] Gold eval set, 35 questions (30 in-corpus + 5 abstention; question → expected source-type
  → expected answer)
- [x] Eval dataset loader / schema
- [x] Test tooling (pytest, configured in `pyproject.toml`, wired into `make check-all`)

Frontend (frontend_plan.md, Phase F0):

- [x] `api/models.py` — the frozen contract models, including the SSE union
- [x] `api/app.py` — `/api/health` + stubbed `POST /api/chat`, static mount, SPA fallback
- [x] `frontend/` scaffold (Vite + React + TS + Tailwind v4 + shadcn), `/api` proxy
- [x] `make types` → `frontend/src/api/schema.d.ts` (committed), plus `make types-check`
- [x] Chat page rendering the stub end to end — badges, citation cards, trace panel, abstention
- [x] Eval endpoints over the gold set, plus HTTP-triggered runs with SSE progress

Beyond the F0 list, because implementation made them the cheaper order:

- [x] `POST /api/chat/stream` + `stream.ts` + its Vitest suite (scheduled for F1; see the log)
- [x] `evals/runner.py` with a pluggable answerer — the Phase 1a swap point
- [x] `GET /api/corpus/{doc_id}` citation drill-down against the real corpus

---

## Log

### 2026-08-14 — configuration splits in two, and the Phase 1a dependencies land

**Did:** added `pydantic-ai` and `pydantic-settings`, then split configuration along the line the
first one exposed: secrets from the environment (`settings.py`), everything else from a committed
`config.yaml` (`config.py`). `ChunkParams` moved into the new config module as part of that. No
agent yet — this is the wiring the agent will sit on.

**Decided:** **the axis is committed-reproducible-input vs git-ignored credential, not
secret vs non-secret.** That reframing is what settles the env-var question, which is otherwise a
matter of taste: a secret *must* come from the environment because it cannot be committed, and
anything that changes an eval result must *not*, because an env var is invisible to git — a run
configured by an unrecorded `AGENT_MODEL=...` cannot be reproduced from the repo, and the score is
worth less for it. So `Secrets` inherits `BaseSettings` and the config models are plain
`BaseModel`s, which have no environment source to accidentally re-enable.

**Decided:** **two accessors, `get_config()` and `get_secrets()`, never composed into one object.**
A chunking run or a retrieval test needs `top_k` and no credentials; composing them would make
every such caller fail when `OPENAI_API_KEY` is unset. Verified by running `get_config()` with the
variable cleared.

**Decided:** **no field defaults in `config.py`; `config.yaml` is mandatory.** A default in Python
beside a value in YAML is two sources of truth that drift silently. The file is committed so it is
always present, and a missing key is an error that names the key. `extra="forbid"` makes a typo'd
key fail at load rather than being ignored.

**Decided:** **`ChunkParams` moved rather than being imported.** `chunking/__init__.py` imports
`pipeline`, so `config.py` importing `chunking.params` is a cycle — the same shape as the
`api/__init__.py` note in the F0 entry below. `CHUNKER_VERSION` deliberately stayed behind: it
describes what the *code* does, not how it is tuned, so it must not live in a file users are
invited to edit.

**Decided:** the model default is `openai:gpt-5.6-luna`, **a floating alias, knowingly.** OpenAI
publishes no dated snapshot for that family (checked against the live account — only the three bare
aliases exist), so a rerun can differ from its baseline. `Config.fingerprint()` exists to mitigate
it: the eval run record should carry it and the resolved model. `tests/test_config.py` keeps the
dated-snapshot rule for every family that *does* publish one, with these three as named exceptions.

**Decided:** `min_length=1` on the API key, and `.env.example` ships it **blank**. A non-empty
placeholder passes a "is it set" check, boots cleanly, and fails with a 401 on the first question —
which is precisely the deferred failure `pydantic-settings` was chosen over `python-dotenv` to
avoid.

**Decided (later the same day, superseding a wrong turn):** **Phase 1a uses no database at all.**
The session first proposed DuckDB's FTS extension — already a dependency, Porter stemming,
query-time `k`/`b` — and recorded a conflict with the LanceDB rationale, which had chosen LanceDB
partly so the 1a lexical baseline and the 1b vector run would share one store. Both were the wrong
frame. **BM25 is a ranking formula, not a storage engine**: an inverted index in a `dict` over the
6,722 chunks builds in 214 ms and answers in 3–4 ms, on the standard library alone. So the choice
was never "which database" — it was a database nobody needed. `plan.md` §1a now says *no database
of any kind* rather than *no vector database*, and `lancedb.md`'s shared-store bullet is marked
superseded: the 1a-vs-1b comparison straddles two systems deliberately, which is not a confound
because both read the same `chunks.jsonl` at the same `snapshot_id` against the same gold set.

**Rejected:** a BM25 *library*. `rank-bm25` is unmaintained and `bm25s` drags in numpy/scipy, and
neither earns a dependency over ~40 lines of stdlib — which also keeps `b`/`k1` directly in hand,
which is what chunking.md §5 hands forward as the thing to tune against the gold set.

**Rejected:** `pydantic-evals`, which arrived inside the full `pydantic-ai` bundle. `evals/runner.py`
is already the pluggable swap point; a second eval framework would pull Phase 1a sideways.
Also rejected a `config.local.yaml` override — it reintroduces exactly the invisible-override hole
this split closes. One-off experiments should be an explicit flag the run record captures.

**Dead end:** `Agent("openai:...")` **does not see `Secrets`.** PydanticAI reads `OPENAI_API_KEY`
from the process environment, and `uv run` does not load `.env`, so the obvious wiring raises
`UserError` even though the settings object loaded the key correctly. The fix is to pass
`OpenAIProvider(api_key=...)` explicitly, which is the better shape anyway — the credential flows
through one audited place instead of ambient global state. Worth knowing before writing the agent.

**Dead end:** two pyright frictions with `pydantic-settings`, both fixed rather than suppressed.
`Secrets()` — the only correct way to call it — is a `reportCallIssue` for a missing argument, so
the field uses `Field(default=...)`, which reads as "required" to pydantic and "has a default" to
pyright's `dataclass_transform`. And the documented `_env_file=None` test keyword is invisible to
pyright, so the tests use an `IsolatedSecrets` subclass overriding `model_config` instead.

**Dead end:** the first draft's fake key in `tests/test_settings.py` was a **blocking**
`cred:assignment` hit. It was a hyphenated phrase that read as obviously fake to a human but began
with a word `PLACEHOLDER_RE` does not know, and that regex anchors at the *start* of the value — so
only a value **beginning** with `placeholder`, `dummy`, `fake`, `test` (etc.) is recognised. The
guardrail catching the assistant's own fixture is the system working; the constant was fixed and
carries a comment saying why for whoever writes the next one. Then this very entry tripped the same
marker by quoting the bad literal — the 2026-07-31 entry below already records the rule that
prevents it (**write the pattern, never the specimen**), which is worth re-reading before
documenting any blocking shape.

**Also:** full `pydantic-ai` was chosen over `pydantic-ai-slim[openai]` on request. The cost is
concrete — it pulls `google-genai`, which pins `websockets<17.0` and downgraded it from 17.0.1 to
16.1.1. Nothing breaks (`uvicorn[standard]` needs only `>=13.0`, and SSE uses no websockets), but
it is a transitive pin from a provider the repo never calls.

**Stopped at:** clean, and verified where it counts. `make check-all` (84 tests, pyright 0 errors),
`make scan`, and `make chunk-check` all pass — the last one reproducing all three committed
manifests with identical `snapshot_id`s, which was the real risk in moving `ChunkParams`. The
`params_sha256` was confirmed byte-identical *before* any downstream file was touched. A live
OpenAI call was made by hand to prove the key path end to end; **no code in the repo calls an LLM.**

### 2026-08-13 — a skill for contract sync, and vitest joins the gate

**Did:** added the `sync-frontend` skill — the procedure for propagating a FastAPI contract change
through codegen into the frontend — and folded `vitest` into `make ui-check`, so the stream-parser
suite now runs as part of `check-all` rather than only on demand.

**Decided:** **the skill is deliberately not about `make types`.** That is one line and needs no
skill; the document exists for the three things around it — the schema diff is the work list (which
is only true because `dump_openapi.py` renders byte-stably, so no line in it is dict ordering), the
seams codegen cannot see, and an empty diff after a real backend change being a *symptom* rather
than a success. The seam that justifies the whole document: `client.ts` hand-writes its URL strings
and the generated `paths`/`operations` types are unused, so a **renamed route compiles clean and
404s at runtime**. Nothing in the toolchain catches it; a by-hand diff of the `paths` keys is the
only defence, so the skill says to do it even when the diff looked small.

**Decided:** **vitest runs in `ui-check`, not only in `ui-test`.** Same shape as the earlier
argument for `tsc`, and stronger: `stream.ts` is the one frontend module with real logic, and the
bugs its tests cover — an SSE frame or a multi-byte character split across a chunk boundary — are
invisible in ordinary use, so the suite is the only feedback that exists. Neither reason `scan` and
`chunk` sit outside `check-all` applies here: it writes nothing and takes about a second.

**Decided:** the skill's stopping rule is CLAUDE.md's "the UI tracks the phases, it doesn't lead
them", stated explicitly because the instinct mid-sync runs the other way. A new response field that
*could* be rendered gets reported and asked about, not rendered; a widened `Literal` surfaces the
question (what does the new lane look like?) rather than an invented answer to unblock the compiler.
The contract was frozen early precisely so a Phase 4 field does not acquire Phase 4's UI today.

**Also:** the F0 UI was run and looked at in a browser, closing the open question the previous entry
left — the pixels are now verified alongside everything beneath them.

**Stopped at:** the skill has not been run against a real contract change. Its §3/§4 claims are read
off the F0 code as it stands, not proven by an actual sync — the first Phase 1a contract edit is
what will test them.

**Commits:** `791b875`, `2dba60c`

### 2026-08-13 — Phase F0: the contract, the stub, and the UI on top of it

**Did:** built the whole frontend half of Phase 0 — the frozen API contract, a FastAPI app serving
canned answers over both JSON and SSE, an eval runner with HTTP-triggered runs, and a React UI
rendering all of it. Phase 0 is now complete on both halves.

**Decided:** **`api/models.py` imports neither `evals` nor `fastapi`, and eval *wire* models live
in `routes/evals.py`.** `evals/models.py` had a standing note to move `SourceType` into the
contract module and import it back; doing so fixes the dependency arrow as evals → contract, which
then forbids the obvious tidy-up of putting `EvalQuestionsResponse` (which needs `GoldQuestion`)
beside the other models. The no-`fastapi` half is the load-bearing one: `tests/test_gold_set.py`
transitively imports the contract, and the gold-set tests should not drag in a web framework.
`api/__init__.py` is docstring-only for the same reason — a single re-export of `create_app` there
would make `import api.models` execute `app.py`, reach `routes/evals.py`, and land back in a
half-initialised `api.models`.

**Decided:** **the SSE discriminant goes inside the JSON payload, and payloads are wrapped.**
frontend_plan §4.5 sketched bare payloads with the type only on the `event:` line. That cannot be
a discriminated union: `openapi-typescript` has nothing to narrow on, so `stream.ts` would need a
hand-written string→type map — the duplicated contract CLAUDE.md exists to prevent — and `step`
and `done` are not structurally distinguishable as bare objects. The `event:` line is kept for
`curl` readability and *derived* from the payload, so the two cannot drift. Getting the union into
`openapi.json` at all needed a `StreamEventEnvelope` model (the `responses=` metadata takes a
model, not an annotated union) plus a response class declaring `text/event-stream` at class level;
without the latter FastAPI files the schema under `application/json` and it looks correct while
being wrong.

**Decided:** **streaming was pulled forward from F1 into F0.** Three reasons, and the first is the
one that decides it: `/api/chat/stream` is the only non-artificial place to hang the envelope, so
the alternative was a fake schema-only endpoint returning something nobody wants. The plan also
calls the stream parser "the one place a subtle bug hides", and debugging it beside a brand-new
agent in 1a is the worse ordering. It cost a generator, since it is the same canned response
either way.

**Decided:** **eval runs are triggerable over HTTP (settling frontend_plan §10.2), and the
answerer is a parameter.** At Phase 0 that parameter is the chat stub, so metrics are *genuinely
computed* against canned answers rather than faked — recall@5 lands at 0.033 and abstention
accuracy at 0.40, which is the honest picture — and every run record carries `runner: "stub"`,
which the dashboard renders as a badge. Phase 1a swaps one function and the API, the storage
format, and the UI are untouched. Progress events are **buffered and replayed** rather than pushed:
the stub finishes 35 questions in under a millisecond, so a push-only stream would routinely
complete before the browser opened it and the dashboard would show an empty run that had in fact
succeeded. That stays correct when 1a makes a run slow.

**Decided:** **`check-all` runs the frontend gate but skips it with a message when
`frontend/node_modules` is absent.** frontend_plan §7 said to fold `tsc` and lint in
unconditionally, which breaks the repo's primary command on a fresh clone. Skipping is not the
softer option here — `tsc` is a correctness check, unlike `scan` (slow) and `chunk` (writes files),
so it belongs in `check-all` rather than beside them.

**Decided:** **TypeScript is pinned to `~5.9`, against the 6.x `create-vite` now scaffolds.**
`openapi-typescript` peer-requires 5.x, and the codegen is the mechanism that makes a Pydantic
change a TypeScript compile error — frontend_plan §4.3's "single highest-leverage choice". Trading
that for a TS major nothing needs is the wrong trade. Also swapped ESLint for `oxlint` (what the
template ships now; same `npm run lint`, no config) and skipped `sse-starlette` entirely — §7
marked it optional and the frame writer turned out to be three lines.

**Decided:** the stub's two citations have deliberately *different shapes* — one with an external
`url` and a score, one with neither — and its trace covers all four `kind` values with one step
leaving every optional field unset. A canned response that fills every field cannot catch a
component that renders `undefined ms`. Trigger words (`abstain`, `lanes`) select the abstention and
all-three-lanes variants rather than React fixtures, so those paths go through the real serializer
and the real generated types.

**Rejected:** a lazy corpus index. Measured the eager load at ~23 ms and ~12 MB for all 2,056
documents, which is less than the cost of writing the lazy path. Also rejected a `doc_id →
byte-offset` sidecar: it saves the memory and buys a second file format, a rebuild step, and a
seek per request. Chunks are deliberately never loaded by the API — `chunks.jsonl` is git-ignored
and a fresh clone has none, so `/api/health` reads the committed manifest instead.

**Dead end:** the first eval-run test monkeypatched `runner.EVAL_RUNS_DIR` and still wrote into the
real `data/eval_runs/` — default arguments bind at definition, so `runs_dir: Path = EVAL_RUNS_DIR`
never saw the patch. Caught only by listing `data/` afterwards, not by a failing assertion. Every
persistence function now takes `Path | None` and resolves through `_runs_dir()` at call time. Worth
remembering: a test that quietly writes into the repo's data tree is exactly what the public-repo
guardrail exists to stop, and nothing in the suite would have said so.

**Dead end:** Vite 8 binds `[::1]` only, so `curl 127.0.0.1:5173` is refused while
`localhost:5173` works — an ugly split, and inconsistent with the uvicorn side, which binds
`127.0.0.1` deliberately because there is no auth. `server.host` is now explicit.

**Dead end:** the SPA fallback's `assets/` carve-out was written as `path.startswith("assets/")`,
which misses a bare directory request — Starlette passes that through as `assets` with no trailing
slash, so `/assets/` fell through to `index.html` with a 200. Compared as a whole path segment now,
with the bare-directory case in the test parametrisation.

**Dead end:** Homebrew on this machine has a stale formula index (no `node@22`, and it errors on
the macOS version), so the Node 22 upgrade went through a `~/.nvm` install instead. That is
self-contained and left the existing `/usr/local/bin/node` v20.7.0 untouched — but **nvm was
deliberately not added to the shell profile**, so an interactive shell still gets v20.7.0. The
Makefile's `ui-*` targets source nvm themselves, so `make` works either way; a bare `npm` in a new
terminal does not. See *Open questions*.

**Stopped at:** clean. `make check-all` (64 pytest + tsc + oxlint), `make ui-test` (11 vitest),
`make types-check`, and `make scan` all pass, with every advisory scan count exactly at baseline —
the `.gitignore`-before-`npm install` ordering held and `node_modules` never entered scan scope.
Verified end to end over HTTP: the proxy, the SSE stream through it, a browser-triggered eval run,
citation drill-down against the real corpus, and the SPA deep link in both dev and single-process
mode. **The rendered UI itself has not been eyeballed in a browser** — everything below the pixels
is verified, the pixels are not.

### 2026-08-13 — chunking, and the duplicate doc ids it surfaced

**Did:** built the chunking step — `src/health_coverage_navigator/chunking/` (params, models,
splitter, per-source strategies, pipeline, CLI), `tests/test_chunks.py` (17 tests), `make chunk` /
`make chunk-check`, and [chunking.md](chunking.md). Also added `corpus.py` as the shared text-corpus
vocabulary, and fixed a corpus defect the work exposed. Phase 0's backend is now complete.

**Decided:** **doc ids come from the raw post's filename stem, and the Spanish pages are dropped.**
`healthcare_gov` had 803 records but only 747 distinct ids: the Spanish content object at
`/es/hawaii/` self-reports `url: "/hawaii/"` **and** `lang: "en"`, so `id = slugify(url)` collapsed
all 56 state pages into pairs, and the existing `--lang` filter was a no-op that could never have
caught it. Both fields now derive from the stem (804 stems, zero collisions) and `--lang` defaults
to `en`. Chunking forced the issue — two records sharing a doc id make a citation label ambiguous
and a chunk's parent unidentifiable — but the bug predated it and would have quietly scored
duplicate-text retrievals in every eval from here on. Deduping in the chunker was rejected: files
sort `es_hawaii.json` **before** `hawaii.json`, so "keep first" would have silently stamped the
Spanish title onto 21 English state pages. `normalize()` now raises on a duplicate id rather than
trusting the caller.

**Decided:** **`chunks.jsonl` is git-ignored; `chunks_meta.json` is committed.** This inverts the
"processed is committed" rule and is the first exception on that side of the tree, so the reasoning
matters. Size (~7 MB) is not it. The real arguments: chunks are a pure offline function of
committed inputs (unlike `corpus.jsonl`, which needs network to rebuild) and rebuild in about a
second; they churn end-to-end on every parameter tweak, and git deltifies reordered JSONL badly.
The decisive one is that `scan_sensitive.py` scopes licensing markers to `data/processed/*`, so
committed chunks would **double-count every narrative CPT/HCPCS mention** — triple inside overlap
regions — while adding zero new licensing surface, since chunk text is a verbatim subset of corpus
text. The advisory baselines would have stopped being a number about the corpus. What replaces the
artifact is the manifest plus `test_chunks_match_committed_manifest`, so reproducibility is checked
rather than asserted — the same standard `part_d_spuf`'s CRC32 set.

**Decided:** **overlap is 320 chars because the longest gold snippet is 279.** Not a round number
picked by feel. Combined with snapping the next chunk's start **backward only** — the greatest
boundary at or *before* `end - overlap`, never after — realized overlap is always ≥ 320, so any
passage under that length is wholly inside at least one chunk. That turns
`test_gold_snippets_survive_chunking` from an observation into a guarantee. "Snap to the nearest
boundary" is the obvious-looking version of that code and silently breaks it; the splitter says so
in a docstring.

**Decided:** **NCD sections are split on `## ` and small sections are not merged**, even though 505
of 2,256 NCD chunks are under 200 chars (339 of them `Benefit Category`). `build_text()` writes
those headings specifically so the chunker can keep the section name as a citation label; merging
discards it. `Benefit Category` is always first, so it could only merge *forward*, after which the
heading would misdescribe several KB of clinical indications. And the sections are semantically
atomic — a statutory benefit category, a cross-reference pointer. The residual risk is BM25
length normalization over-favouring short chunks; that is a **Phase 1a `b`/`k1` question to measure
against the gold set**, and chunking.md says so explicitly to stop it being relitigated as a
chunking bug.

**Decided:** the contextual header (`NCD 30.3 > Acupuncture > Indications...`) is a **computed
property**, not stored text. Baking it in would end the verbatim-slice contract that the offsets
test and Phase 4's per-claim highlighting both rest on, and would make a citation-format tweak a
full re-chunk. Keeping the pieces as bare fields instead would push header construction into Phase
1a, 1b and the eval runner separately, where they would drift. Phase 1b's line becomes: embed
`retrieval_text`, **store `text`** — so the `text` key lancedb.md's loader expects is unchanged.

**Decided:** medicare_pubs pages are split to the same budget as everything else rather than kept
one-page-one-chunk. 197 of 964 pages exceed 2,400 chars, and one index and one gold set span all
three corpora — letting one corpus average 2× another's chunk size would make the Phase 1a-vs-1b
comparison partly a measurement of chunk size. Nothing is lost, because `page` is metadata: the
citation is *Medicare & You 2026, p. 31* either way. The printed running header stays **in** the
text (stripping it would break the verbatim contract for ~40 chars) and is *promoted* to `heading`,
which lifts heading coverage from 192 pages to 641.

**Decided:** characters, not tokens, with no tokenizer dependency. Phase 1a is lexical; Phase 1b's
embedding ceiling is 27× the budget, so a real token count buys only a cost estimate that
`n_chars / 4` gives within ~10% — and one baked into a committed artifact is invalidated by any
model change.

**Dead end:** the first cut of the manifest-writing path rewrote `chunks_meta.json` on every run,
because the timestamp always differs. A "re-runnable" step that dirties the tree every time is one
nobody re-runs, so the write is now gated on the content *excluding* `chunked_at`. Verified by
mtime across two runs.

**Dead end:** `BuildResult` as a plain class with an `__init__` had pyright widening
`self.source` to `str`, which then failed at all six `CorpusName` call sites downstream. A frozen
`@dataclass` with real annotations fixed it and deleted the boilerplate. Worth remembering: an
inferred attribute is not the same as a declared one.

**Deviation from the plan:** the per-source path helpers (`corpus_path`, `chunks_path`,
`chunks_meta_path`) live in `corpus.py`, not `paths.py` as planned — `paths.py` cannot name
`CorpusName` without importing `corpus.py`, which imports `paths.py`. `paths.py` keeps the
directory constants; `corpus.py` owns everything typed by corpus name.

**Stopped at:** clean. `make check-all` (32 tests, 0 pyright errors), `make chunk-check`, and
`make scan` all pass. One baseline moved: `pii:phone` 39 → 37, because dropping the Spanish state
pages removed two duplicate agency numbers — a corpus that shrank, not a change in what it carries.

### 2026-08-03 — pyright never actually checked `tests/`

**Did:** added `"tests"` to `[tool.pyright]`'s `include` in `pyproject.toml`, fixed the two real
type errors that surfaced once it did (both in `tests/test_gold_set.py`, both `CorpusName | None`
/ `str | None` fields being indexed/passed as if narrowed to non-`None`), and made `make test` run
`pytest -v` so per-test names show by default instead of dots.

**Decided:** the fix for the type errors is `assert q.corpus is not None` / `assert
q.expected_snippet is not None` right after entering each `gold.in_corpus()` loop, rather than
loosening the `GoldQuestion` model. The fields are genuinely `Optional` — `None` for abstentions,
required otherwise — and that's enforced at runtime by the model's validator, not by the static
type, so pyright has no way to know a loop born from `in_corpus()` excludes the `None` case. The
asserts double as a real guard (if the validator were ever bypassed via `model_construct`, this
fails loudly instead of a confusing `KeyError`), not just a type-checker appeasement.

**Dead end (a real gap, not just noise):** `make check-all`'s bare `uv run pyright` reported 0
errors the whole time these bugs existed, because `pyright`'s project config only scanned
`include = ["src"]` — 5 files, none in `tests/`. The bug was visible only because the file
happened to be open in the IDE, which type-checks whatever's open regardless of project config.
`check-all` is the command this repo trusts as its correctness gate; a silent blind spot in it
is worse than a caught bug. Confirmed via `uv run pyright --stats` before (5 files checked) and
after (6 files checked) the `include` change.

**Stopped at:** clean. `make check-all` now checks 6 files, 0 errors; all 15 tests still pass.

### 2026-08-03 — Phase 0 gold eval set (35 questions) + eval package + pytest

**Did:** authored `evals/gold/questions.yaml` — 10 questions per text corpus
(`healthcare_gov`/`medicare_ncd`/`medicare_pubs`) plus 5 out-of-corpus abstention cases, backed
by `health_coverage_navigator.evals.{models,loader}` (Pydantic schema + YAML loader) and
`tests/test_gold_set.py`. Also added `paths.py` (the package's first shared module besides
`__init__.py`), pytest as the project's first configured test tool, and a `make test` target
folded into `check-all`.

**Decided:** gold labels anchor on `corpus.jsonl` **doc ids**, not chunk ids — `expected_doc_ids`
plus a verbatim `expected_snippet` from the primary doc's `text`. This is what unblocks the eval
set ahead of chunking (see the corrected *Next up* note above): doc ids are stable regardless of
how chunking eventually splits a document, and the eval runner can map a retrieved chunk to its
parent doc for `recall@k` today. When chunk-level scoring is wanted later, the snippet already
pins the exact passage, so no relabeling pass is needed. The corpus-dependent invariants that
matter — do these doc ids exist, is the snippet actually verbatim, is the target not a `RETIRED`
NCD, do the distribution counts hold — are enforced by `tests/test_gold_set.py` against the live
corpus rather than trusted by inspection; the structural invariants (abstention shape, required
fields) are enforced by a pydantic `model_validator` in `evals/models.py` instead, since those
hold independent of the corpus.

**Decided:** `expected_doc_ids` is **any-of**, not all-of. The three corpora are genuinely
redundant — the SNF days-21-100 coinsurance figure and the "homebound" definition each appear
verbatim in multiple publications — and penalizing a retriever for finding the equally-correct
one would measure nothing real. `recall@k` counts a hit if any listed doc lands in the top *k*;
`MRR` uses the rank of the first hit. Questions that require **combining** two documents are
Phase 4 multi-hop territory, out of scope here.

**Decided:** add 5 abstention questions beyond the ~30 asked for. Phase 1a's acceptance test
explicitly requires abstaining on out-of-corpus questions, and `abstained` is a first-class
boolean in the frozen API contract specifically so this is gradeable without parsing answer text
— an all-in-corpus gold set would leave that guardrail untested. Each abstention carries
`becomes_answerable_at_phase` (provider lookups and formulary checks turn `3`; time-sensitive
web-lane questions turn `2`; a future plan-year figure and an out-of-domain question stay `null`
permanently) so a later phase's eval run doesn't score a now-correct answer as a false abstention.

**Decided:** difficulty is graded and labeled (8 easy / 14 medium / 8 hard across the 30
in-corpus questions) and is about the **phrasing gap** to the source wording, not about how
counter-intuitive the correct answer is — two of the "easy" NCD questions (`ncd-09`, the insulin
syringe rule; `ncd-10`, screening vs. diagnostic mammography) have surprising or easy-to-overclaim
answers precisely so the set can catch an agent that pattern-matches to a plausible "yes" instead
of grounding in what the source actually says.

**Decided:** at most 6 of the 30 in-corpus questions may be `volatile: true` (year-specific dollar
figures); only 2 are (`pub-02`'s SNF coinsurance, `pub-03`'s Part D late-enrollment penalty
math), both pinned to `plan_year: 2026`. Preferring rule-shaped facts (a 6-month enrollment
window, a homebound definition) over dollar amounts keeps the set from rotting when 2027 figures
land.

**Decided:** the file lives at `evals/gold/questions.yaml`, not under `data/`. `data/` is the
vendored-corpus tree the licensing scanner's path-scoped markers apply to; this is hand-written
prose *about* the corpus, and the `part_d_spuf` dead end (explanatory text written into a file
under `data/` tripping a blocking marker) was reason enough not to repeat the pattern here. YAML
over JSONL specifically for this file: it's hand-authored and human-reviewed, unlike the
generated `corpus.jsonl` files, and comments plus readable multi-line blocks make a label change
a reviewable diff instead of an escaped-`\n` blob.

**Dead end (caught by the test suite, not by inspection):** the first draft of `hcg-05` cited
`appeal-insurance-company-decision_external-review` for a sentence that actually lives in the
sibling `appeal-insurance-company-decision_appeals` doc — an easy mistake with two docs this
similarly named and titled. `test_expected_snippet_is_verbatim` caught it immediately. A second,
subtler miss on `pub-03`: the snippet's leading "The" was typed lowercase against a doc that
capitalizes it — same test, same immediate catch. Both are the reason the plan required this
test rather than trusting hand-verification: a hallucinated citation is exactly what a language
model (or a human skimming quickly) produces most confidently.

**Glossary:** added `Medigap Open Enrollment Period`, `benefit period`, `grace period`,
`homebound` (upgraded from a passing mention to a real entry), `national base beneficiary
premium`, `creditable (prescription drug) coverage`, `LDCT`, `cLBP`, and `AHI / RDI` — every term
the new questions introduce that the glossary didn't already carry.

**Stopped at:** clean. `make check-all` (ruff, format-check, pyright, pytest — 15 tests) and
`make scan` both pass with no new advisory hits; the loader's distribution summary matches the
counts above.

### 2026-08-02 — NPPES and openFDA are API-only; no bulk downloads

**Did:** recorded the decision not to bulk-download NPPES or openFDA, and swept the docs and the
scanner for claims that assumed otherwise.

**Decided:** both are used live through their APIs and **never vendored**. Provider lookup and
drug-label lookup are inherently one record at a time, so a 4 GB NPPES mirror (or the openFDA
label dump) would buy storage cost and a staleness problem in exchange for nothing the API
doesn't answer fresher. This closes the bulk-source list at five.

**Decided:** the consequential downstream effect is on the guardrail, not the ingestion. The
`pii:npi` baseline in `sensitive_baseline.toml` was annotated "expected to rise when NPPES lands
in Phase 3 — provider NPIs are FOIA-disclosable and cleared to vendor", and `scan_sensitive.py`
justified its whole advisory PII tier partly on that. Neither is true now: **no provider-level
data will ever reach a scanned file**, so `pii:npi` is expected to stay at zero permanently and
a nonzero is a genuine "go and look" rather than a baseline to ratchet up. That is a
strengthening of the guardrail and is now written down as such — it would have been easy to
leave the old note in place and quietly accept a future ratchet.

**Rejected:** keeping a trimmed NBER "core" NPPES mirror as a dev fixture. With no bulk
ingestion at all there is nothing for it to be a fixture *of*; Phase 3's synthetic fixtures
cover the testing need without vendoring real practitioner names. The **NBER** glossary entry
was removed rather than corrected, since the term no longer appears anywhere in the repo.

### 2026-08-02 — Part D SPUF, fetched by byte range

**Did:** added the fifth bulk source, `part_d_spuf` — the quarterly Part D formulary files —
with a guide in [part_d_spuf_data.md](part_d_spuf_data.md). Requested explicitly, ahead of the
phase it belongs to.

**Decided:** fetch **individual zip members over HTTP range requests** instead of downloading
the published file. The container is 2.49 GB and holds ten nested per-file zips; the seven we
want are 9.4 MB, and the pharmacy-network file we don't want is 92% of the weight. So the script
reads the zip central directory over HTTP and pulls only the members it needs. This is a real
departure from the other four downloaders and worth the ~90 lines because the alternative is a
250x transfer cost on every refresh — the kind of thing that gets a pipeline run once and then
quietly abandoned. What makes it trustworthy is the **CRC32 in the central directory**: every
member is verified against it before being written, so a truncated or mis-offset range fails
loudly rather than landing as plausible garbage. A range answered `200` instead of `206` is a
hard error, never a fallback — silently streaming 2.49 GB is the exact failure this avoids.

**Decided:** the committed sample is anchored on `CONTRACT_ID` by a **seed + top-up** rule, not
by a single filter. `exchange_puf`'s "one state" approach cannot transfer: `STATE` is populated
only for Medicare Advantage rows, every standalone PDP leaves it blank (they are region-coded),
and Alaska — that source's default — has zero rows here. Seed takes the smallest PDP and
smallest MA contract so both plan shapes appear; top-up then adds the smallest contributor for
any file the seed would leave empty. The top-up is not tidiness: indication-based coverage names
only **three contracts nationally**, so no seed hits it by chance, and a 0-row fixture cannot
test a join. The rule is computed each run rather than hardcoded, since a contract can stop
being offered between quarters.

**Decided:** treat the source as **Latin-1**. CMS documents these files nowhere as anything but
text, and six of the seven are pure ASCII — but plan-information carries three Spanish plan
names (`Óptimo Plus`, `Freedom Máximo`, `Community y Más`) that make a strict UTF-8 read raise.
ASCII being a subset of Latin-1 means decoding all seven that way is exact, not merely tolerant;
no `errors="replace"`, which would have silently corrupted those three names. The mirror is
therefore lossless in content while transcoding to UTF-8, and that distinction is written down
rather than left implicit.

**Decided:** the `licensing:hcpcs-shaped` scanner hit is an allowlist case, not a regex fix. A
Medicare `CONTRACT_ID` is a letter plus four digits (`H1671`), which is *exactly* an HCPCS
Level II code — and `H`/`R`/`S` are all real HCPCS letters, so the two are indistinguishable by
shape. Loosening the detector would hide real `G0465`-style tokens in the other corpora; what
disambiguates is context, so the entry is scoped by path and any other letter still fires.

**Rejected:** downloading the whole container and extracting locally — simpler and matches
`download_exchange_puf.py`, but pays 2.49 GB to read 9 MB. Also rejected wiring up the
pharmacy-network file (2.29 GB across six parts needing reassembly, and nothing before Phase 5
reads it) and fetching pricing by default (191 MB); pricing is defined and opt-in via `--file`.

**Dead end:** the first `_meta.json` `license_note` spelled out which code sets the source does
*not* carry, and tripped the scanner's blocking `CDT` marker — prose about the guardrail,
written into a committed file under `data/`, where the markers apply to the text itself. Reworded
to describe the position without naming the code sets; the named version lives in the script
docstring and the data guide, both outside `data/`. Worth remembering before writing any other
explanatory string into a committed data file.

**Dead end:** the first cut of the 304 short-circuit checked whether the extract existed *on
disk*. A member that failed after extraction but before it was measured left a file behind that
no catalog entry vouched for, so the next run's 304 skipped it permanently — the file was
stranded and `normalize()` silently ignored it. Fixed twice over: "held" now means on disk **and**
in `catalog.json`, and extraction writes to `.part` and renames only after measuring, so a
failure leaves nothing a later run can trust.

**Stopped at:** clean. Verified end to end by wiping `data/{raw,processed}/part_d_spuf` and
rebuilding from nothing — all seven members reproduced with identical sha256, CRC32, byte
offsets, and row counts, and byte-identical sample files. Nothing consumes this source yet, by
design.

### 2026-08-01 — Exchange PUFs + vector-backend choice

**Did:** added the fourth bulk source — the Exchange PUFs (Plan Attributes, Benefits & Cost
Sharing, Service Area for plan year 2026) — with a guide in [exchange_puf_data.md](exchange_puf_data.md).
Chose LanceDB for Phase 1-b and wrote the reasoning down in [lancedb.md](lancedb.md) before any
code exists. Promoted `/wrap-up` from a command to a skill so it can trigger on intent, not only
on the slash form. Closed out two long-standing drifts in `plan.md`: it now names the MCD
Coverage API as the NCD route (superseding the bulk ZIPs it had described since the original
survey) and LanceDB as the settled Phase 1-b store.

**Decided:** `processed/` no longer means one thing. The three text corpora emit an app-ready
`corpus.jsonl`; `exchange_puf` emits a **lossless mirror** — every column `VARCHAR`, values
byte-for-byte, no trimming, no type coercion. [data/README.md](../data/README.md) now names both
kinds rather than letting this source quietly stretch the old definition. The reason is that
every "numeric" column here is publisher-formatted text (`'$450 '`, `'70.88%'`, `'Not
Applicable'` beside an empty field, which mean *different* things), and that Plan Attributes
carries **36 max-out-of-pocket columns** — choosing which one is "the" MOOP needs real query
requirements from Phase 3/5, and guessing once at ingestion is worse than not guessing. DuckDB's
CSV reader would have collapsed the empty-vs-`Not Applicable` distinction by mapping empty
fields to `NULL`, so `nullstr` is overridden to a string that cannot occur in a CMS PUF.

Service Area is fetched even though `plan.md` names only two PUFs: without the ZIP-to-area
mapping, `ServiceAreaId` cannot answer the plan's own canonical structured-lookup example
("plans in ZIP 30076"), and the table is 44 KB.

Committed a one-state (AK) full-column sample instead of the real files — Benefits & Cost
Sharing alone is a 375 MB CSV — extending the manifest-not-blob bargain `medicare_pubs` already
made. The sample is **regenerated and re-scanned on every `normalize()`**, never hand-curated,
so a future plan year that introduces code-bearing text into Alaska's data fails the run rather
than resting in a stale fixture nobody re-checks. The full PUFs do contain CPT/CDT numbers in
issuer-authored free text (249 lines) — narrative reference, not a redistributed code table, and
git-ignored regardless; the only question that mattered was the sample, which is clean.

For Phase 1-b: **LanceDB**, on two grounds that outrank raw scale at a few thousand chunks —
it holds vector *and* BM25 search in one table, so the 1-a lexical baseline and the 1-b vector
run share a store instead of the comparison straddling two systems; and its versioned writes let
an eval score be pinned to the corpus/embedding snapshot it was measured against. Embeddings are
computed by us and handed over as plain vectors (LanceDB's registry never calls OpenAI on our
behalf), which keeps bulk ingestion eligible for the Batch API discount.

**Rejected:** Chroma — comparable setup cost, but in-memory-first and weaker on the
eval-reproducibility angle. pgvector — the better long-term fit *if* Phase 5 puts the PUFs in
Postgres rather than DuckDB, but not worth operating a server in Phase 1-b. Qdrant/Weaviate/
Pinecone — a server before we need one, or a managed service that reopens the BAA question,
which is the same reason OpenRouter was ruled out earlier: health-domain data should not leave
the machine. Also not fetched: the Rate PUF and five other tables (Phase 5 territory; the URL
pattern is uniform, so adding one is a dict entry).

**Commits:** `a0b6147`, `813123c`, `5afcbdd` (the `plan.md` and `glossary.md` corrections above
are uncommitted)

### 2026-07-31 — sensitive-data scanner

**Did:** replaced the ad-hoc, per-source licensing check with one scanner
(`scripts/scan_sensitive.py` + a `scan-sensitive` skill) covering all three halves of the
public-repo guardrail — credentials, PII/PHI, licence-restricted content — across every file
that is or would become public. Runs clean today; baselines recorded.

**Decided:** three severities, not two. The NCD session had already established that a check
which fails on legitimate narrative `CPT` mentions "would just get switched off"; that lesson is
now the scanner's architecture rather than one function's local behaviour. **Blocking** exits 1
with no judgement call available, **advisory** compares against a recorded count so a *jump* is
reported rather than a nonzero, **allowlisted** suppresses but still names and counts the
suppression. The advisory tier is not a softer blocking tier — it exists because this corpus
genuinely contains code mentions and agency contact details, and because Phase 3's NPPES data
will genuinely contain real provider names and NPIs that are FOIA-disclosable.

The scan is deliberately **not** part of `make check-all`. `check-all` is the fast inner-loop
command; scanning the whole corpus is a pre-publish gate you invoke on purpose, and burying a
slow check inside a fast one is how the fast one stops getting run.

Licensing markers are scoped to `data/raw/**` and `data/processed/**` rather than the whole
repo, because `data/README.md`, `docs/glossary.md`, and the scanner itself all discuss CPT, CDT
and the AMA by name. **Prose about the guardrail must not trip the guardrail** — the risk being
detected is a code table inside ingested data, not the word.

Baselines are whole-repo totals, so `--staged` / `--unstaged` / `--paths` runs list advisory
hits without comparing them and cannot exit 2. Comparing a whole-repo baseline against a subset
would report every marker as "below baseline" and advise lowering it, which is actively wrong.

Allowlist entries are principled classes, not enumerations, where a principle exists: `*@*.gov`
is a government contact point by definition, and a toll-free area code cannot be a personal
number — that one suppresses 1,429 helpline hits so the reviewable remainder is 39 numbers
rather than a haystack. The four non-government domains are listed individually on purpose, so
a *new* third-party domain surfaces as drift instead of being absorbed by a wildcard.

**Rejected:** a date-of-birth detector. In a corpus built on effective dates, transmittal dates
and revision histories it is pure noise, and would teach everyone to ignore the PII layer
wholesale. A DOB here would arrive attached to a name, which the e-mail/phone/NPI markers
already catch. Also rejected importing the licensing markers from `download_medicare_ncd.py`:
`scripts/` is not a package and that module pulls in `requests`/`bs4`, so they are duplicated
with a "change one, change the other" note — a dependency-free scanner was worth the copy.

**Dead end:** two regex shapes that had to be tightened rather than allowlisted, both now
pinned as anti-canaries. A bare `sk-[A-Za-z0-9_-]{20,}` matched inside the ordinary URL slug
`ask-about-preventive-services`; fixed by anchoring to real vendor prefixes plus a lookbehind.
`pii:npi` as "any Luhn-valid 10-digit run" reported a UUID fragment, a vendor PDF filename and
an IRS publink anchor — Luhn alone rejects only ~90% of arbitrary digit runs — so the
identifier must now also be *labelled* NPI. The general rule this establishes: allowlisting a
regex bug hides the next true positive that shares its shape.

The scanner then caught a live one during this session's own glossary edit: an illustrative
SSN written out in the `SSN` entry blocked the scan. Fixed by writing the *pattern* instead of
a specimen — the standing convention for documenting any blocking shape.

**Commits:** `699fdf3`

### 2026-07-31 — Medicare NCD corpus + glossary

**Did:** added the third and last Phase 0 bulk source — Medicare Coverage Database NCDs, 345
determinations — and [glossary.md](glossary.md), with a standing "keep it current" rule in
`CLAUDE.md` and a matching sweep step in `/wrap-up`.

**Decided:** fetch NCDs from the **MCD Coverage API**, not the bulk ZIPs `plan.md` points at.
The API's auth boundary falls exactly on this repo's licensing boundary — National coverage
endpoints answer keyless, LCD and Article endpoints `401` — so "NCDs only" stops being a rule
the script has to police and becomes the set of endpoints that answer at all. The fetcher
therefore never requests a license token, and `fetch_json()` treats `401` as fatal rather than
retryable so a mistake is loud.

The licensing scan **reports** `CPT`/`HCPCS` hits instead of failing on them. `medicare_pubs`
can claim zero occurrences; this corpus cannot — 22 records mention codes narratively in
revision histories. A scan that failed on those would have been switched off within a week, so
it distinguishes blocking markers (`©`, `CDT`, AMA/ADA notices — all zero) from advisory ones.

`effective_date` is `null` for the 75 longstanding NCDs, with CMS's explanatory sentence moved
to `effective_date_note`, rather than storing prose in a date field. `null` means "in force,
date unknown". `revision_history` is kept as a record field but excluded from `text` — it is
retrieval noise that would match date and number queries without answering them.

The glossary was scoped to *domain* vocabulary only, with each entry required to say what the
term means **in this repo** (licensing status, routing lane, schema field, correctness rule).
A bare dictionary expansion is not a useful entry, and the 256 vendored HealthCare.gov consumer
definitions are pointed at, not restated.

**Rejected:** the MCD Downloads-page ZIPs — 403 to non-browser clients, and their single
license click covers the AMA/ADA/AHA terms for Local coverage data sitting beside the National
data, which is precisely the conflation the guardrail exists to prevent. Also skipped the other
license-clean National document types the API serves (NCAs, CALs, MEDCAC materials, Technology
Assessments): they are the *process* behind a decision, and Phase 0 wants the rule.

**Stopped at:** clean. `plan.md` still describes the bulk ZIPs as the NCD route, which this
session superseded — see *Open questions*.

**Commits:** `58ed14a`, `d0b861c`

### 2026-07-31 — progress tracking

**Did:** added this file. Removed the `## Project status` section from `CLAUDE.md`, which now
carries only a pointer here. Added a `/wrap-up` command (`.claude/commands/wrap-up.md`) to
append log entries at the end of a working session.

**Decided:** status lives in exactly one place. `CLAUDE.md` is for conventions that must be
obeyed every session; status is read every session but obeyed never, and mixing the two
dilutes the guardrails and makes every status change look like a rules change in the diff.

**Rejected:** keeping a short status summary in `CLAUDE.md` alongside the full version here —
two sources of truth drift. Also rejected a `Stop` hook for auto-updating this file: it fires
on every pause, not on session end, so it would produce constant low-value entries.

### 2026-07-31 — frontend design

**Did:** wrote [frontend_plan.md](frontend_plan.md) (design only — no code). Added the frontend
slices to each phase in `plan.md` and the frontend section to `CLAUDE.md`.

**Decided:** FastAPI + React/Vite/TS/Tailwind/shadcn in a top-level `frontend/`. Vite proxies
`/api` to uvicorn in dev, so no CORS config anywhere. TS types are generated from the OpenAPI
schema rather than hand-written. Streaming is SSE over `fetch`, not `EventSource` — the latter
cannot send a POST body. The API contract is frozen now: `source_type`, `citations`, `claims`,
`trace`, and `abstained` as a first-class boolean, never inferred from answer text.

**Rejected:** a state-management library and a data-fetching library — there is no server state
worth caching yet. A corpus-browser page — citation drill-down covers most of the need.

**Left open:** frontend_plan.md §10 (multi-turn intent, eval runs over HTTP, corpus browser).

**Commits:** `87e2dc4`

### 2026-07-27 — Medicare publications corpus

**Did:** `scripts/download_medicare_pubs.py` → 83 publications / 964 pages into
`data/{raw,processed}/medicare_pubs/`. Guide in [medicare_pubs_data.md](medicare_pubs_data.md).

**Decided:** the second downloader mirrors the first one's shape rather than being generalized
into a framework — argparse CLI, `raw/` → `processed/` split, idempotent, `--normalize-only`
re-parse. Both emit `corpus.jsonl` with the same field vocabulary (`id`, `source`, `url`,
`title`, `bite`, `text`), which is what makes the sources interchangeable downstream.

**Commits:** `0bbc7b2`

### 2026-07-06 — HealthCare.gov corpus

**Did:** `scripts/download_healthcare_gov.py` → 803 articles/glossary/state pages. Guide in
[health_care_data.md](health_care_data.md). Revised the Phase 0/1 sections of `plan.md`.

**Decided:** Phase 1 splits into 1a (lexical retrieval, no vector DB) and 1b (embeddings behind
the same `retrieve` interface), so 1b has a baseline to be compared against.

**Commits:** `673e2ca`, `023c611`

### 2026-07-04 — tooling

**Did:** ruff, pyright, `Makefile`, and a pre-commit hook running both.

**Commits:** `c31edf5`, `d038216`, `97fb8f8`
