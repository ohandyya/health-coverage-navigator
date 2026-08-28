# The agent

One PydanticAI `Agent`, grown a toolset at a time. Phase 1a built it with a full-text toolset and
**no database of any kind**; Phase 1b registered `vector_search` alongside those tools and made
*which* tools it sees a per-run choice; Phase 1-c added a second **lane** — three tools over the
vendored CMS plan data, where the answer is a row rather than a passage. Same agent, same output
contract, throughout. Written alongside the code rather than before it, unlike
[chunking.md](chunking.md) and [lancedb.md](lancedb.md) — the decisions here needed a running loop
to make.

The relational lane has its own design document, [relational-tool.md](relational-tool.md), which
owns the engine, the SQL guard and the row-citation model. This file covers what changed *in the
agent* to hold two lanes.

Code: `src/health_coverage_navigator/agent/`, with semantic retrieval in `vectors/` and the
relational lane in `structured/`. Ask it something with `make dev`; measure it with `make eval`.

---

## 1. Shape

```
                        ┌─▶ CorpusIndex ──▶ BM25 over chunks.jsonl ──┐
question ──▶ Agent ──▶ tools                                        ├─▶ deps.seen_chunks
               │        ├─▶ VectorIndex ──▶ LanceDB ────────────────┘
               │        │
               │        └─▶ StructuredStore ─▶ DuckDB over Parquet ──▶ deps.seen_rows
               ▼
          AgentAnswer ──▶ output validator ──▶ ChatResponse
       (abstained,          (rejects an          (citations rebuilt from
        answer, citations)   ungrounded answer)   the real chunk or row)
```

Both *retrieval* paths converge on the same `Chunk`, which is what lets one grounding rule cover
them and one citation shape serve both. The relational lane deliberately does **not** converge
there — a row is not a chunk and pretending otherwise would mean inventing a passage — so it gets
its own citable set, its own validator branch, and the same rule: cite only what a tool returned,
quote it exactly.

| module | holds |
|---|---|
| `bm25.py` | the ranking formula and an inverted index. Standard library only |
| `index.py` | `CorpusIndex` — the chunks and the four lexical primitives. No `pydantic_ai` import |
| `models.py` | what the *model* sees: `ChunkHit` in, `AgentAnswer` out |
| `prompt.py` | `system_prompt(toolset, structured, web, live, marketplace)` — the grounding rule, plus instructions composed per configuration (§3a) |
| `deps.py` | `AnswerDeps` — the run-scoped trace and the two citable sets |
| `tools.py` | the five reference-lane tools and `select_tools` |
| `structured_tools.py` | the three relational-lane tools |
| `runtime.py` | `build_agent()`, the grounding validator, `stream_answer()`, `answer_question()` |
| `../vectors/` | `VectorIndex` and the embedder. No `pydantic_ai` import either |
| `../structured/` | `StructuredStore` and the SQL guard. No `pydantic_ai` import either |

`index.py`, `vectors/` and `structured/` sit below the tool modules deliberately: retrieval and
querying are then testable with no model and no agent, which is what makes `make eval-retrieval`,
`make eval-retrieval-vector` and the whole of `tests/test_structured.py` possible.

**`AnswerDeps` lives in its own module since Phase 1-c**, and not for tidiness: both tool modules
need the type and `tools.py` needs the structured tools to build `select_tools`, so leaving it in
`tools.py` closes an import cycle.

## 2. Nine tools, not one `retrieve()`

| tool | for | when registered |
|---|---|---|
| `search_corpus(query, k, source?)` | ranked retrieval by **words** | toolset lexical, both |
| `grep_corpus(pattern, source?, ...)` | an exact string: an NCD number, a statutory phrase | toolset lexical, both |
| `vector_search(query, k, source?)` | ranked retrieval by **meaning** | toolset vector, both |
| `get_chunk(chunk_id, before, after)` | widen a hit that landed mid-definition | every |
| `list_documents(source?, limit)` | what is in the corpus at all | every |
| `list_tables()` | what plan data exists, and for which years | `structured_tools` |
| `describe_table(table, contains?)` | a table's columns **and their real values** | `structured_tools` |
| `query_structured(sql, limit?)` | one guarded read-only `SELECT` | `structured_tools` |

The three relational tools are a *lane*, not more retrieval: they answer "what is the deductible on
plan X", which no amount of better search over prose can. Why three and not a typed function per
question, and what the guard around model-written SQL is:
[relational-tool.md](relational-tool.md) §3–4.

A single `retrieve(query)` would hide the search strategy inside a ranking function, which is the
part of the exercise worth doing. With narrow tools a bad query and the recovery from it are both
visible in the trace, and Phase 2's lane routing becomes a change of degree — the agent is already
choosing between tools before a second lane exists. The cost is accepted: more surface to get
wrong.

