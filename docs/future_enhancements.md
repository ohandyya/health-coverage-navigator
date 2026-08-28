# Future enhancements

**Active development on this project ended after Phase 3.** The tri-modal core is complete and
measured; what follows is the record of everything that was *scoped and deliberately not built*.

This document exists because that work was real. Phases 4 and 5 have acceptance tests and
capability checklists in [plan.md](plan.md), and a dozen smaller items were each deferred with a
reason — several with a **named trigger condition** saying what evidence should cause them to be
built. Left scattered across six design docs, all of that reads as a queue. Collected here, it
reads as what it is: decisions.

**What this doc is and is not.** It says *what was scoped and not built, and why*. It does not say
what is built ([progress.md](progress.md) owns that), and it does not restate a design — every
entry links to the document that owns the full reasoning and stops there. The fifth doc in
[CLAUDE.md](../CLAUDE.md)'s table.

---

## 1. Phase 4 — planning, decomposition, per-claim provenance

Full scope: [plan.md](plan.md) → *Phase 4*. The agent has called tools in a loop since Phase 1a;
what it never learned to do is **plan** — decompose a compound question into sub-questions, route
each to its own lane, and synthesize one answer from several tool results. Loop safety would have
*tightened* here rather than appeared (step limits shipped with the agent in 1a); what Phase 4 adds
is cycle detection and a hop ceiling on top of them.

- Multi-step loop (plan → act → observe → synthesize) on the existing agent
- Question decomposition
- Per-claim provenance tagging
- Observability / tracing (Logfire or similar): tool calls, latencies, token usage
- Multi-hop correctness and citation-accuracy evals
- Loop safety: cycle detection + hop ceiling
- Frontend F3 ([frontend_plan.md](frontend_plan.md) §6): nested multi-hop trace steps, hovering a
  claim highlights exactly its citations, per-run latency and token cost

**Two things Phase 3 left on the table for it**, carried here rather than lost with the "next up"
line that used to hold them:

**`live-06` is the motivating case, and its original wording is preserved.** It first asked *"is
atorvastatin covered under a plan sold in ZIP 27360"* — `find_plans` → pick a plan →
`check_drug_coverage` → `drug_label`, a four-hop chain. It passed on one run of three and errored on
the others. It was **re-scoped to name a plan rather than fixed**, precisely because reliable
multi-hop is Phase 4's job. The un-scoped version is the natural first Phase 4 gold question and its
original wording sits in the question's notes in `evals/gold/questions.yaml`.

**`abs-03` is the standing argument for per-claim provenance.** Asked what the 2027 Part B premium
will be — a figure CMS has not published — the agent finds writing *about* the figure and answers.
Grounded, correctly routed, and wrong. See §3 below: nothing here checks that a source is
authoritative *for a given claim*, and per-claim provenance is where that check would live.

---

## 2. Phase 5 — growth surface: from bot to tool

Full scope: [plan.md](plan.md) → *Phase 5*. The lookups already exist — Phase 1-c built the query
tools over the PUFs and Phase 3 put the live APIs beside them. What Phase 5 adds is not access to
the data but **what you do with it**: comparing plans rather than reporting one, breaking a cost
down rather than quoting a cell, checking a network, walking someone through an appeal, and noticing
that something changed without being asked.

- Comparison / aggregation layer **over the Phase 1-c query tools** — multi-plan queries, ranking,
  tabular results; not a second storage engine. `query_structured` can express it, but nothing in
  1-c is *designed* for it and the row cap is sized for lookups
  ([relational-tool.md](relational-tool.md) §14)
- Drug-cost breakdowns · provider-network checks · appeals guidance (No Surprises Act)
- Scheduled "what changed for this plan year" monitor — the **first feature needing state that
  outlives a request**, plus a scheduler and an alert channel
- Regression eval suite that grows with each capability
- Frontend F4 ([frontend_plan.md](frontend_plan.md) §6): comparison tables, cost breakdowns, a
  "what changed" view — the first UI that is more than chat + provenance

**Three concrete prerequisites, each already documented where it belongs:**

