# Technical highlights

The parts of this build worth presenting: where a general problem was met with a specific,
defensible mechanism rather than a prompt instruction or a hope. Each entry states the problem, the
approach, why the obvious alternative was rejected, and what evidence exists that it works.

This is a *presentation* document. The reference docs it points at carry the full reasoning —
[agent.md](agent.md), [chunking.md](chunking.md), [lancedb.md](lancedb.md),
[relational-tool.md](relational-tool.md) — and [progress.md](progress.md) carries the history.

---

## 1. Hallucinated citations are made structurally impossible, not discouraged

### The problem

An LLM asked to answer with citations will, some fraction of the time, cite a document it never
read, or attach real-looking quotation marks to words the source never said. For a health-coverage
tool this is the **worst failure available** — worse than a wrong answer, because a fabricated
citation is what makes a wrong answer *credible*. Someone makes a financial or medical decision on
it.

The usual mitigation is to ask nicely: *"only cite passages you retrieved."* That is not a
guarantee, it is a preference, and it fails silently and unobservably.

### The approach: two mechanisms that meet in the middle

**A. The citable set is recorded by the tools, not declared by the model.**

Every tool routes its results through one method before returning them
([`agent/tools.py`](../src/health_coverage_navigator/agent/tools.py)):

```python
def remember(self, hits: list[ChunkHit]) -> list[ChunkHit]:
    """Mark hits as citable. Returns them unchanged, so it can wrap a return value."""
    for hit in hits:
        chunk = self.index.chunk(hit.chunk_id)
        if chunk is not None:
            self.seen_chunks[chunk.id] = chunk
    return hits
```

`seen_chunks` is run-scoped state on the dependency object. It accumulates *what the agent has
actually been shown* — a fact about the run, produced as a side effect of retrieval, with no input
from the model. Returning `hits` unchanged is what lets it wrap a return value, so a tool cannot
accidentally forget to call it.

**B. An output validator rejects any answer whose provenance does not hold up.**

Registered on the agent, so it runs on **every** candidate answer before anything is served
([`agent/runtime.py`](../src/health_coverage_navigator/agent/runtime.py)):

```python
agent.output_validator(_validate_grounding)
```

It refuses four things, each raising `ModelRetry` — which hands the model the reason and lets it
try again:

| Rejected | Why it matters |
|---|---|
| a `chunk_id` no tool returned this run | a fabricated source: the worst failure this tool has |
| a `snippet` not verbatim in that chunk | a real source with words put in its mouth — **worse**, because it reads as more trustworthy |
| a `[cN]` marker with no matching citation | a dangling reference the contract would reject with a 500 |
| an answer with no citations, not marked as an abstention | an assertion with nothing behind it |

The first check is the one that closes the loop:

```python
chunk = ctx.deps.seen_chunks.get(citation.chunk_id)
if chunk is None:
    raise ModelRetry(
        f"Citation {citation.id} names chunk_id {citation.chunk_id!r}, which no tool "
        f"returned in this conversation. ..."
    )
```

**There is no wording the model can choose that gets around this.** The set was assembled by code
that ran before the model spoke.

**C. Citations are then rebuilt from the corpus.** The model contributes exactly two things —
*which* chunk and *which words* — and both are checked. Title, URL, `doc_id` and `source_type` are
read off the real `Chunk`, so **an invented title has no path to the browser.**

### Details that decide whether it actually works

- **Snippets compare whitespace-normalised.** The corpora wrap mid-sentence, so a byte-exact test
  would reject genuinely verbatim quotations and trap the model in a retry loop it cannot win.
- **Retries are a bounded budget** (`agent.retries: 2`). Exhausting it raises rather than serving —
  a model that will not ground its answer must fail loudly, not degrade quietly.
- **The prompt was written *not* to duplicate any of this.** Whatever a guardrail can enforce, the
  guardrail enforces; the prompt spends its words on what only the model can do — which tool to
  reach for, when the evidence is enough, when to decline.
- **`claims` are derived, not requested.** The contract requires each claim's text to be a verbatim
  substring of the answer, and a model reproducing its own prose character-for-character is a coin
  flip. Splitting on the citation markers is exact by construction.

### The property that made it extensible

When semantic search was added in Phase 1b, **the guardrail needed no changes at all.**
`vector_search` resolves LanceDB's ids back through the same `CorpusIndex` the lexical tools use,
so a semantic hit reaches `seen_chunks` by the identical path and is citable on identical terms.

That is not a happy accident — it is the failure the design was checked against. Returning store
rows directly would have meant `remember()` silently skipping ids it could not resolve, after which
every vector citation would fail grounding, and the error would surface *two layers away* looking
like a model problem. The mechanism is only cheap to extend because the extension point is
"resolve to a real `Chunk`", not "be a particular retriever".