**Each tool docstring is a prompt.** The model reads it as the tool description, which is why they
are longer than an ordinary internal docstring, name their siblings, and say what a bad result
means. `search_corpus`'s says outright that matching is on words and not meaning, and to query with
the terms the *source* would use — see §6 for the measurement behind that.

Two ceilings, both in `index.py`: `MAX_HITS = 10` (each hit can be 1,200 characters, so an
unbounded `k` is a context-window problem before it is a latency one) and
`MAX_PATTERN_CHARS = 200` (an arbitrary regex over 6,722 chunks, with no timeout available in
Python's engine — a length cap does not make catastrophic backtracking impossible, it removes the
room to construct one by accident).

## 3. What the agent can see is a per-run choice — on five independent axes

`config.Toolset` is `lexical | vector | both`; `agent.structured_tools`, `agent.web_tools` and
`agent.live_tools` are booleans, and `marketplace` is a fifth axis that is **not** a config key —
it records whether a CMS credential exists on this machine, which `config.yaml` must not encode
(docs/configuration.md). `tools.select_tools(toolset, structured, web, live, marketplace)` turns
them into the registered list and
`prompt.system_prompt(toolset, structured, web, live, marketplace)` into the matching instructions.
`config.yaml` sets what the app ships (`both`, every lane on); `--toolset`, `--structured` /
`--no-structured`, `--web` / `--no-web` and `--live` / `--no-live` override them for one eval run,
and the run record carries them. That is what makes each comparison **one runner with a flag rather
than several code paths** (docs/plan.md §1b).

**Booleans rather than more `Toolset` values**, because they select different things: `toolset`
picks how the *reference lane* is searched, while the others pick whether another *lane* — or, for
`live`, another *half* of a lane — exists at all. Folding them together would make dozens of
combinations, most of them meaningless, and would redefine the three names Phase 1b's measurement is
already recorded under.

Three things about it are load-bearing rather than incidental:

**The navigation tools are in every configuration.** `get_chunk` and `list_documents` widen and
orient; they do not rank. Dropping them from the vector-only run would fold "lost the ability to
widen a hit" into the lexical-vs-vector number, and nothing downstream could separate the two
effects again. `grep_corpus` *is* retrieval by content, so it travels with the lexical set.

**The prompt composes with every axis**, which is enough of a design to have its own subsection —
§3a below.

**`_build_agent` caches on `(model, toolset, structured, web, live, marketplace)`.** Two agents that differ in what
they can do must not share one cached object, and every default is resolved *before* the lookup —
docs/progress.md records the Phase 1a bug where `build_agent()` and `build_agent(None)` were two
cache keys and an `override` silently went to the real provider. Each new defaulted argument is
another chance at exactly that, and **every phase since has taken it.** Phase 1-c: adding the third
key made every agent test build a *structured* agent while the run under test built a reference-only
one, so the override applied to an object nobody used and the run tried to reach OpenAI. Phase 2:
adding the fourth key broke three tests that pinned `structured=` explicitly and let `web` default,
producing the identical mismatch. **Phase 3 added a fifth *and* a sixth key and took the bug on the
sixth**, because `live` is derived from *either* live client while `marketplace` is derived from
one. The suite-wide `ALLOW_MODEL_REQUESTS = False` is what turned a would-be bill into a red test
every time.

`tests/conftest.py`'s `AgentKit._agent` must derive the key the same way `stream_answer` does, line
for line, and
`tests/test_web_agent.py::test_each_lane_configuration_is_a_separate_cached_agent` asserts the
property directly — so a new axis fails a test instead of a bill. **Known deviation as of Phase 3:**
`AgentKit._agent` accepts `marketplace` but its three call sites do not pass it, and `AgentKit.stream`
does not forward it to `stream_answer` either. Harmless only because no test currently drives a
Marketplace client through the kit; the first one that does will hit exactly the mismatch above.

### 3a. The prompt composes with every axis

Three toolsets × five lane booleans is **48 configurations** the same code can take, and the eval
sweep runs paired arms across them on purpose. A single hardcoded prompt is wrong in 47 of them, and
the ways it is wrong escalate:

1. **It corrupts the measurement.** The original prompt asserted that search matches "on words, not
   meaning" and named `grep_corpus` — both false in a vector-only run. Describing a tool the agent
   does not have is not a cosmetic flaw in an eval: it makes the comparison partly a measurement of
   how well each configuration copes with misleading instructions.
2. **Stale text becomes an instruction to abstain**, which is the dangerous direction. Every
   pre-Phase-2 variant listed *"anything needing current news"* as a reason to abstain — with the web
   lane registered, that declines the questions the lane was added for. Phase 3 hit it harder:
   the abstention list said plan years the tables do not hold are out of reach, and, measured, the
   agent declined *"what plans can a 40-year-old buy in ZIP 27360"* **without calling a single tool.**