| Prerequisite | Why it is Phase 5's | Owner |
|---|---|---|
| Part D **pharmacy network** (2.29 GB, six parts needing reassembly) and **pricing** (191 MB) | Deliberately unfetched since Phase 0; nothing before network checks and drug-cost work reads them | [part_d_spuf_data.md](part_d_spuf_data.md) |
| Exchange **Rate PUF** (premiums) | Not vendored; premium modelling is what needs it | [exchange_puf_data.md](exchange_puf_data.md) |
| Marketplace `/providers/covered` + `/providers/search`, and `POST /households/eligibility/estimates` | Network checks are Phase 5's; the estimates endpoint is the one Marketplace endpoint whose output is a **calculation** rather than a record, which makes citing it a different problem | [structured-api-tools.md](structured-api-tools.md) §11, §18b |

**The network boundary is worth restating, because it looks like a gap and is not.** The NPI
registry says what a provider *is* — identity, specialty, status — never which plans pay them. So
*"which dermatologists near a ZIP take Aetna"* is correctly declined today and needs
`/providers/covered`, which is Phase 5's. It was mis-dated to Phase 3 in the gold set for two phases
before being corrected to `"5"`.

---

## 3. Deferred by design

Each of these was a real capability left out with a reason. Where a **trigger condition** was
recorded — the evidence that should cause it to be built — it is repeated here, because that is what
makes an item a decision rather than an omission.

### `read_url` over Tavily's `/extract` — the web lane's `get_chunk`

The most likely thing to be needed next in the web lane. Tavily returns three reranked chunks per
result; when the answer sits just outside them, the only recovery is a narrower query. Deferred
because Phase 2's measurement is *lane routing*, and a two-tool web lane would make that number
partly about navigation **within** the lane.

**Trigger condition:** traces showing repeated near-identical searches against the same domain, or
web answers that abstain while a cited result plainly contains the topic. Both are visible in the
trace panel and neither needs new instrumentation.

**Try the cheap fix first:** `include_raw_content` on `/search` costs no extra credits and was
rejected for flooding the context, not for cost — measure `max_results: 3` with raw content against
5 without it before adding a second tool. That is a config change and a run.

The design is already worked out — POST to `/extract` with the *originating query* and
`chunks_per_source`, never a bare URL, plus its own `max_reads_per_run` budget — in
[web_search_tool.md](web_search_tool.md) §15, so it does not need re-deriving.

### A negative finding in the *mirror* half is still not citable

The live lane's version of this is closed structurally: nine paths, one `_search_row` helper, and a
test that walks every tool so a new one inherits the invariant. But `query_structured` returning
zero rows is in the same bind — *"empty is an answer"* with no way to **cite** that answer, which
forces a false abstention or a worse source.

Left open deliberately rather than swept in with the live fix: it is Phase 1-c code, so **its change
should be measured against the mirror slice with its own eval run**. `_search_row` is now the
natural place to close it. [negative-finding-gaps.md](negative-finding-gaps.md) §6.

### Nothing checks that a source is *authoritative for a claim*

`abs-03` is the demonstration (§1 above). The grounding guardrail checks that a source **says** what
the answer says; there is no notion anywhere of a source being authoritative *for a given claim*, so
an article speculating about an unpublished figure passes every check the repo has. Not a routing
error — the web was the right lane to try — and not something the guardrail can catch, because the
answer really is grounded. [agent.md](agent.md) §6 names it as the honest interim step; Phase 4's
per-claim provenance is where the check would belong.

Two heuristics were considered and rejected at Phase 2 — an `authoritative: bool` on results, and
re-ranking Tavily's order by domain — on the grounds that neither could be defended with a number by
an eval slice that grades routing and groundedness ([web_search_tool.md](web_search_tool.md)).

### Hybrid ranking

LanceDB carries BM25 in the same table as the vectors, and it is deliberately unused. Phase 1-b asks
whether the *agent* can choose between two ways of searching, so "both" means both **tools
registered**, with the agent reconciling them — a fused ranker would do the reconciling itself and
hide the thing being measured. It would also put a second BM25 implementation beside the stdlib one
in `agent/bm25.py`, so the lexical baseline and the lexical half of the hybrid could drift apart.
Still available: the store's schema does not preclude it. [lancedb.md](lancedb.md).

### The relational lane's unbuilt shapes

All from [relational-tool.md](relational-tool.md) §14, recorded there so none of it gets re-argued:

- **Typed join helper tools** — `lookup_plan_formulary` and its sibling are fully written out in
  §14a, unbuilt because everything they would protect against is knowable from `describe_table` plus
  one sentence in the `query_structured` description. **The traces were to say**; adding a helper
  afterwards is additive, while one written on a hunch is a design decision that never gets
  revisited.
