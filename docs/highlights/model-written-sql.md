# Letting a model write SQL — safely, successfully, and with every number citable

One of the [technical highlights](../technical_highlights.md).

## The problem

Two thirds of what people actually ask a coverage tool is not answerable from prose. *"What is a
deductible"* is a reference question; *"what is the deductible on plan 38344AK1060002"* is a
**row**, and no amount of retrieval over HealthCare.gov will produce it. Worse, retrieval will
produce something that *reads* like an answer — a passage defining the word — which is the failure
mode that makes this lane worth building rather than skipping.

CMS publishes the underlying facts as public-use files: 10 tables, **2.96 million rows** across ACA
marketplace plans and Medicare Part D. The design question is how an agent reaches them.

The obvious approach is a typed function per question — `get_deductible(plan_id, year)`. It was
rejected because the questions are not knowable in advance: deductibles, out-of-pocket maximums,
metal levels, formulary tiers, utilization-management flags, service areas, premiums, and every
combination of those with a filter. Each new question would be a code change. **The data is
relational, so the general tool is SQL, and the guard is code.**

That decision immediately raises three questions worth answering separately: what can the model
reach, what can it break, and how do you prove the number it reports came from a row that exists.

## A. Connecting: DuckDB over Parquet, with nothing loaded

The mirrors are registered as views and read where they lie
([`structured/store.py`](../../src/health_coverage_navigator/structured/store.py)):

```python
con.execute(
    f"CREATE VIEW {table.view} AS SELECT * FROM read_parquet('{_sql_literal(table.path)}')"
)
```

Measured against the real mirrors: **22 ms** to open a connection and register all ten tables,
**3–16 ms** for a representative lookup, **77 MB** RSS with everything registered. There is no
server, no import step, no second copy of the data, and no daemon to run beside the API — which is
what makes this a *feature of the app* rather than a piece of infrastructure someone has to operate.

**The mirrors are deliberately lossless: every column is `VARCHAR`.** That is not laziness, it is
the whole correctness story. This data ships with its formatting attached — a deductible is stored
as `'$4,500 '`, dollar sign, comma and trailing space — and an empty string sits beside the literal
`'Not Applicable'` meaning something different from it. Typing the columns on import would collapse
those distinctions before anyone could ask about them, in the one lane where the difference *is* the
answer. Two macros exist for the arithmetic instead:

```python
"CREATE MACRO to_usd(x) AS TRY_CAST(NULLIF(regexp_replace(trim(CAST(x AS VARCHAR)), '[$,]', '', 'g'), '') AS DOUBLE)"
```

`TRY_CAST` rather than `CAST`, so one unparseable value drops out of a comparison instead of failing
a query that is 99% right. The stated cost — `''` and `'Not Applicable'` both become `NULL` — is why
the tool description tells the model to use the macros to **filter and order**, and to quote the raw
cell when **reporting** a value.

## B. Three tools, each answering a different question

| Tool | Purpose | Why it exists as its own call |
|---|---|---|
| `list_tables` | what plan data exists, and **for which years** | Names each table twice: `table` and the `view` to write in SQL. Also reports `available_partitions` — every plan year on this machine — which is what turns a question about an unvendored year into an abstention rather than a confident answer from the wrong year |
| `describe_table` | what the columns are, **by their values** | Every column is `VARCHAR`, so a type listing says nothing. This returns example values, approximate cardinality, a full distribution for low-cardinality columns, and a macro hint when values look like money or a percentage |
| `query_structured` | run one guarded `SELECT`, get back citable rows | The general tool. Joins, CTEs, `GROUP BY` and aggregates all work |

`describe_table` is the one that would be easy to leave out and expensive to skip. It costs one call
and prevents the two failures that waste several: a column name that does not exist, and a filter
written against a value formatted differently than assumed. It also takes a `contains` filter,
because `plan_attributes` has **151 columns, 36 of which are different flavours of out-of-pocket
maximum** — `contains="MOOP"` is how you find the right one instead of guessing a name.

