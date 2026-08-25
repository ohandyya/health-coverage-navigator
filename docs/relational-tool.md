# The relational lane — Phase 1-c design

How the agent gets to *look a fact up* instead of searching prose: three tools over the two
vendored structured mirrors (`data/processed/exchange_puf`, `data/processed/part_d_spuf`), queried
by DuckDB in place.

[`plan.md`](plan.md) says **when** this lands and what it is worth; this document is the **how**,
and it is the authority for everything below. Read [`exchange_puf_data.md`](exchange_puf_data.md)
and [`part_d_spuf_data.md`](part_d_spuf_data.md) first — they own what the data *is*, and this
document does not restate them.

Where a number appears it was measured against the real mirrors on 2026-08-18 (duckdb 1.5.5,
M-series laptop), not estimated. Build status belongs to [progress.md](progress.md), not here.

---

## 1. What this phase adds, and what it must not

The reference lane answers *"what is a deductible"*. It cannot answer *"what is the deductible on
plan 38344AK1060002"*, and — this is the whole reason the phase exists — it fails at that question
in the most dangerous way available: `search_corpus` returns a fluent, correctly-cited passage
about deductibles in general. Cited, plausible, and not an answer.

So Phase 1-c registers a second *kind* of source on the same agent. Three things stay exactly as
they are, and a change to any of them means the design is wrong:

- **The agent.** One `Agent`, built in `agent/runtime.py`, gaining tools. No second agent, no
  router component, no pipeline.
- **The mirrors.** Still lossless `VARCHAR` Parquet, still never chunked, still never embedded.
  Phase 1-c reads them; it does not reshape them or add a typed copy beside them.
- **The wire contract.** No new field, no new `SourceType`. Row answers are
  `source_type="structured_api"`, which has meant *deterministic row-level lookup* since Phase 0.

**No new storage engine.** DuckDB reads the Parquet files where they lie — no load step, no
server, no second copy. Measured: 22 ms to open a connection and register all ten tables as views;
3–16 ms for representative lookups; 77 MB RSS with every table registered. The same shape of
argument Phase 1-a made for BM25: *a query engine over files already on disk is not a database to
stand up and maintain*. DuckDB is already a dependency (both downloaders write the mirrors with
it); this is the first time it appears in `src/`.

---

## 2. The data, as it actually sits on disk

```
data/processed/exchange_puf/2026/{plan_attributes,benefits_cost_sharing,service_area}.parquet
data/processed/part_d_spuf/2026Q2/{basic_drugs_formulary,plan_information,beneficiary_cost,
                                   insulin_beneficiary_cost,excluded_drugs_formulary,
                                   geographic_locator,indication_based_coverage}.parquet
```

Four properties drive the design:

**The mirrors are git-ignored.** Only the sample slices under `<source>/sample/*.csv` are
committed (Alaska for `exchange_puf`; four anchor contracts for `part_d_spuf`). A fresh clone has
no mirror — exactly like `chunks.jsonl` and the LanceDB store.

> **Decision: the lane reads the mirror, and refuses when it is absent.** No fallback to the
> samples. The precedent is `routes/chat.py`, which already refuses to quietly degrade a `both`
> configuration to lexical-only: an answer measured against data nobody chose, with nothing in the
> response saying so, is worse than a 503. An Alaska-only answer served as a national one is that
> failure with a health-coverage question attached. The samples are for **tests and fixtures**,
> where the scope is stated by the test itself.

**Partitions are two different shapes.** `exchange_puf` partitions by plan **year** (`2026`),
`part_d_spuf` by **quarter** (`2026Q2`). §7 resolves a request's `plan_year` against both.

**Column vocabularies differ per source.** Exchange PUF is `CamelCase`
(`StandardComponentId`, `TEHBDedInnTier1Individual`); Part D is `UPPER_SNAKE` (`FORMULARY_ID`,
`TIER_LEVEL_VALUE`). The agent cannot guess either — see `describe_table` in §3.

**`catalog.json` is committed and carries per-table `row_count` and `column_count`.** That is the
mirror's manifest, and it is what lets the store detect a partial or stale build up front rather
than as a wrong answer later — the same job `vectors_meta.json` does for the embeddings.

---

## 3. Three tools