3. **A lane described only in the negative disappears.** Asked what it could do, a three-lane run
   described the corpus and the tables and never mentioned the web — every other reference to that
   lane was a hedge.
4. **A prompt that contradicts itself is resolved by the model, not by the author.** The first Phase 3
   attempt kept the Phase 2 sentence naming "a recent recall" as a reason to search the web and
   *added* a step saying the web is the wrong source for recalls. Measured: the agent called
   `drug_recalls`, got 44 recalls, searched the web anyway, and cited the web.

Maintaining one prompt per combination is 48 documents that drift on the first edit. So every
lane-dependent region is assembled from per-lane fragments — **one copy of each sentence** — and that
means every region, not just the tool list: what the agent holds, the search guidance, the numbered
steps, the citation forms, the self-description, and the abstention list, where **each landed lane
removes a reason to abstain.**

Four properties make it hold:

**Capabilities are affirmative; absences are generated.** Each lane owns one positive paragraph, and
the closing "you have no access to anything else" clause is built from the same booleans, so it can
never survive a lane landing. Leading with the capability is a fix for failure 3 — and
`_self_description` closes the loop from the other side, naming the lanes from the booleans and
telling the model to give the last the same weight as the first.

**Superseded text is replaced, never rebutted.** There are two mutually exclusive versions of the
"the web is the last place to look" step, and the live-lane variant simply does not contain the
recall example. That is the whole fix for failure 4.

**Some fragments exist only at an intersection.** `_WEB_STEP_LIVE_OVERLAP` needs `web and live`;
`_RECONCILIATION_STEP` needs `structured and live`, because "which source wins when the vendored
table and the live API disagree" cannot be asked unless both are registered (§15 of
[structured-api-tools.md](structured-api-tools.md)). Neither can be owned by a single lane's
fragment.

**Structure is generated, not transcribed.** Steps are stored unnumbered and numbered at render,
replacing a hand-maintained `step=6 if structured else 4` — one magic number per combination. A
mis-numbered list is a small but real signal to the model that the instructions were not written for
the tools it has.

**What the prompt deliberately does not say** is the other half of the design. Everything a guardrail
can enforce is enforced: it does not ask the model to cite only what it retrieved (`seen_chunks` plus
the output validator make that impossible rather than requested), does not ask for verbatim snippets
on trust (§4 checks them), and does not ask the model to limit its tool calls (`UsageLimits` does).
What is left is what only the model can do — which tool to reach for, when the evidence is enough,
and when to say it does not know. The corollary is that **an instruction that fights a guardrail
loses expensively**: `_self_description` deliberately omits "no citations needed" for a question
about the agent itself, because `_validate_grounding` refuses a non-abstained answer with an empty
citation list and that instruction would spend the retry budget losing the argument.

Asserted per axis rather than assumed: `tests/test_agent.py` (toolset),
`tests/test_web_agent.py::test_the_prompt_only_promises_lanes_the_run_actually_has` and
`::test_the_prompt_tells_the_agent_to_name_every_lane_it_holds` (web, and failure 3),
`tests/test_live_agent.py::test_the_prompt_describes_the_lane_only_when_it_exists`,
`::test_the_prompt_claims_the_marketplace_only_when_it_is_registered` and
`::test_the_reconciliation_rule_reaches_the_model_only_with_both_halves` (live, marketplace, and the
intersection). Each `#:` comment in `agent/prompt.py` records the failure its fragment fixes.

## 4. The grounding guardrail is code

The prompt asks for grounded answers. `runtime._validate_grounding` is what makes them grounded. It
runs on every candidate answer and raises `ModelRetry` — handing the model the reason — when:

| rejected | why it matters |
|---|---|
| a `chunk_id` no tool returned this run | a fabricated source: the worst failure this tool has |
| a `snippet` not verbatim in that chunk | a real source with words put in its mouth — *worse*, because it reads as more trustworthy |
| a `row_id` no query returned this run | the same fabrication, one lane over |
| a cell value that differs from the row's, by so much as a trailing space | an edited figure, presented as what CMS published |
| a column the cited row does not have | a value attributed to a field that does not exist |
| a `[cN]` marker with no matching citation | `ChatResponse` would reject it with a 500 |
| an answer with no citations and `abstained=false` | an assertion with nothing behind it |

The first is mechanical rather than requested: `tools.AnswerDeps.seen_chunks` records every chunk
any tool returns, and nothing outside that set is citable. There is no wording the model can choose
that gets around it.