### Evidence

- **`groundedness` and `citation_resolution` read 1.000 on every agent run ever recorded.** They
  are measured anyway, and the reason is worth stating: *a number below 1.0 would be a bug in the
  validator, not a score to improve.* A guardrail nobody checks is one that has already stopped
  working.
- Guardrail tests are written as **"what would a model do wrong"** — citing an unretrieved chunk,
  paraphrasing a quotation, leaving a dangling marker, answering with no sources — and they assert
  the retry **message**, not just the rejection. A retry the model cannot act on is a retry wasted.
- `make smoke-abstain` runs the whole path live against an out-of-corpus question, where an invented
  citation would be the most damaging possible output.

### Why it presents well

It converts a **probabilistic worry into a structural property**, and it does so in about forty
lines. The claim is not "the model rarely hallucinates citations" — it is "an ungrounded answer
cannot be served, and here is the code path that makes that true." It also demonstrates the
distinction worth having an opinion about: *prompts express intent; code enforces invariants*, and
knowing which to reach for is most of the craft in building on top of an LLM.

Full design rationale: [agent.md §4](agent.md).

---

## 2. A test suite that *cannot* spend money — and the guard that had to be repaired to keep it that way

### The problem

This repo runs on a hard invariant: **`make check-all` never reaches a model provider.** It needs no
API key, costs nothing, and cannot fail because a provider is having a bad afternoon. That is what
makes it safe to run constantly, safe to hand to a stranger cloning the repo, and safe to trust when
it is green.

The failure mode being defended against is unusually nasty, and it is worth saying plainly when
presenting this: **a test that accidentally calls a real API does not fail. It passes.** It is
slower, it costs a fraction of a cent, it quietly requires a credential — and every signal you have
says everything is fine. You find out from a bill, or from a CI box that has no key, or never.

### What went wrong, and why it is instructive

Phase 1a enforced the invariant with one line:

```python
monkeypatch.setattr(pydantic_ai.models, "ALLOW_MODEL_REQUESTS", False)
```

Phase 1b added embeddings — and **silently punched a hole straight through it.** That flag is
*PydanticAI's* switch, governing PydanticAI's model requests. The new embedder calls the OpenAI SDK
directly:

```python
client = AsyncOpenAI(api_key=get_secrets().openai_api_key.get_secret_value(), ...)
response = await client.embeddings.create(model=model, input=list(texts))
```

Nothing in that path consults `ALLOW_MODEL_REQUESTS`. The suite would have gone out to the network
on the developer's own key and stayed green.

**The transferable lesson: a safety flag borrowed from a library only covers that library's surface
area.** The invariant was "no test reaches a provider"; the mechanism only ever implemented "no test
reaches a provider *through PydanticAI*". Those were the same sentence right up until they weren't,
and nothing announced the divergence. Any new dependency that can open a socket re-opens this
question.

### The approach: a negative guard plus a positive substitute

**A. The guard — refuse the live factory, suite-wide.**

```python
@pytest.fixture(autouse=True)
def _no_live_embeddings(monkeypatch: pytest.MonkeyPatch) -> None:
    import health_coverage_navigator.vectors.embedder as embedder_module

    def refuse(*_args, **_kwargs):
        raise AssertionError(
            "a test tried to build the live OpenAI embedder. Use the `fake_embedder` fixture; "
            "the suite must not reach a provider."
        )

    monkeypatch.setattr(embedder_module, "openai_embedder", refuse)
```

Patched at **our factory**, not at `AsyncOpenAI`, deliberately: the failure then names the seam the
test should have used, instead of surfacing as an authentication error from somewhere inside a
vendor package. `autouse` so it applies without being asked for, and via `monkeypatch` so it is
restored per test.

**B. The subtlety that makes or breaks it — and that forced a change in production code.**

Patching a module attribute only works if consumers **look the name up at call time**. Two modules
originally did this:

```python
from health_coverage_navigator.vectors.embedder import openai_embedder   # binds at import
```

That copies the reference into the consumer's namespace *before* any fixture runs. The patch would
have replaced a name **nobody used**, the guard would have reported nothing, and the hole would have
stayed open behind a passing suite — a guard that appears to work is worse than no guard. Both
consumers now import the module and resolve through it:

```python
from health_coverage_navigator.vectors import embedder as embedder_module
...
embedder = embedder_module.openai_embedder(config.embedding_model, config.dimensions)
```

`openai_embedder`'s own docstring records the requirement, so the next person does not "tidy" the
import back into the shorter form. `vectors/__main__.py` is a deliberate exception — it is the CLI
whose entire job is to spend that money, and no test imports it.

**C. The substitute — a deterministic fake at the seam we own.**

A guard alone only converts silent spending into loud failure; something has to fill the gap.
`fake_embed` hashes the text into a unit vector:

```python
digest = hashlib.sha256(text.encode("utf-8")).digest()
raw = [(digest[i % len(digest)] / 255.0) - 0.5 for i in range(FAKE_DIM)]
norm = math.sqrt(sum(x * x for x in raw)) or 1.0
out.append([x / norm for x in raw])
```

Its docstring is emphatic about what it is **not**:

> Not semantic, and deliberately not pretending to be. Two paraphrases get unrelated vectors, so
> this can never stand in for a judgement about whether vector search *works*.

That honesty is the point. The fake gives exactly two properties, and they are the only two the
plumbing needs: an exact-text query retrieves its own chunk at similarity 1.0, and ordering is
stable across runs. Retrieval *quality* is a different question, answered by
`make eval-retrieval-vector` against the real model and the real corpus — never by a unit test.

### Where the line between fake and real was drawn

The fake stops at the embedder. **The vector store in the tests is a real LanceDB database**, built
in `tmp_path`, not a stub object.

The reasoning generalises: fake the thing that *costs money or leaves the machine*; keep the thing
you are actually testing. The failure modes worth catching here are LanceDB's own — the fixed-width
Arrow schema, whether `.where()` filters before or after the top-k cut, the name and direction of
the `_distance` column. A hand-written fake `VectorIndex` would assert only that our mock behaves
like our mock, which is a test that can never fail for a real reason.

### Evidence

- **248 tests run with no API key, no network, and no billing**, in about ten seconds.
- The hole was real and is now closed in both directions: the guard fails loudly on the live
  factory, and every consumer resolves through the module so the guard actually binds.
- A companion gap surfaced from the same instinct and was fixed alongside it: under the shipped
  configuration the agent reaches for keyword search first and succeeds, so **every default
  `make smoke` run exercised only the lexical path** — `vector_search` could have broken live with
  all nine checks green. `make smoke --toolset vector` now covers it.

### Why it presents well

It is a concrete, slightly uncomfortable story rather than a claim of discipline: *we had an
invariant, we added a feature, and the invariant quietly stopped holding — here is how it was caught
and what now keeps it true.* It carries three ideas worth arguing for:

1. **Safety mechanisms inherit a library's scope, not your intent.** Re-derive them whenever a new
   dependency can reach the network.
2. **Python's import style is a testability decision**, not a formatting preference.
   `from x import y` freezes a reference; `import x` keeps a seam. That difference decided whether
   this guard worked at all.
3. **The most dangerous test failures are the ones that pass.** Anything whose failure mode is a
   green suite deserves a mechanism, not a convention.

---

## 3. "Missing" and "wrong" are different failures, and get opposite treatment

### The problem

The vector store is a **derived artifact**: 40 MB of float32 computed from the committed corpus by a
paid API call, and therefore git-ignored. Every derived artifact can fail in two ways, and
collapsing them into one error type is a mistake that costs you exactly when it matters:

| | **Missing** | **Stale** |
|---|---|---|
| What happened | never built here | built against *different* chunks or a different model |
| How it presents | nothing works, loudly | **everything works, plausibly** |
| Is it expected? | yes — fresh clone, and building costs money | no |
| Cost of getting it wrong | an obvious error message | citations pointing at passages that no longer exist |

The second row is the whole argument. A missing store is *visibly* unbuilt — you cannot fail to
notice. A stale store answers every question with confident prose and real-looking citations that
resolve to the wrong text, or to nothing.

**The severity of a failure is not how broken the system is. It is how detectable the wrongness
is.** A system that is obviously down is safer than one that is quietly wrong, and for a
health-coverage tool a quietly wrong citation is the failure the entire grounding design (§1) exists
to prevent — so it must not be reintroduced one layer lower, in the storage.

### Why staleness is even possible

Retrieval returns `chunk_id`s, and **a chunk id is only meaningful against the chunk set that
produced it.** Re-chunking the corpus — a parameter tweak, a new heading rule — renumbers
everything. A store built before that change hands back ids the in-memory corpus cannot resolve.

Left undetected, the damage lands two layers away and wearing a disguise: `remember()` skips ids it
cannot look up, so the grounding validator then rejects every citation, burns the retry budget, and
the run dies as `UnexpectedModelBehavior` — which reads like a *model* problem. You would debug the
prompt for an hour before suspecting the store.

### The approach

**A. Two exception types, because there are two failures.**

```python
class VectorsNotBuiltError(RuntimeError): ...   # ordinary, expected
class VectorsStaleError(RuntimeError): ...      # never ordinary
```

`VectorsNotBuiltError` deliberately mirrors Phase 1a's `ChunksNotBuiltError`, so the two
git-ignored artifacts fail the same way and both name the command that fixes them.

**B. A committed manifest is what makes staleness detectable at all.**