> **Decision: three tools, not the four `plan.md` first drafted.** The typed join helpers
> (`lookup_plan_formulary`, `lookup_service_area`) are deferred until the eval traces show the
> agent actually getting those joins wrong. What each would have done, and the join traps each
> would have hidden, is written out in §14a; the tool descriptions below carry the same warnings in
> a sentence each. Writing a helper now would be guessing at a failure that costs nothing to
> observe first — the same habit that put BM25's `k1`/`b` on a sweep instead of a hunch.

| Tool | Returns | Job |
|---|---|---|
| `list_tables(source?)` | `StructuredOverview` | What structured data exists at all: sources, tables, partitions, row and column counts. The counterpart of `list_documents`, and the honest basis for *"what plan data do you actually have?"* |
| `describe_table(table)` | `TableDescription` | Every column name, plus **real sample values** for each and the most frequent values for low-cardinality ones. |
| `query_structured(sql, limit?)` | `QueryResult` | One guarded read-only `SELECT`. The general-purpose tool of this lane. |

### Why `describe_table` is not garnish

Everything in the mirror is publisher-formatted text. `TEHBDedInnTier1Individual`'s four most
common values are `'$0 '` (5,045 rows), `''` (3,492), `'$6,000 '`, `'$2,000 '` — a dollar sign, a
trailing space, and an empty string that means *not this plan's benefit design*, not zero. An
agent that writes `WHERE deductible < 1000` against that gets a string comparison and a confident
wrong answer. `describe_table` is how it finds out before writing the filter, and it is why the
tool returns values rather than just types (every type is `VARCHAR`; a type listing would say
nothing at all).

It also solves the naming problem cheaply. `Plan Attributes` has **151 columns, 36 of them
max-out-of-pocket** (MEHB/DEHB/TEHB × network tier × individual/family). A first attempt at
`TEHBDedInnTier1IndividualAmount` is a binder error, not a row — and DuckDB's binder error already
names the near misses:

```
Binder Error: Referenced column "TEHBDedInnTier1IndividualAmount" not found in FROM clause!
Candidate bindings: "TEHBDedInnTier1Individual", "TEHBDedInnTier2Individual", ...
```

That message is handed back verbatim as the `ModelRetry` text (§4). Free, and better than anything
we would write.

### Tool descriptions are prompt surface

Every docstring is read by the model, so they follow `agent/tools.py`'s existing rules: what the
tool is for, when to reach for a sibling instead, and what a bad result means. Three things the
`query_structured` docstring must say, because they are the documented ways to be silently wrong:

- **Part D `STATE` is blank for every standalone PDP row.** Filtering by state drops exactly the
  plan type a "does my drug plan cover X" question is usually about.
- **The formulary is keyed by `FORMULARY_ID`, not by plan.** Answering "does *my plan* cover X"
  always joins through `plan_information` first; one formulary backs many plans.
- **One plan is many rows.** `plan_attributes` holds 22,059 rows for 5,144 distinct
  `StandardComponentId`s, because each CSR variant is its own row. §6 shows what that does to an
  answer.
- **An empty service area means "statewide", not "nowhere".** `service_area` rows with
  `CoverEntireState = 'Yes'` carry no county and no ZIP. It is the one place in this data where an
  empty result inverts the answer rather than reporting an absence — §14a.

---

## 4. The guard on model-written SQL

Two independent layers, both verified against duckdb 1.5.5. Neither trusts the other.

### 4a. Statement shape — `duckdb.extract_statements`

```python
statements = duckdb.extract_statements(sql)          # raises on a parse error
if len(statements) != 1: reject
if statements[0].type != duckdb.StatementType.SELECT: reject
```

Measured classifications: `COPY (...) TO 'x.csv'` → `COPY`, `ATTACH 'x.db'` → `ATTACH`,
`INSTALL httpfs` → `LOAD`, `CREATE TABLE t AS ...` → `CREATE`, `WITH a AS (...) SELECT ...` →
`SELECT`. So CTEs pass and everything that writes, attaches, or loads is rejected before DuckDB
sees it. Note `PRAGMA version` also classifies as `SELECT` — harmless, and layer 4b blocks the
PRAGMA forms that would matter.

### 4b. The connection sandbox — three settings, in this order