**A vector hit is citable on exactly the same terms, and that is a property of how it is built.**
`vector_search` resolves LanceDB's `chunk_id`s back through the same `CorpusIndex` the lexical
tools use, so a semantic hit reaches `seen_chunks` by the same path. Returning rows straight out of
the store would have broken it in the nastiest available way: `remember()` looks chunks up by id
and silently skips what it cannot find, so every citation would then be rejected as ungrounded —
two layers from the cause, looking like a model problem. `VectorIndex.open` refusing a store built
against different chunks is the production-scale version of the same guarantee.

The last two duplicate checks `ChatResponse._check_provenance` already makes. Doing them one layer
earlier turns a served 500 into a retry the model can act on. `agent.retries` (2, in `config.yaml`)
is that budget; exhausting it raises `UnexpectedModelBehavior`, which is correct — a model that
will not ground its answer must fail loudly rather than serve an ungrounded one.

Snippets are compared **whitespace-normalized**. The corpora wrap mid-sentence, so a byte-exact
test would reject genuine quotations and send the model into a retry loop it cannot win.

**Cells are compared byte-exactly, and the asymmetry is deliberate.** A cell has no line wrapping
to survive, and its whitespace is data: the mirror stores `'$4,500 '` with a trailing space, and
`'Not Applicable'` sits beside `''` meaning something different. A model that tidies `$4,500 ` to
`$4,500` in a *citation* has edited the evidence, so it is told to copy it again — while its prose
is free to read `$4,500`, which is what a person should see. Same rule as the chunk path (quote it
exactly), applied to a unit whose exactness means something stricter.

**Citations are then rebuilt from the source.** The model contributes an identifier and a
quotation — `chunk_id` + `snippet`, or `row_id` + `cells` — both checked; title, url, `doc_id` and
`source_type` are read off the real `Chunk` or `Row`. No invented title can reach the browser.

A row citation carries `source_type="structured_api"`, a title naming the table and plan year, the
CMS landing page as its url, the cited cells as its snippet, and **null `doc_id` and `chunk_id`** —
the first citations in this repo that are not chunks. That is also how the frontend tells the two
apart: cells or prose, decided by the absence of a chunk rather than by the lane name, because
Phase 3 will put live-API answers in the same lane without them being rows.

**`claims` are derived, not asked for.** `AnswerClaim.text` must be a verbatim substring of the
answer, and a model reproducing its own prose character-for-character is a coin flip that fails the
contract validator when it loses. `_claims()` splits on the markers instead, which is exact by
construction.

## 5. Loop safety, from day one

`config.yaml`'s `agent:` block carries `request_limit: 12` and `tool_calls_limit: 16`, applied as
`UsageLimits` on every run. They were 8 and 12 through Phase 1b and were **raised by measurement**,
not by feel: see the note below on what the relational lane spends.

Without them, a model that keeps reformulating a query it will never satisfy runs until the request
times out, and the reader watches a spinner instead of getting an answer or an honest abstention.

Phase 4 adds cycle detection and a hop ceiling **on top of** these rather than introducing the
idea — cheap now, painful to retrofit.

**The relational lane spends more of that budget than the reference lane does**, and it is worth
knowing why before raising the ceilings. A plan question runs `list_tables` → `describe_table` →
`query_structured`, sometimes with a second describe or a corrected query, where a corpus question
is often one search and an answer. A single live plan question measured 12 tool steps and ~42k
tokens against roughly a third of that for a definitional one. Two consequences, both measured on
the first structured eval run rather than predicted: the old `request_limit: 8` **lost a working
question outright** to `UsageLimitExceeded` (str-01, which the lane can answer), and a concurrent
sweep hit the provider's tokens-per-minute limit far sooner than the Phase 1b runs did. The
ceilings are now 12/16; the concurrency lesson is in §6.

**`plan_year` reaches the loop for the first time here.** It has ridden in `ChatRequest` since
Phase 0 and nothing read it; now it selects which partition of the vendored data a query may touch,
and a query naming another year's table is rejected before it runs. That is the cross-cutting "pin
the plan year" principle enforced by code rather than stated — and it is enforced in the *guard*
rather than in the prompt, because the failure it prevents (a figure from the wrong year) looks
exactly as authoritative as the right answer.

## 6. What the numbers actually say

### Phase 2: what does a third lane cost the first two?

Measured 2026-08-20, `gpt-5.6-luna`, toolset `both`, 43 questions with the web lane on and 39
without (`run_2026-08-20_1` / `_2`). One run each — read the caveat at the end of this section.

| | web ON | web OFF |
|---|---|---|
| **routing_correct** (3 lanes / 2) | **1.000** | 1.000 |
| **web_reach_rate** | **1.000** (4/4) | — |
| structured_exact_match | 1.000 | 1.000 |
| recall@5 (30 reference questions) | 0.667 | 0.633 |
| MRR | 0.650 | 0.633 |
| groundedness · citation_resolution | 1.000 · 1.000 | 1.000 · 1.000 |
| abstention_accuracy | **0.800** | 1.000 |