The store is ignored; `data/processed/vectors_meta.json` is committed, and records the chunk
snapshot ids, the embedding model, the dimensionality and the distance metric. Without it, "is this
store current?" is simply not a question anyone could ask. Same bargain the chunker already made:
**the artifact is ignored, the manifest is committed, and reproducibility is *checked* rather than
asserted.**

`VectorIndex.open` compares before answering anything, and the message names the fix:

```python
if manifest.chunker_snapshot_id != expected:
    raise VectorsStaleError(
        f"the vector store was built against chunks {manifest.chunker_snapshot_id} but the "
        f"corpus is now {expected}. Every stored chunk id refers to the old chunking, so "
        f"citations would resolve against passages that no longer exist. Run `make embed`."
    )
```

**C. The asymmetry, at the point of loading.**

```python
try:
    return await VectorIndex.open(embedder)
except VectorsNotBuiltError as exc:
    logger.warning("semantic search unavailable: %s", exc)
    return None
```

One is caught; the other is conspicuously **not**. `VectorsStaleError` propagates out of the
lifespan and **stops the server booting.** Refusing to start is a strange thing to want, until you
price the alternative: a server that starts and serves wrong citations. Failing at boot with a
message naming `make embed` is the cheap version of that discovery.

### The same two errors, a different policy per context

This is the part worth presenting, because it shows the rule is *derived* rather than copied around.
Three call sites, three policies, each following from what wrongness would cost **there**:

| Context | Missing | Stale | Why |
|---|---|---|---|
| **API startup** | log, degrade to `None` | **refuse to boot** | the app must never serve a wrong citation |
| **Answering a request** | 503 naming `make embed` | (never reached — boot failed) | no stub fallback, and no silent downgrade to lexical |
| **Eval runner** | **`SystemExit`** | **`SystemExit`** | *both* are fatal here |

The eval runner is the interesting row. It treats a *missing* store as fatal where the API treats it
as ordinary — because silently falling back to lexical would produce a plausible score for a
configuration nobody asked for, while the run record would name the toolset that was *requested*.
That is the one failure capable of corrupting the measurement the whole phase exists to make.

The request path applies the same reasoning to a subtler temptation: `_unavailable()` refuses to
quietly degrade `both` to lexical-only. The answer would be perfectly real — just measured against a
retrieval setup nobody chose, with nothing in the response saying so.

**`make embed-check`** is the offline half: it compares the snapshot id and row count without
re-embedding, so drift is caught in the same breath as `make chunk-check` rather than at boot.

### Evidence

- Three tests drive the refusal directly — different chunks, different model, and manifest-without-store
  (the realistic fresh-clone shape, which must report *not built* rather than *stale*).
- The snapshot id is pinned against **every** input that could change a vector — model,
  dimensionality, distance metric, chunk snapshots — plus a test that it does not depend on dict
  ordering, since it reaches a committed file.
- It fired in real use during development: `make embed-check` and a 503 naming `make embed` are what
  the app reported before the store was built, rather than anything mysterious.

### Why it presents well

It is a small amount of code carrying an opinion most systems get wrong by default: **degrade when
the system is visibly reduced, refuse when it would be invisibly wrong.** Most error handling
collapses toward one policy — usually "log it and carry on", which is precisely backwards for the
dangerous case.

Two ideas transfer beyond this repo:

1. **Classify failures by detectability, not severity.** "Loudly broken" is a *better* state than
   "quietly wrong", and error types should encode that judgement rather than leave it to each caller.
2. **A derived artifact needs a committed description of what it was derived from.** Otherwise
   staleness is not a bug you can catch — it is a bug you cannot even express.

---

## 4. Letting a model write SQL — safely, successfully, and with every number citable

### The problem

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

### A. Connecting: DuckDB over Parquet, with nothing loaded

The mirrors are registered as views and read where they lie
([`structured/store.py`](../src/health_coverage_navigator/structured/store.py)):

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

### B. Three tools, each answering a different question

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

### C. The guard: two layers that do not trust each other

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

### D. Making the model's SQL succeed, not just fail safely

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

### E. Citing a row — and why a fabricated one cannot be served

§1 made hallucinated *passage* citations structurally impossible. A row is a different kind of
evidence — quoted by cell, not by sentence — so the same mechanism had to be extended without being
weakened. It ends up **stricter**, not looser.

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
| a `row_id` no query returned this run | a fabricated row — the relational lane's version of §1's worst failure |
| a column the row does not have | a real row with an invented field |
| a value for a cell that is `NULL` | the query produced no value there; reporting one invents it |
| a cell value that differs **by a single byte** | a tidied figure is a different claim from the published one |

That last row is the interesting one, because it is where this validator and §1's part ways:

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

### Evidence

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

### Why it presents well

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

Full design rationale: [relational-tool.md](relational-tool.md).