```python
con = duckdb.connect(config={"memory_limit": ..., "threads": ...})
#   ... register every partition's tables as views first ...
con.execute(f"SET allowed_directories=['{processed_dir}']")
con.execute("SET enable_external_access=false")
con.execute("SET lock_configuration=true")
```

Order is load-bearing and was found by measurement, not by reading:

- **Views must be created *before* external access is disabled.** DuckDB views are lazy, so a view
  over `read_parquet(...)` created after the lock fails at query time with
  `Permission Error: ... file system operations are disabled`.
- **`allowed_directories` alone does nothing.** With external access still enabled,
  `SELECT * FROM read_csv('/etc/passwd')` returned the file. The allow-list only takes effect once
  external access is off.
- **`enable_external_access` is a one-way door.** It can be turned off at runtime but not on
  (`Cannot enable external access while database is running`), and `allowed_directories` cannot be
  passed in the `connect(config=...)` dict at all (`Cannot change/set allowed_directories before
  the database is started`). Hence `SET`, in this sequence.

Verified after locking: the registered views still read (22,059 rows), while
`read_csv('/etc/passwd')`, `COPY ... TO '/tmp/x.csv'`, `ATTACH '/tmp/y.db'`, `INSTALL httpfs` and
`SET memory_limit='8GB'` all raise `PermissionException` / `InvalidInputException`. The blast
radius of a model-written `SELECT` is `data/processed/`, read-only.

### 4c. Row cap and timeout

- **Cap.** The tool wraps the statement — `SELECT * FROM (<sql>) LIMIT <limit+1>` — and reports
  `truncated: true` when the extra row comes back. Wrapping rather than requiring the model to
  write `LIMIT` means the cap cannot be forgotten, and `limit+1` distinguishes "exactly the cap"
  from "more than the cap" without a second count.
- **Timeout.** DuckDB has no statement timeout. The query runs on its own `con.cursor()` inside
  `asyncio.to_thread`, with a `threading.Timer` armed on that cursor's `interrupt()`. Verified: a
  deliberately slow aggregate was interrupted at 516 ms against a 500 ms deadline, raising
  `InterruptException`, and both the connection and other cursors stayed usable afterwards. A
  cursor per query is also what makes concurrent eval questions safe on one connection.

### 4d. Every rejection is a `ModelRetry`

Not-a-`SELECT`, two statements, a parse error, a binder error, a timeout, an unknown table — all
come back as `ModelRetry` carrying DuckDB's own message plus the corrective ("`describe_table` will
give you the column names"). This matches `grep_corpus`'s handling of a bad regex. A rejected query
is a normal event in a lane where the model writes SQL, not an error condition.

---

## 5. Reading a text mirror: macros, not a typed layer

> **Decision: register SQL macros on the connection; do not build typed views.** The macros are a
> *tool* the agent may apply; a view would be a *decision* about which of 36 MOOP columns is "the"
> MOOP, which is precisely what the ingestion layer refused to guess and what
> [`exchange_puf_data.md`](exchange_puf_data.md) defers until there are real query requirements.
> Macros keep the mirror lossless and the semantics in the agent's SQL, where the trace shows them.

```sql
CREATE MACRO to_usd(x) AS
  TRY_CAST(NULLIF(regexp_replace(trim(CAST(x AS VARCHAR)), '[$,]', '', 'g'), '') AS DOUBLE);
CREATE MACRO to_pct(x) AS
  TRY_CAST(NULLIF(replace(trim(CAST(x AS VARCHAR)), '%', ''), '') AS DOUBLE);
```

Measured: `to_usd('$450 ')` → `450.0`, `to_usd('$1,200.50')` → `1200.5`,
`to_usd('Not Applicable')` → `NULL`, `to_usd('')` → `NULL`, `to_pct('70.88%')` → `70.88`.

`TRY_CAST` rather than `CAST` is the whole design: an unparseable value becomes `NULL` and drops
out of a comparison, instead of failing a query that is 99% right. **The cost has to be stated in
the tool description**: `to_usd` maps `'Not Applicable'` and `''` to the same `NULL`, and those two
mean different things in this data. So a macro is right for *filtering and ordering* and wrong for
*reporting a value* — the answer quotes the raw cell (§6), never the parsed one.

`describe_table`'s output names the macros beside any column whose sample values look like money or
a percentage. That is where the agent learns they exist; the system prompt stays out of it.

---

## 6. Provenance: what a citable row is