**The third lane costs the first two nothing measurable.** Recall moves 0.667 against 0.633 — one
question, and the known run-to-run spread at fixed config is 0.200, so read this as "no evidence of
harm", never as an improvement.

**The agent did not over-reach once**, which was the phase's characteristic new risk. `tools_used`
— added this phase precisely so this question could be answered from a run file rather than from
hand-captured traces — says `web_search` was called on **0 of 30 reference questions** and on
**4 of 4 web questions**. Routing scored 1.000 on both runs.

**The one real cost is `abs-03`, and it is the interesting result.** *"What will the standard Part B
premium be in 2027?"* is a figure CMS has not published. Without the web lane the agent abstains,
correctly. With it, the agent finds projections and estimates written *about* the figure and answers
anyway — dropping abstention accuracy from 1.000 to 0.800 on a five-question slice.

That is the failure this question was deliberately kept as an abstention to expose
(docs/web_search_tool.md §12 predicted it in those words). It is **not** a routing error: the web
was the right lane to try. It is a harder judgement one level down — *finding something written
about a figure is not the same as the figure existing* — and neither the prompt's "say what you do
not know" nor the grounding guardrail catches it, because the answer **is** grounded: the quoted
words really are on the page. The guardrail checks that a source says what the answer says; nothing
checks whether the source is *authoritative for that claim*. Phase 4's per-claim provenance is where
that could be addressed; naming it here is the honest interim step. Phase 4 was never built, so this
stands — [future_enhancements.md](future_enhancements.md) §3 carries it forward.

**Three questions errored** on the web-on run (`UnexpectedModelBehavior: Exceeded maximum output
retries`), the recurring failure docs/progress.md has tracked since 1a. Their treatment exposed a
harness bug that this section would otherwise have quoted wrongly: an errored result dropped its
`expected_source_type`, so it left the recall denominator instead of counting as a miss, and the run
first reported **0.741 over 27** rather than 0.667 over 30. A run that loses questions must never
score better than one that answered them all. Fixed, with a regression test; the table above is
recomputed.

### Phase 1-c: what does a second lane cost the first one?

**Nothing measurable, and it answers the questions the corpus never could.** Measured 2026-08-19,
two agent runs differing only in `--structured` / `--no-structured`, sequentially (see the
concurrency note below).

| | reference questions (30) | | structured (4) | abstentions (6) | |
|---|---|---|---|---|---|
| | recall@5 | MRR | exact cell match | accuracy | routing |
| `--no-structured` | 0.767 | 0.711 | — | **1.000** | — |
| `--structured` | **0.800** | **0.733** | **0.875** | 0.833 | **1.000** |

**The reference numbers do not move.** 0.800 against 0.767 is one question on a set of thirty, and
this repo's own figure for agent run-to-run spread at fixed configuration is 0.200 — so the honest
reading is *no measurable cost*, not *a small gain*. That was the question worth asking: the risk of
adding a lane is that the agent starts reaching for it on questions the corpus already answers, and
that would have shown up here as a drop.

**Routing is 1.000, and it is the number this phase exists for.** Every structured question was
answered from a row, and every reference question from a passage. The failure this guards against —
answering *"what is the deductible on plan X"* with a fluent passage defining "deductible" — did not
happen once. It is also the metric to distrust first when the gold set grows: four structured
questions is not many, and they are unambiguous by construction.

**Structured exact match is 0.875, and the missing eighth was a bad gold question.** `str-02` asked
for a plan's monthly premium while grading both the premium *and* the deductible; the agent cited
the premium and scored 0.5. The question now asks for both. Worth stating plainly because the fix
was to the eval, not to the agent: a question must ask for everything it grades, and this lane makes
that sloppiness visible in a way a prose question does not.

**Abstention 0.833 is one question, and that question got genuinely harder.** `abs-02` — *"is
metformin covered under the Humana Gold Plus HMO plan's formulary?"* — is a coin flip now. Asked
again directly, the agent abstained, and did it better than the reference-only build ever could:

> I can't determine that from the plan name alone. The 2026 data contains multiple Humana Gold Plus
> HMO plans tied to different contracts and formularies — for example, contract H0028, plan 077 uses
> formulary 00026409, while contract H1036, plan 302 uses formulary 00026412. Please provide the
> plan's contract and plan IDs, or your state and county.

That is an abstention that **used the tables to show its own limit**, with rows cited for the claim
that the plan name is ambiguous. The lane did not teach the agent to over-reach; it gave it enough
to investigate, and investigating sometimes ends in an answer and sometimes in a better refusal. Two
things follow. A six-question abstention set cannot resolve a difference of one. And `abs-02` should
probably be re-authored, because "abstain" and "answer for the one plan you can identify" are both
defensible now, which is not what a gold label is for.