- **Typed views over the mirror** — Phase 5's comparison work is what produces real column
  requirements.
- **Cross-source joins (Exchange ↔ Part D)** — rejected outright, not deferred. Different
  programmes, different plan-identifier spaces; a question that seems to need it is two questions.
- **A structured answerer with no model** (the `bm25`/`vector` equivalent) — rejected. There is no
  "retrieve and stop" for SQL: writing the query *is* the model's job.

### The live lane's unbuilt endpoints and machinery

From [structured-api-tools.md](structured-api-tools.md) §18b:

- **A persistent response cache.** The cache is run-scoped on purpose — the value is almost entirely
  *within* a run, and a cross-run disk cache introduces staleness into the one lane whose selling
  point is being current. **Trigger condition:** if an eval sweep is observed approaching openFDA's
  1,000/day, add a disk cache with a TTL measured in hours and a `--no-cache` escape for smoke runs
  — not before (§13c).
- **openFDA's other endpoints** — NDC directory, Orange Book, Drugs@FDA, adverse events. Label and
  enforcement answer the questions plan.md names; the rest is scope.
- **Automatic key-rotation handling.** The Marketplace key's 60-day expiry is real, but the fix —
  re-reading `.env` without a restart — means unfreezing `Secrets`, and a clear 401 message is most
  of the value for none of the risk.

### Frontend surfaces left out of v1

From [frontend_plan.md](frontend_plan.md) §9–§10:

- **Multi-turn conversation.** `conversation_id` is carried through the frozen contract from day one
  and is unused while the agent is single-shot. That was settled at F0 deliberately: it means
  multi-turn is a change to the *agent*, not to the contract.
- **A corpus browser.** A "search the 2,056 ingested documents" page was considered and left out —
  citation drill-down covers most of the need. Revisit if inspecting the corpus by hand turns out to
  be a frequent debugging move.
- **A lane-status indicator** driven by `/api/health`'s `lanes`. A server without the vendored
  mirror already answers with a 503 naming `make puf`, which the UI renders as an error; a second
  surface saying the same thing waits for a lane whose absence is not self-explanatory.
- Also out of scope by decision: auth and user accounts, mobile layouts, dark mode, conversation
  persistence across restarts, i18n, PWA/offline, and any deployment target.

---

## 4. Measurement gaps

These are the places where the repo's own numbers say what to work on next.

**Retrieval is the reference lane's bottleneck, and one more index is not the fix.** recall@5 0.700
in the shipped configuration means the agent never saw the right document for about 3 of 10
in-corpus questions — and **six of the thirty defeat *both* retrieval methods**, so the remaining
gap is chunking, the gold set's phrasing, or query reformulation, not a third backend.
[agent.md](agent.md) §6.

**The gold slices outside the reference lane are thin** — 4 mirror, 7 live, 4 web questions, every
one unambiguous by construction. A routing score near 1.000 is the first number to distrust as they
grow. The live slice is thinner than it looks: two of its questions were **reworded after they
failed**, both argued in the questions' notes, but changing a test after watching it fail deserves a
reader's scepticism.

**A single agent run is not evidence, and the repo still quotes single runs.** Seven runs at
effectively one configuration put recall@5 between 0.600 and 0.867 — model nondeterminism alone.
Phase 1-b routed *around* this by taking its decision on the deterministic retrieval-only runners.
The fix is a `--repeat`-and-aggregate mode on the eval runner; it was never built, so the docs say
"one sample" by hand instead.

**`web-01` is claimed by two lanes, and it is left for a human.** Asked whether a blood-pressure
medication was recalled recently, the agent calls both `web_search` and `drug_recalls` and cites the
FDA record — so a question expecting `web` fails routing. **The agent is arguably right and the gold
set arguably wrong**: the FDA's own enforcement database beats a news article about it. Options are
to re-label it `structured_api`, to keep it as a web question and accept that "recently" is genuinely
a web word, or to split it in two.

**Live and web answers are graded on routing and groundedness, not correctness**, and that is the
honest boundary of what a static gold set can assert about a live source — a formulary, a premium
and a recall list all move underneath a stable question. Correctness for those is asserted in
offline fixture tests, where the response is pinned.

The remaining operational open questions — eval rate-limit pacing, the recurring
`Exceeded maximum output retries`, `new_run_id()` collisions, judge-model reproducibility — are
status rather than scope, and stay in [progress.md](progress.md) → *Open questions*.