> **Decision: the citable unit is a recorded result row.** Every row any structured tool returns is
> recorded in the run's deps under a synthetic id, and the model may cite only those — the exact
> analogue of `seen_chunks`, and the reason the grounding guardrail keeps working in a lane where
> there is no chunk text to quote.

### The row id

```
<source>/<partition>/<table>#<query_seq>.<row_seq>      e.g. exchange_puf/2026/plan_attributes#q1.3
```

Query-scoped rather than data-keyed, for two reasons. A projection or an aggregate has no natural
primary key, so a data-derived id would make `SELECT COUNT(*) ...` uncitable — and counts are a
legitimate answer here. And the honest claim being made *is* query-scoped: **"this query, against
this partition, returned this row."** For a multi-table join the id names the join
(`part_d_spuf/2026Q2/plan_information+basic_drugs_formulary#q2.1`) and the trace carries the SQL
that produced it.

### The validator, extended

`runtime._validate_grounding` gains a branch. Today a citation is `chunk_id` + verbatim `snippet`;
a structured citation is `row_id` + the **cells** used:

```python
class AgentRowCitation(BaseModel):
    id: str                    # c1, c2 — one id space with chunk citations
    row_id: str                # must be in deps.seen_rows
    cells: dict[str, str]      # column -> the value, copied exactly from the row
```

Three checks, each rejecting with a `ModelRetry` that says what was wrong:

1. `row_id` is in `deps.seen_rows` — the analogue of "no tool returned that chunk".
2. Every column in `cells` exists in that row.
3. Every value is **byte-identical** to the recorded cell. Not normalized, not trimmed: `'$4,500 '`
   is what the source says, and a model that "helpfully" writes `4500` has edited the evidence.
   This is stricter than the chunk path, which collapses whitespace to survive corpus line wrapping
   — a cell has no wrapping to survive.

The answer text may of course say *"$4,500"*; what is pinned is the cited cell.

### Why this matters more here than in the reference lane

`SELECT ... WHERE StandardComponentId = '38344AK1060002'` returns **six rows**:

| CSRVariationType | TEHBDedInnTier1Individual |
|---|---|
| Standard Silver On Exchange Plan | `$4,500 ` |
| Zero Cost Sharing Plan Variation | `$0 ` |
| Limited Cost Sharing Plan Variation | `$4,500 ` |
| 73% AV Level Silver Plan | `$4,000 ` |
| 87% AV Level Silver Plan | `$1,200 ` |
| 94% AV Level Silver Plan | `$400 ` |

"The deductible for that plan" is $400 or $4,500 depending on a variant the user did not mention.
A prose citation would let the model pick one and sound right. A row citation makes the choice
visible — the cited row *names its variant* — which turns a silent wrong answer into either a
correct qualified one or a visible mistake. This example belongs in the eval set.

### The wire `Citation`, unchanged

| field | value |
|---|---|
| `source_type` | `"structured_api"` |
| `title` | `Plan Attributes PUF, PY2026 — 38344AK1060002 (87% AV Level Silver Plan)` |
| `url` | the CMS landing page for that PUF (from `catalog.json`) |
| `snippet` | the cited cells rendered `Column: value`, one per line |
| `doc_id`, `chunk_id` | `null` — the first citations in this repo that are not chunks |
| `score` | `null` — a row either matched or did not |

`_citations()` builds all of it from the recorded row, exactly as it builds chunk citations from
the real `Chunk`. The model contributes *which row* and *which cells*, both checked.

### Empty is an answer

A query returning zero rows is reported as **"no row for that plan in `<table>` `<partition>`"** and
never softened into prose. The tool result says `row_count: 0` explicitly, and the system prompt
gets one added sentence: an empty result is a finding about the data, not an invitation to fall
back on the reference corpus. Distinguishing "not covered" from "no row" from "`Not Applicable`" is
the correctness bar for this lane.

---

## 7. Plan year, partitions, and abstention

`ChatRequest.plan_year` has ridden in the contract since Phase 0 and nothing has read it. Here it
selects the partition, which is the first time the cross-cutting *"pin the plan year"* principle is
enforced by code rather than stated.

Resolution, in order:

1. **Request `plan_year` set** → `exchange_puf/<year>`, and for Part D the newest vendored quarter
   whose year matches (`2026` → `2026Q2`).