**Two bugs this measurement found, both fixed before these numbers were taken:**

- **`request_limit: 8` lost a working question.** `str-01` died on `UsageLimitExceeded` in the first
  run. A plan question spends `list_tables` → `describe_table` (often twice) → `query_structured` →
  a corrected query → answer, where a corpus question spends two or three calls. Now 12/16.
- **`groundedness_grader` scored row citations as fabricated sources**, reporting 0.889 where the
  guardrail guarantees 1.000, because a row has no `chunk_id` to resolve. A metric that punishes a
  capability for existing reads exactly like a regression — see §4 and `evals/grading.py`.

**Concurrency, measured the hard way.** Three sweeps were thrown away to rate limits before these
two completed: at `--concurrency 3`, ten questions errored; at `--concurrency 5`, twenty-five and
thirty-three did, reporting `recall@5 0.033` for a set that was never answered. The cause is this
lane's token appetite — ~40k tokens for one structured question against roughly a third of that for
a definitional one, because the whole conversation is resent each turn and `describe_table` is a
large reply. Five in flight is ~200k tokens, which is the account's per-minute ceiling exactly.
**Run this lane's evals sequentially**, and read any run with errored questions as broken rather
than weak — the errors are in the run record for exactly that reason.

### Phase 1b: does vector search beat lexical?

**Yes, and "both" beats either alone.** Measured 2026-08-16 against the same gold set and chunk
snapshots.

The decision was made on the two **retrieval-only** runners, not on an agent A/B, and that choice
is the methodological point of the phase. Seven Phase 1a agent runs at fixed config span recall@5
0.600-0.867 — so an agent pair cannot resolve an effect the size of the one being looked for. These
two have no model in them at all, and the vector run reproduced to three decimals across two
invocations:

| retrieval only (30 in-corpus questions) | recall@5 | MRR |
|---|---|---|
| `make eval-retrieval` — BM25 | 0.567 | 0.416 |
| `make eval-retrieval-vector` — embeddings | **0.733** | **0.561** |

**They fail differently, which is the finding that matters.** Vector fixes 7 questions and
regresses 2 — net +5, but the composition is more informative than the total. Four of the seven
land at **rank 1**. The clearest case is `hcg-01`, *"What exactly is a deductible?"* — the exact
question §6's Phase 1a half predicted BM25 would lose to the rare word *exactly*. Vector retrieves it first.
Meanwhile `ncd-05` and `pub-10` go the other way, and six questions defeat both.

That complementarity is the empirical case for shipping `both`, and it is why the trade is not a
swap. Through the agent, on the full 35-question set:

| agent (35 questions) | recall@5 | MRR | abstention | errors |
|---|---|---|---|---|
| `make eval-lexical` | 0.667 | 0.650 | 1.000 | 0 |
| `make eval-vector` | 0.733 | 0.717 | 1.000 | 2 |
| `make eval` — **both** (shipped) | **0.800** | **0.733** | 1.000 | 0 |

plan.md set the bar: *"'Both' has to earn its place — it only wins if it beats each alone."* It
does, on both metrics. **Read the agent rows as weaker evidence than the retrieval rows**: they are
single runs, the known spread is 0.200 wide, and 0.800-vs-0.733 is inside it. The retrieval
comparison is what carries the decision; the agent rows say the agent is not squandering the extra
tool, which is the separate thing they can honestly show. The vector-only row is also a *floor* —
it lost 2 questions to errors (§8), and an errored question scores as a failure.

### Phase 1a: the lexical baseline

Measured against the 30 in-corpus gold questions with `make eval-retrieval` (free, instant, no
model):

| | recall@5 | MRR |
|---|---|---|
| BM25 over `retrieval_text` (shipped) | **0.567** | 0.416 |
| BM25 over bare `text` | 0.500 | 0.371 |

**The context header earns its place.** [chunking.md](chunking.md) §6 asserted that the breadcrumb
(`NCD 30.3 > Acupuncture > Indications...`) is legitimate lexical signal; it is, at every `k1`/`b`
cell swept, and `tests/test_corpus_index.py` pins it so a future "simplify: index `text`" has to
argue with a number.

**`bm25_b` was a false alarm.** chunking.md §5 handed forward the worry that length normalization
would over-favour the 505 NCD chunks under 200 characters. Sweeping `b` from 0.0 to 1.0 moves
recall@5 by at most one question at any `k1`. That question is closed; the short NCD sections are
fine.

**`bm25_k1: 2.0`, but read it as "no evidence 1.2 is better here".** 2.0 won at every `k` on both
metrics, by two questions out of thirty — which a set this size cannot resolve. Do not chase
further decimals on this set.