## C. The guard: two layers that do not trust each other

Model-written SQL reaches a real query engine, so neither layer assumes the other held.

**Layer 1 — statement shape, before DuckDB executes a character:**

```python
statements = duckdb.extract_statements(sql)
if len(statements) != 1:
    raise QueryRejected(f"send exactly one statement; that was {len(statements)}. ...")
if statements[0].type != duckdb.StatementType.SELECT:
    raise QueryRejected(f"only SELECT is allowed here, and that is a {statements[0].type.name} ...")
```

Parsed by DuckDB itself rather than by a regular expression over the SQL text. That distinction
matters: a keyword blocklist is defeated by comments, casing, whitespace and string literals, while
a parser reports what the statement *is*. It rejects `COPY`, `ATTACH`, `INSTALL`, `CREATE`,
`UPDATE`, and anything hiding behind a second statement.

**Layer 2 — the connection itself is sandboxed, and the order is load-bearing:**

```python
# Views first, then the sandbox.
con.execute(f"SET allowed_directories=['{_sql_literal(root.resolve())}']")
con.execute("SET enable_external_access=false")
con.execute("SET lock_configuration=true")
```

Views are lazy, so they must be created before the lock; and `allowed_directories` does nothing
while external access is still enabled. After those three lines, the blast radius of any SQL that
somehow got past layer 1 is `data/processed/`, read-only — and `lock_configuration` means a query
cannot undo any of it.

Two further ceilings make a *bad* query cheap rather than impossible:

```python
cursor = self._con.cursor()
timer = threading.Timer(self._config.query_timeout_s, cursor.interrupt)
```

Every statement is wrapped in a row cap the model cannot forget to write (`LIMIT cap + 1`, the `+1`
being how truncation is detected), and each runs on **its own cursor** under a `threading.Timer`
armed on `interrupt()` — because DuckDB has no statement timeout. The cursor-per-query is doing
double duty: it makes concurrent requests safe on one connection, and it means an interrupt cancels
*that* query rather than taking down every other in-flight request.

The last guard is not about safety but about a domain bug, and it is the one most likely to matter
in practice. CMS keeps many plan years live, and quietly answering 2026 for a 2025 question is this
domain's most common wrong answer — one that looks exactly as authoritative as the right one.
Because every view name carries its partition (`exchange_puf.plan_attributes_2026`), enforcing the
pinned year is a set-membership test rather than SQL parsing:

```python
mentioned = [table for view, table in self._by_view.items() if _mentions(sql, view)]
wrong = [t for t in mentioned if partitions.get(t.source) != t.partition]
```

| Layer | Catches | Fails how |
|---|---|---|
| Statement type | writes, `ATTACH`, `INSTALL`, `COPY`, stacked statements | `ModelRetry` — the model rewrites the query |
| Connection sandbox | filesystem reads/writes, extensions, config changes | a `duckdb.Error` from the engine, surfaced to the model as a retry — a path layer 1 is not supposed to leave reachable |
| Row cap + timeout | an accidental cross join, an unfiltered scan | truncated result, or a retry naming the deadline |
| Partition pin | a number from the wrong plan year | `ModelRetry` naming the right table |

## D. Making the model's SQL succeed, not just fail safely

A guard that rejects everything is not a working lane. Four mechanisms raise the hit rate, and none
of them is "ask the model to be careful":

**1. Errors are returned as corrections, carrying DuckDB's own message.**

```python
except QueryRejected as exc:
    deps._record("query_structured", {"sql": sql, ...}, f"rejected: {exc}", ...)
    raise ModelRetry(str(exc)) from exc
```

DuckDB's binder error already names the near-miss columns — *"Candidate bindings:
TEHBDedInnTier1Individual, …"* — which is better than anything hand-written, so it is passed through
verbatim rather than replaced with a generic message. **A rejected query is a normal event in this
lane, not an error condition**, and it is recorded in the trace before the retry so the reader can
see the correction happen.