2. **Unset** → the newest vendored partition of each source, and the tool result *says which*, so
   the answer can name the year it is talking about.
3. **A year with no vendored partition** → the tool returns the available partitions and the agent
   abstains. It does **not** answer from an adjacent year. CMS keeps many years live; silently
   answering 2026 for a 2025 question is this domain's most common correctness bug, and it is worse
   here than in prose because the number looks authoritative.

`list_tables` always reports the partitions on disk, so "what years do you have" is answerable
without a failed query first.

---

## 8. When the mirror is not there

Three states, and the difference is the design (mirroring `ChunksNotBuiltError` / `VectorsStaleError`):

| state | how it is detected | what happens |
|---|---|---|
| **not built** | partition directory absent | `StructuredNotBuiltError` at startup → `ctx.structured is None` → 503 from `routes/chat.py` naming the downloader command |
| **stale / partial** | a table's Parquet row count disagrees with the committed `catalog.json` | `StructuredStaleError` — refuse, do not answer from half a mirror |
| **built** | all tables present and counted | lane live |

The 503 text follows the existing `NO_CORPUS` / `NO_VECTORS` pattern — a sentence naming the exact
command:

```
The structured plan data has not been built on this machine. The Parquet mirrors are git-ignored,
so a fresh clone has none — run `make puf` (or the two downloader scripts) and restart the server.
```

`make puf` is a new target wrapping both downloaders, so the message can name one command. It costs
~24 MB over the wire (13.5 MB Exchange, 9.4 MB Part D) and a few minutes.

**Whether a missing mirror is fatal depends on configuration**, exactly as it does for vectors: an
agent configured without structured tools must still boot and answer. `_unavailable()` asks
`needs_structured(config)` before deciding.

`GET /api/health` gains a real `structured_api` lane entry — `configured: false, detail: "plan data
not built on this machine — run make puf and restart"` — replacing the placeholder. The UI's badge
legend becomes truthful for a second lane without a contract change, since `LaneStatus.detail` is a
free string.

---

## 9. Module layout, and the one refactor it forces

```
src/health_coverage_navigator/
├── structured/                  # NEW — the engine, no agent imports
│   ├── catalog.py               # partitions, tables, catalog.json manifest check, CMS urls
│   ├── store.py                 # StructuredStore: connection, views, macros, guard, execute
│   └── models.py                # StructuredOverview · TableDescription · QueryResult · Row
├── agent/
│   ├── deps.py                  # NEW — AnswerDeps moves here (see below)
│   ├── structured_tools.py      # NEW — the three tools, model-facing docstrings
│   ├── tools.py                 # keeps select_tools; imports both tool modules
│   ├── models.py                # + AgentRowCitation; AgentAnswer gains row citations
│   └── runtime.py               # + the validator branch and the row → Citation mapping
└── api/{deps,routes/chat,routes/health}.py   # + ctx.structured, the 503, the lane report
```

**The refactor: `AnswerDeps` moves out of `agent/tools.py` into `agent/deps.py`.** Not tidying —
without it there is an import cycle: `structured_tools` needs `AnswerDeps`, and `tools.py` needs
the tool functions to build `select_tools`. `agent/deps.py` is the leaf both import, the same shape
as `api/deps.py` existing so routes and `app.py` do not import each other.

`AnswerDeps` gains two fields, alongside `index`, `vectors`, `seen_chunks`:

```python
structured: StructuredStore | None = None   # None is a real state, like `vectors`
seen_rows: dict[str, Row] = field(default_factory=dict)   # the citable row set
```

**Async placement.** DuckDB is synchronous and CPU/IO-bound, and the repo's rule is that threads
are for genuinely blocking work with no async form — which this is. So `StructuredStore.query()` is
`async def` and does its work in `asyncio.to_thread` (a fresh cursor per call), matching
`vector_search`'s asynchronous seam. The tools are `async def` for the same reason: making a seam
async later converts every implementation, caller and test at once.

**What `structured/` must not import.** Nothing from `agent/`. `store.py` is the DuckDB layer and
has to be testable — and *is* tested — without pydantic-ai, a model, or an API key, the same
constraint `evals/answerers.py` documents.

---

## 10. Configuration