**The raw-question baseline is mediocre on purpose.** Feeding the verbatim gold question to BM25
scores 0.567, and the misses are vocabulary gaps: *"what exactly is a deductible?"* is dominated by
the rare word *exactly*, not by *deductible*. That is not a bug to fix in the ranking function — it
is the reason the agent gets a toolset and reformulates. Observed on a live run, the agent turns
that question into `search_corpus(query='deductible definition what you pay before plan pays')` and
lands the glossary entry first. It is also the honest baseline Phase 1b's vector search had to
beat — and did, retrieving that same question at rank 1 without reformulation.

## 7. Streaming a structured output

`answer_question()` is `stream_answer()` drained to its `done` event. One code path, two shapes.
Phase 0 had a test asserting the two endpoints returned identical objects; a nondeterministic model
makes that unassertable by re-running, so the property moved into the code.

The answer streams even though the output is a structured object rather than text. The model emits
that object as JSON fragments; `_partial_answer()` parses a prefix of the buffer with
`from_json(allow_partial=...)`, reads `answer`, and emits the **diff** against what was already
sent. Which streamed part is the output is identified **by shape** — a buffer that parses to an
object with a string `answer`, and no tool takes an argument by that name. The
alternative was tracking part indices across `FinalResultEvent`, which differs between
tool-output, native-output and prompted-output modes and between providers.

A final flush covers the rest: a provider that sends the output in one piece, or a retry whose
accepted answer shares no prefix with the rejected draft. `done` is authoritative either way, but
the UI shows accumulated tokens until then, so a silent gap reads as a truncated answer.

Trace steps come from `deps.trace`, not from the framework's tool events, because the tools record
the arguments, the real duration, and a summary of what came back — none of which the event stream
carries.

### Abandoned drafts

Found by `make smoke-web` and **pre-existing since Phase 1a**: when the grounding validator rejects
an answer and the retry words the replacement differently, the reader saw the rejected draft
*followed by* the accepted one. `done` had always corrected it after the fact, but during the stream
the visible text was the ungrounded draft — which is the single worst thing this UI can display.

It had gone unnoticed because it needs a retry *and* a materially reworded second attempt, which no
earlier live run happened to produce. `TokenEvent` now carries `reset: bool`, set when the answer
being written no longer extends what was streamed; `useChat.ts` replaces rather than appends on it.
The field is additive and defaulted, so a client that ignores it behaves exactly as before.

The guard on the reset branch matters: early fragments of a retry are usually still a prefix of what
was streamed, and resetting on those would flicker the answer away and back. It fires only once the
two genuinely diverge.

## 8. Three things that will bite

**Two runs in a row will hit the tokens-per-minute ceiling.** One agent run is ~6,000 tokens, so
the 35-question set is ~210k against a 200k/minute allowance — the set cannot honestly complete
inside a minute at *any* concurrency. Running `eval-lexical`, `eval-vector` and `eval`
back-to-back put 16 of 35 questions into `Rate limit reached` on the third, which scored 0.400
recall and 0.400 abstention accuracy: **a TPM-starved run looks like a quality regression in every
column at once.** Read a run's error list before its score. A short pause and `--concurrency 2`
produced a clean 0.800 immediately afterwards.

**`UnexpectedModelBehavior: Exceeded maximum output retries (2)` recurs, intermittently.** Two
questions in the vector-only run died this way — the *grounding validator's* budget exhausted,
which is a different failure from a 429 despite sharing the ERR column. Both were re-run by hand
immediately afterwards and both succeeded, so it is model nondeterminism producing a non-verbatim
snippet twice in a row rather than anything toolset-specific. Recorded, not fixed: raising
`agent.retries` would paper over the one guardrail whose failures should stay loud. If it becomes
frequent, the validator's `ModelRetry` messages are where to look.

**`Agent("openai:...")` does not see `Secrets`.** PydanticAI reads `OPENAI_API_KEY` from the
process environment and `uv run` does not load `.env`, so the obvious wiring raises `UserError`
even though the settings object holds the key. `_resolve_model` passes
`OpenAIProvider(api_key=...)` explicitly — also the better shape, since the credential flows
through one audited call instead of ambient global state.

**The `gpt-5.6-*` family needs the Responses API, and `_resolve_model` has to track that even
though it constructs the model class by hand.** PydanticAI's own inference of a bare `openai:`
string already resolves to `OpenAIResponsesModel` (only `openai-chat:` gets `OpenAIChatModel`) —
`_resolve_model` exists for the credential, above, not to pick a different endpoint. The first
draft built `OpenAIChatModel` explicitly on an unchecked assumption, and that combination is a hard
400 on the first tool call: *"Function tools with reasoning_effort are not supported ... in
/v1/chat/completions. To use function tools, use /v1/responses"*. An agent with no tools is not
this project, so the explicit construction has to match what inference would have picked.

## 9. Testing it without a provider