**2. `describe_table` returns values, distributions and a macro hint** — the orientation that
prevents the bad query being written in the first place. Three queries, ~70 ms on the widest table.

**3. Per-table correctness notes, surfaced by `describe_table`.** Each is a documented way for a
reasonable-looking query to be confidently wrong:

> `STATE` and `COUNTY_CODE` are populated only for Medicare Advantage rows. Every standalone PDP is
> located by `PDP_REGION_CODE` instead, so filtering this table by `STATE` silently drops exactly the
> plan type a "what does my drug plan cover" question is usually about.

> One plan is many rows: each CSR variant of a plan is its own row, with its own deductibles and MOOP.
> Filtering by `StandardComponentId` alone returns all of them, and they disagree.

**4. The prompt changes shape when the lane is registered.** Phase 1a's preamble told the model
*"You have no access to anything else. No live plan data, no drug formularies…"* — a sentence that
becomes a **lie** the moment these tools exist, and whose failure mode is the expensive one:
abstaining on exactly the questions the phase was built to answer. Four fragments now vary by
configuration (what sources exist, routing guidance, how a citation is formed, what is out of
reach), and the routing rule is the new instruction:

> **Route by the kind of question, not by the topic.** "What is a deductible", "does Medicare cover X
> in general" — search the reference corpus. "What is the deductible on plan X", "is this drug on
> that formulary" — those are rows, not prose. Searching the corpus for a plan-specific fact returns
> a passage that sounds like an answer and is not one.

**5. The loop ceilings were raised by measurement, not by taste.** A reference question spends two
or three model calls; a plan question spends `list_tables` → `describe_table` (often twice) →
`query_structured` → a corrected query → the answer. The first structured eval run lost `str-01`
outright to `UsageLimitExceeded: the next request would exceed the request_limit of 8` — a working
capability recorded as a failed question, which is the worst way for a ceiling to be wrong.
`request_limit` went 8 → 12 and `tool_calls_limit` 12 → 16, still far below the runaway loop they
exist to stop.

## E. Citing a row — and why a fabricated one cannot be served

[Grounded citations](grounded-citations.md) made hallucinated *passage* citations structurally
impossible. A row is a different kind of evidence — quoted by cell, not by sentence — so the same
mechanism had to be extended without being weakened. It ends up **stricter**, not looser.

**The citable set is recorded by the tool, exactly as `seen_chunks` is.** Every row a query returns
is remembered whole:

```python
def remember_rows(self, rows: list[Row]) -> None:
    """Rows are recorded whole, not by identifier: the validator has to check the *values* a
    citation quotes, and a row id alone would let a model cite a real row with invented cells."""
    for row in rows:
        self.seen_rows[row.row_id] = row
```

**Each row carries an id that names the query that produced it** —
`exchange_puf/2026/plan_attributes#q2.1` — because a projection or an aggregate has no key of its
own. The trace shows the SQL beside it, so a reader can see precisely which statement produced the
number they are looking at.

**The output validator refuses four things**, each raising `ModelRetry`:

| Rejected | Why |
|---|---|
| a `row_id` no query returned this run | a fabricated row — the relational lane's version of the reference lane's worst failure |
| a column the row does not have | a real row with an invented field |
| a value for a cell that is `NULL` | the query produced no value there; reporting one invents it |
| a cell value that differs **by a single byte** | a tidied figure is a different claim from the published one |

That last row is the interesting one, because it is where this validator and the passage one part ways:

```python
# A passage quotation is normalised for whitespace because the corpora wrap mid-sentence…
# **A cell has no wrapping to survive**, and its whitespace is data — '$4,500 ' carries a
# trailing space that distinguishes the published value from a tidied one — so this
# comparison is byte-exact, and deliberately stricter than the chunk path.
if value != actual:
    raise ModelRetry(f"Citation {citation.id} gives {column!r} as {value!r}, but the row holds "
                     f"{actual!r}. Copy the cell exactly … your answer text may present it more readably.")
```