New block in the committed `config.yaml` (no secrets, no env vars — `config.py`'s rule):

```yaml
structured:
  max_rows: 200          # rows one query may return before it reports truncation
  query_timeout_s: 5.0   # the interrupt deadline; measured queries run in 3–16 ms
  memory_limit: 512MB    # DuckDB's ceiling for the whole connection
  threads: 2             # DuckDB worker threads, kept small beside the API's event loop
```

And one new key under `agent:`:

```yaml
agent:
  structured_tools: true   # whether the agent sees the relational lane at all
```

**Why a separate boolean and not a fourth `Toolset` value.** `Toolset` (`lexical`/`vector`/`both`)
selects how the *reference* lane is searched. The relational lane is an orthogonal axis, and
folding it in would produce six meaningless combinations and break Phase 1b's recorded comparison.
So `select_tools(toolset, structured=...)`, and the eval runner grows `--structured` /
`--no-structured` beside `--toolset`.

`EvalRunSummary` gains `structured: bool | None` (`None` for runners with no agent), for the same
reason `toolset` is recorded: a comparison between runs that differ only in this is only possible
if a run says which it was. The dashboard's badge row renders it beside the toolset badge.

---

## 11. Evals

The phase's new thing to grade is **structured-lookup correctness**, plus the first **routing**
measurement.

### Gold-set schema

`GoldQuestion` today has two shapes — abstention and in-corpus — enforced by `_check_shape`. It
needs a third, because a structured question has no `corpus`, no `expected_doc_ids` and no
`expected_snippet`:

```yaml
- id: str-01
  question: "What's the individual medical deductible on plan 38344AK1060002 for 2026?"
  expected_source_type: structured_api
  plan_year: 2026
  expected_table: exchange_puf.plan_attributes        # NEW
  expected_cells:                                    # NEW — verbatim, from the mirror
    - "$4,500 "
  answer_key_facts:
    - "the standard on-exchange silver variant's deductible is $4,500"
    - "CSR variants of the same plan carry different deductibles"
```

The validator branches on `expected_source_type`: `reference` keeps today's requirements exactly,
`structured_api` requires `expected_table` and `expected_cells` and forbids the corpus fields.
Abstention rules are untouched.

Two smaller changes fall out:

- **`becomes_answerable_at_phase: int` becomes a string label** (`"1c"`, `"2"`, `"3"`), because
  the phases are not integers.

  > **Correction, found in implementation.** An earlier draft of this section said `abs-02`
  > (*"is metformin covered under the Humana Gold Plus HMO formulary"*) was really a Phase 1-c
  > question. It is not. **The Part D formulary carries `NDC` and `RXCUI` and no drug names at
  > all**, so a question naming a drug needs a name → NDC lookup, which is openFDA or RxNorm and
  > therefore Phase 3. `abs-02` keeps abstaining and keeps its `"3"` label. The gold set instead
  > gained `str-03`, which is the same question asked in the vocabulary the table actually has
  > (*"is NDC 00002143380 on formulary 00026408…"*), and that one the mirror answers.
  >
  > The general lesson is worth keeping: **this lane answers questions phrased in the data's own
  > identifiers.** Translating a human's vocabulary into those identifiers — a drug name, a plan
  > name, a ZIP code — is Phase 3's job, and it is the honest boundary between the two.
- **`expected_cells` must be generated from the mirror, never hand-typed.** A trailing space in
  `'$4,500 '` is not something to retype by hand. A `make gold-structured` helper emits candidate
  YAML from a query; a test then re-checks every `expected_cells` value against the mirror when one
  is present, and **skips when it is not**, so `make check-all` still passes on a fresh clone.

### Scoring

- `score_question` gains a structured branch: `passed` = did not abstain **and** every
  `expected_cells` value appears in a cited row's cells. Exact match on the cell, not fuzzy overlap
  on the prose.
- **`aggregate()` must split by lane.** `recall@5` and `mrr` are computed over questions with
  `expected_doc_ids`; a structured question has none, and leaving it in the denominator would drop
  recall by construction — a metric moving because the *question set* changed is the failure this
  harness exists to avoid. Structured questions get `structured_exact_match` instead.
- **`routing_grader()`** — a new grader in `evals/grading.py` (free, deterministic, runs on every
  `make eval`): does the majority lane of the answer's citations equal `expected_source_type`? The
  runner already averages any grader-reported key and the dashboard already renders whatever a run
  reports, so this needs no change to the runner core, the storage format, the API, or the table.
  Phase 2 widens the same metric to three lanes.

### The comparison worth running

Same gold set, agent runs differing only in `--structured` / `--no-structured`. What it measures is
not "can it look a row up" — of course it can — but **whether the extra lane makes it worse at the
old questions**: reaching for SQL on a question the corpus answers is this phase's version of 1-b's
"both has to earn its place". Given the known 0.200 run-to-run spread on 30 questions
([agent.md](agent.md) §6), the honest reading is directional; the deterministic half of the picture
is the routing grader on the structured slice.

---

## 12. Tests (all offline, all in `make check-all`)

`ALLOW_MODEL_REQUESTS = False` still holds — nothing here calls a model.

- **The sandbox is a test, not a claim.** Each of `read_csv('/etc/passwd')`, `COPY ... TO`,
  `ATTACH`, `INSTALL`, `SET` asserts it raises after the lock, and the registered views assert they
  still read. This is the test most worth having: it is the file that says the blast radius of
  model-written SQL is `data/processed/`.
- **Statement guard**: multi-statement, non-`SELECT`, and unparseable inputs each raise
  `ModelRetry`; a CTE and a join pass.
- **Row cap and truncation flag**; **timeout** via a deliberately slow query against a short
  deadline (the 516 ms measurement above, as a test with margin).
- **Macros**: the five measured cases in §5, including `'Not Applicable'` → `NULL`.
- **Provenance**: a citation of an unrecorded `row_id` retries; a cell value that differs by so
  much as a trailing space retries; a valid one maps to a `Citation` with `source_type`
  `structured_api` and null `chunk_id`.
- **Fixtures are the committed samples.** `StructuredStore` takes its root as an argument (the
  `create_app(dist_dir=...)` precedent, and the default-argument bug `progress.md` records), so
  tests point it at a tmp Parquet built from `sample/*.csv` — real CMS bytes, ~2 MB, committed, and
  including the Part D shapes (`S`-contract PDP rows with blank `STATE`) that a hand-written
  fixture would not have.
- **Not-built / stale** paths: a missing partition and a doctored row count each produce their own
  error, and `routes/chat.py` returns 503 rather than answering.

---

## 13. Build order

Each step leaves the repo green and is separately reviewable.

1. **`structured/catalog.py` + `store.py` + the sandbox tests.** No agent, no API. Ends with: a
   `StructuredStore` that opens the mirror, refuses a stale one, runs a guarded query, and has the
   escape hatches proven shut.
2. **`make puf`**, plus the `catalog.json` row-count check the store uses.
3. **`agent/deps.py` refactor** — `AnswerDeps` moves, nothing else changes. A separate commit
   because it touches existing imports and should not be reviewed alongside new behaviour.
4. **The three tools + `select_tools(structured=...)` + config keys.** Registered, traced, not yet
   citable.
5. **Provenance**: `seen_rows`, `AgentRowCitation`, the validator branch, `_citations()` mapping.
   The lane is answerable end to end after this step.
6. **API + health**: `ctx.structured`, the 503, the real lane report. `make types` after, per the
   `sync-frontend` skill.
7. **Frontend slice**: `structured_api` badge live, `CitationCard` renders a row (§6's `Column:
   value` shape) rather than a snippet, `TracePanel` renders SQL as SQL. Specifics and the
   checklist are [`frontend_plan.md`](frontend_plan.md) §6, **Phase F1c**.
8. **Evals**: gold-set schema, the structured questions, `routing_grader`, the `aggregate()` split,
   the `--structured` flag and the run-record field.
9. **Measure**, and write the numbers into [`agent.md`](agent.md) §6 beside 1-b's.

---

## 14. Deferred, deliberately

Recorded so none of it gets re-argued mid-build:

- **Typed join helper tools** (§3) — until the traces earn them. Written out below, because
  "deferred" is only a decision if the next person can pick it up.
- **Typed views over the mirror** (§5) — Phase 5's comparison work is what will produce real column
  requirements.
- **Cross-source joins** (Exchange ↔ Part D). Nothing joins them: different programmes, different
  plan-identifier spaces. A question that seems to need it is two questions.
- **Aggregation, ranking, and comparison across many plans** — Phase 5. `query_structured` can
  express it; nothing in 1-c is *designed* for it, and the row-cap is set for lookups.
- **`pharmacy_network` and `pricing`** — still unfetched, still Phase 5.
- **A structured answerer with no model** (the `bm25`/`vector` equivalent). There is no
  "retrieve and stop" for SQL: writing the query *is* the model's job, so there is nothing
  deterministic to compare against. The deterministic instrument here is `routing_grader` plus
  exact cell match.
- **Multi-turn refinement** ("what about the 87% variant?") — `conversation_id` is still unused.

### 14a. The two join helpers, and what they would have done

Both are *one-call versions of a multi-step join*, pre-loaded with the key that is easy to get
wrong. Neither is built, because everything each would protect against is knowable from
`describe_table` plus one sentence in the `query_structured` description — and building one now
means guessing that the agent will fail at this particular join. The traces will say. Adding a
helper afterwards is additive; a helper written on a hunch is a design decision that never gets
revisited.

**`lookup_plan_formulary(contract_id, plan_id, segment_id?, ndc | rxcui)`** — *does this Part D
plan cover this drug, on what tier, with what utilization management?* It would walk
`plan_information` → `basic_drugs_formulary` and return tier plus the three UM flags. Three facts
the question itself does not supply:

- **The formulary table has no plan column.** It is keyed by `FORMULARY_ID`, fetched from
  `plan_information` first. 112,294 plan rows share **329 formularies**, so the same join taken
  backwards ("which plans cover this drug") multiplies by ~341.
- **A plan is three columns, not one.** `PLAN_ID` has **497 distinct values across 112,294 rows** —
  it is a sequence within a contract. The key is `CONTRACT_ID` + `PLAN_ID` + `SEGMENT_ID` (5,518
  distinct).
- **393 plans are suppressed** (`PLAN_SUPPRESSED_YN = 'Y'`) and appear in no other file. The helper
  would separate *not on this formulary* from *plan suppressed, cannot tell* — two outcomes a row
  count alone conflates, and telling someone a drug is uncovered when CMS withheld the row is the
  failure this lane exists to prevent.

**`lookup_service_area(standard_component_id, year)`** — *where is this Marketplace plan sold?* It
would walk `plan_attributes.ServiceAreaId` → `service_area` and return the counties. The traps here
are worse, because the wrong query still returns rows:

- **`ServiceAreaId` is not unique.** 8,820 rows carry **264 distinct `ServiceAreaId`s but 656
  distinct `(IssuerId, ServiceAreaId)` pairs** — `AKS001` belongs to several issuers. Joining on
  `ServiceAreaId` alone silently returns another company's counties. The key is
  `(BusinessYear, StateCode, IssuerId, ServiceAreaId)`.
- **A statewide plan lists no counties.** 252 rows carry `CoverEntireState = 'Yes'` with `County`
  and `ZipCodes` blank, so *"sold everywhere in the state"* arrives as **zero rows** — which §6
  reports as "no row for that plan". This is the only trap found here where a correct-looking query
  produces a **reversed** answer, and so the likeliest of the two to earn a helper.

**One limit that holds whether or not either is built:** `ZipCodes` is populated in **23 of 8,820
rows**, only for partial counties, and `County` is a FIPS code (`02170`), not a name. This source
answers *county* coverage, not ZIP coverage — so `plan.md`'s recurring example, *"find plans in ZIP
27360"*, is **not answerable from the vendored data**. It needs a ZIP → county crosswalk this repo
does not have, and stayed a Marketplace-API question.

**Phase 3 built exactly that**, and the crosswalk turned out to be an endpoint rather than a file:
`GET /counties/by/zip/{zip}` is folded into `find_plans` so the tool takes a ZIP and the wrapper
resolves the FIPS. It also proved the limit above is not merely a gap in the vendored data — a ZIP
can span **two** counties with different premiums, so ZIP-level plan pricing is ambiguous at the
source and not only unindexed here. See [structured-api-tools.md](structured-api-tools.md) §10e.

---

## 15. What `plan.md` keeps

`plan.md` keeps the schedule: why 1-c comes before web search, the milestone, and the capability
checklists. Everything in this document — the tool set, the guard, the macros, the row-citation
model, the partition rules, the module layout, the eval mechanics — is here, and `plan.md` links to
it rather than restating it.