`tests/conftest.py` sets `pydantic_ai.models.ALLOW_MODEL_REQUESTS = False` for the whole suite, so
`make check-all` cannot spend money, cannot need a key, and cannot fail because a provider is
having a bad afternoon.

**That flag covers PydanticAI's model requests and nothing else, so each phase that adds a provider
has to add its own guard.** There are now three: the flag, `_no_live_embeddings` (Phase 1b — an
embedding call goes out through the OpenAI SDK directly), and `_no_live_web_search` (Phase 2 — a
Tavily search goes out through `httpx`). Each is patched **at the factory**, so a test that trips
one gets an error naming the seam it should have used rather than an authentication failure from
inside a vendor library. Without them a test would reach the network on the developer's own key and
**pass**, which is the worst direction for a guard to fail in.

The web lane is tested against `httpx.MockTransport` injected into a real `AsyncTavilyClient` —
the SDK accepts an external client, which is the concrete reason it was chosen over a hand-rolled
HTTP call (docs/web_search_tool.md §4). Its fixtures are **synthetic, over `example.org`**, and that
is a licensing decision rather than a testing preference: a real Tavily response body carries
extracted third-party page text, and this repo is public.

The `agent_kit` fixture supplies a two-chunk corpus and a scripted `FunctionModel`. It provides
**both** a `function` and a `stream_function`, because the runtime only ever streams — and the
streaming half emits arguments in 17-character JSON fragments, deliberately aligned to nothing, so
they split mid-key and mid-string. That is what exercises `_partial_answer`; a single-chunk script
would leave the whole streaming path untested while appearing to pass.

Guardrail tests are written as *what would a model do wrong*, and assert the retry **message** as
well as the rejection: a retry the model cannot act on is a retry wasted.

**One thing that suite structurally cannot check, so `make smoke` does.** `_partial_answer` parses
the output as a **real provider fragments it**, and `FunctionModel` can only approximate that. If
OpenAI changes how `/v1/responses` chops output deltas, every test stays green, the final flush
still delivers a correct answer in one lump, and incremental streaming is silently dead — a long
pause then a wall of text. `make smoke` makes one live call and fails when a run produces a single
token event. It stays out of pytest deliberately: a failing test means this repo is wrong, a failing
smoke check might mean the provider changed, and one command that means either teaches you to shrug
at red. `make smoke-abstain` runs the same checks over an out-of-corpus question, where an invented
citation would be the worst failure this tool has. `make smoke-web` (Phase 2) asks a question no
offline lane can answer and requires the agent to reach Tavily and cite a real URL — the only
command in the repo that spends a Tavily credit.

**`make smoke-web` earned its place on its first run**, which is the argument for keeping a rung
that no offline test can replace. It caught two things `check-all` could not:

- **A metric punishing a capability for existing.** `citations_resolve` demanded a resolvable
  `chunk_id` of every citation that was not a row, so two perfectly good web citations were reported
  as fabrications. This is the *second* time that check has made the mistake — Phase 1-c made it
  first, about rows. A check that enumerates lanes by exclusion has to be revisited by whoever adds
  one.
- **A real, pre-existing streaming bug** (§7).

## 10. Grading

Three tiers, split by cost, which is why the graders are a list the caller composes rather than a
fixed pipeline:

| | runs | measures |
|---|---|---|
| `make eval-retrieval` | free, instant, no key | lexical retrieval alone — the `bm25_b`/`k1` sweep loop |
| `make eval-retrieval-vector` | ~30 embedding calls (a fraction of a cent) | semantic retrieval alone |
| `make eval` / `eval-lexical` / `eval-vector` | 35 model calls | the whole phase at one toolset |
| `make eval-judge` | +35 model calls | answer correctness, per key fact |

**The two retrieval rows are the only ones comparable run-to-run**, because no model touches them.
That is what made them the instrument Phase 1b's decision was taken on rather than a convenience:
§5's agent rows carry a 0.200 spread and the effect under test is smaller than that.

`make smoke` (§9) sits below all of these: one call, and it asks whether the live path works at all
rather than how well it answers.

`groundedness` and `citation_resolution` should be **1.000 on every agent run**. The output
validator rejects either failure before the response is built, so a number below 1.0 is a bug in
that validator, not a score to improve. It is measured anyway, because a guardrail nobody checks is
a guardrail that has already stopped working.

`key_fact_coverage` is lexical overlap and is named *coverage* rather than *correctness* on
purpose: it scores an answer and its exact negation identically. The judge produces the correctness
number, grades one key fact at a time against the gold set's `answer_key_facts`, and runs on
`evals.judge_model` — **a different model from the one it grades**, because a judge marking its own
homework agrees with itself most confidently exactly where both are wrong.

The judge is deliberately unreachable from the UI. A button that spends money on every click is the
wrong affordance.