The prose may read `$4,500`; the citation must carry `'$4,500 '`. **The answer is allowed to be
readable, the evidence is not allowed to be edited.**

**And the displayed citation is rebuilt from the `Row`, never from the model.** The model
contributes exactly two things — *which* row and *which* cells — and both are checked before this
runs. Title, URL and source type are derived:

```python
source_type="structured_api",
title=f"{source_label(row.source)} · {row.view.rsplit('/', 1)[-1]} ({row.partition})",
snippet="\n".join(f"{column}: {'NULL' if value is None else value}" for column, value in ...),
```

The cells render one per line, and the frontend sets `whitespace-pre` on the value — because a
trailing space defended through three layers of Python and then dropped by CSS is a citation that
has quietly stopped being the evidence it claims to be.

One more rule lives in the prompt rather than the code, because only the model can apply it: **zero
rows is a finding, not a failure.** An empty result means this table holds no such row — which is
different from "the plan does not cover it" and different from a cell reading `'Not Applicable'`.
Turning "no row" into "not covered" is a claim the query never checked.

## Evidence

- **The latest structured eval run (`run_2026-08-19_9`, 40 questions, judged):**
  `structured_exact_match` **1.000** — every cell the gold set demands, byte for byte —
  `routing_correct` **1.000**, `citation_resolution` **1.000**, `groundedness` **1.000**,
  `abstention_accuracy` **1.000**, `answer_correctness` 0.803.
- **The lane costs nothing on the questions that were already answerable.** The paired
  reference-only run (`run_2026-08-19_8`, `--no-structured`) scores `recall@5` 0.767 against the
  structured run's 0.733 — one question apart on a 30-question denominator — with
  `abstention_accuracy` 1.000 in both. Adding a second lane did not make the agent worse at the
  first one, which is the risk that comparison exists to detect.
- **`test_the_sandbox_is_shut`** drives `read_csv('/etc/passwd')`, `COPY … TO`, `ATTACH`, `INSTALL`
  and two `SET`s **directly at the connection**, bypassing layer 1, and asserts every one raises.
  Its companion asserts the registered views still read after the lock — locking must not lock us
  out. The whole relational suite runs offline against a fixture mirror built from committed CMS
  sample slices, so it costs nothing and needs no download.
- **The timeout was measured, not assumed**: a deliberately slow aggregate was interrupted at 516 ms
  against a 500 ms deadline.
- **`expected_cells` in the gold set is generated, never typed.**
  `python -m health_coverage_navigator.evals.structured_gold` runs a real query and emits the YAML,
  because a value tidied by hand on its way into the eval asserts something the source does not say —
  and would grade the agent against a fiction in the one lane whose point is byte-exact evidence.
- **A metric bug caught by the same instinct.** The first structured run reported
  `citation_resolution` 0.889, which reads exactly like a fabrication regression. It was the *grader*:
  a row citation has no `chunk_id` by design, and the old code counted that as unresolvable — a metric
  punishing a capability for existing. `citation_resolution` now spans both lanes and `groundedness`
  covers passages only, each checking what its shape can actually be checked against.

## Why it presents well

It is the part of the system where "give the LLM a tool" stops being a slogan and has to be
engineered. Three ideas transfer:

1. **Generality belongs in the tool; safety belongs in the code around it.** The alternative — a typed
   function per question — buys safety by making the capability grow linearly with the questions
   anyone thinks to ask. SQL plus a parser-backed guard scales to questions nobody has asked yet.
2. **Error messages are prompt surface.** DuckDB's binder error, passed through verbatim as a
   `ModelRetry`, is a better retry instruction than anything written by hand — the system's most
   useful "prompt engineering" here was deciding *not* to rewrite an error.
3. **Provenance has to follow the shape of the evidence.** A row is not a passage: it is quoted by
   cell, its whitespace is data, and "exactly" therefore means something stricter. Extending a
   guardrail is not copying it — it is asking what "grounded" means one lane over.

Full design rationale: [relational-tool.md](../relational-tool.md).
