# Health Coverage Navigator

Created by: Andy Tseng
Created time: July 3, 2026 2:55 PM
Status: In Progress
Tags: Learn

An open-source AI agent that answers U.S. health-coverage questions by routing between three
tool "lanes": **RAG** over public reference documents, **structured public APIs** for exact
facts, and **web search** for the fresh, open-ended world. Built on PydanticAI, using only
public data.

The organizing idea: almost every health-coverage question decomposes into one of three
sub-types, and picking the right lane is the core engineering problem — not retrieval alone.
Each sub-question becomes an independently gradable unit with a known correct source-type,
which is directly borrowable from binary-decomposition eval thinking.

| Sub-question type | Example | Lane |
|---|---|---|
| "What does the rule/benefit say?" | *What is a deductible?* | **RAG** (indexed reference) |
| "What's the fact for this plan/drug/provider?" | *Is drug X covered under plan Y?* | **Structured API** |
| "What's happening now / not in my corpus?" | *Any recent recall on drug X?* | **Web search** |

Each phase below is independently shippable and has an **acceptance test** — the phase is done
when you can do the thing in the milestone line.

## Resources

- Google Drive: [folder](https://drive.google.com/drive/folders/1DR4dGU8eLrRXLk_Hsfz68sgAQ4ySMqp4?usp=drive_link)

## What does it do

Nearly everyone deals with health insurance, so impact is enormous. The public-data story is the
best in all of insurance. RAG over genuinely public reference material: the Medicare & You
handbook, CMS coverage determinations (the Medicare Coverage Database of national/local
determinations is a deep, structured, public corpus), ACA marketplace rules, and Summary of
Benefits & Coverage documents (which use a federally standardized public template). Live public
tools: the [Healthcare.gov](http://healthcare.gov/) Marketplace plan API, the NPI registry for
provider lookup, and openFDA / drug-pricing data. Web search for the fresh half — a specific
drug's recent news, a provider, a plan-year change. Answers things like "is this treatment
typically covered, what plans cover my doctor, and what changed for this plan year?" Growth path
is long: plan comparison → formulary/drug-cost lookup → provider-network checks → appeals
guidance under the No Surprises Act.

## Data sources (verified live; see licensing note)

### Bulk-downloadable corpus (vendored into the repo)

Two kinds live here, and the difference matters: the **text corpora** get chunked and embedded
for RAG, while the **structured** sources land as a lossless columnar mirror that a later typed
layer queries — never chunked, never embedded.

**HealthCare.gov consumer-education content (the cleanest starting corpus).** HealthCare.gov
publishes every article and glossary term as machine-readable JSON, explicitly for third-party
reuse. All educational content is available in machine-readable formats, published as HTML pages
and JSON data, and everything is accessible through an API. There's a site-wide content index
endpoint plus per-post JSON (you append `.json` to any post URL). This is your best MVP corpus:
clean, unambiguously reusable, and written at exactly the "explain coverage to a human" level.
[HealthCare.gov](https://www.healthcare.gov/developers/)

**Medicare & You handbook + related CMS guides.** The annual handbook is a US-government work
(public domain), distributed as PDF. Great for the Medicare side of the domain. Pair it with the
HealthCare.gov glossary for terminology grounding.

**Medicare Coverage Database — National Coverage Determinations (NCDs).** Fetch these from the
**MCD Coverage API**, not the MCD Downloads page's bulk ZIPs. **Licensing caveat:** stick to NCDs
for your public corpus. NCDs don't contain procedure codes. The LCDs and Billing/Coding Articles
do — and CPT codes and similar are copyrighted by the AMA and ADA, with use restricted under
license. So the local-coverage code tables are the one thing you should *not* vendor into a public
GitHub repo. The API is what makes that rule self-enforcing: its auth boundary falls exactly on
the licensing boundary — National coverage endpoints answer without a key, LCD and Article
endpoints return `401` — so "NCDs only" becomes the set of endpoints that respond at all rather
than a rule the ingestion script has to police. The bulk ZIPs are the wrong route on both counts:
they 403 non-browser clients, and their single license click covers the AMA/ADA/AHA terms for
Local coverage data sitting beside the National data, which is precisely the conflation the
guardrail exists to prevent. See [medicare_ncd_data.md](medicare_ncd_data.md) for the endpoints,
the record schema, and the other license-clean National document types (NCAs, CALs, MEDCAC
materials, Technology Assessments) that are deliberately *not* vendored.
[CMS](https://www.cms.gov/medicare-coverage-database/downloads/downloads.aspx)
[Noridian](https://med.noridianmedicare.com/web/jea/policies/ncd)

**Health Insurance Exchange Public Use Files (the structured plan corpus).** These are the bulk
CSV/ZIP dumps behind the ACA marketplace, and they're actively maintained. The Exchange PUFs are
available for plan years 2014 through 2026, with the Benefits and Cost Sharing PUF updated April
28, 2026. The two you'll care about most: the Benefits and Cost Sharing PUF and the Plan
Attributes PUF. The Plan Attributes PUF contains plan-level data on max out-of-pocket,
deductibles, cost sharing, HSA eligibility, and formulary ID. These are large enough that the Rate
PUF and Benefits and Cost Sharing PUF exceed Excel's row limit and need a database or statistical
tool to open — good, because loading them into DuckDB/SQLite is exactly the kind of
structured-tool backend you want to practice against.
[CMS](https://www.cms.gov/marketplace/resources/data/public-use-files)

**Medicare Part D formulary files (the Part D structured corpus).** CMS publishes quarterly
formulary/pharmacy/pricing files. The Quarterly Prescription Drug Plan Formulary, Pharmacy
Network, and Pricing files contain formulary details including NDCs, cost-share tier, and
indicators for step therapy, quantity limits, and prior authorization — the Medicare-side
counterpart to the Exchange PUFs above, and the backing data for the drug-cost angle.

Scope worth stating up front, because the published file's shape forces a choice: it ships as a
2.49 GB container of ten nested per-file zips, of which the six-part pharmacy-network file is
92%. Seven files — formulary, excluded drugs, indication-based coverage, beneficiary cost,
insulin beneficiary cost, plan information, geographic locator — total 9.4 MB and cover every
question above. Take those; leave pharmacy-network for the Phase 5 provider/network work that
actually needs it, and pricing (191 MB) for when drug-cost estimates are on the table.
[CMS Data](https://data.cms.gov/provider-summary-by-type-of-service/medicare-part-d-prescribers/quarterly-prescription-drug-plan-formulary-pharmacy-network-and-pricing-information)

#### Deliberately *not* bulk-downloaded

Two sources that have bulk downloads are **used live, through their APIs, and never vendored**.
Recorded here so neither gets re-added on the reasoning that "the bulk file exists":

- **NPPES provider registry.** The dissemination file exceeds 4 GB, and provider lookup is
  inherently one-record-at-a-time — *"what is this NPI's specialty"*, not "scan every provider
  in the country". A bulk mirror would be 4 GB of storage and a staleness problem in exchange
  for nothing the [live registry API](#live-web--api-tools) doesn't already answer, faster and
  fresher (it updates daily).
- **openFDA drug labels.** Same reasoning: the questions this project asks openFDA — *has drug X
  been recalled*, *what are its indications* — are per-drug lookups against a keyless API. Bulk
  JSON would only pay off for offline whole-corpus indexing, which is not a capability any phase
  plans.

**This has a consequence worth stating:** no provider-level data is ever vendored into this
repo, so the guardrail's PII surface stays limited to what the reference corpora happen to
contain. Both sources reappear as typed tools in [Phase 3](#phase-3--add-structured-api-tools).

### Live Web / API tools

This is the other two-thirds of your tool-routing problem: **structured public APIs**
(deterministic lookups) and **general web search** (the open-ended, freshest layer).

**Marketplace API** — the big one. This is the API that literally powers HealthCare.gov shopping.
The Marketplace API drives Window Shop and Plan Compare on HealthCare.gov, showing plans available
based on location and household, whether plans cover specific providers and drugs, and estimated
yearly costs. Interface highlights: [Cms](https://developer.cms.gov/marketplace-api/)

- Base: `https://marketplace.api.healthcare.gov/api/v1/`
- Drug autocomplete → RxCUI: `GET /drugs/autocomplete?q={query}&apikey={key}`
- Drug coverage check: `GET /drugs/covered?year={yr}&drugs={rxcui}&planids={planid}&apikey={key}`
- Plan search + cost estimates: `POST /households/eligibility/estimates`

You request a key via the CMS developer portal; note API keys are rate limited, with the limit
passed back in the response headers. There's also a companion **Finder API** for private health
plans available outside the Marketplace, with keys rate-limited to 1000 requests/minute and
expiring every 60 days. [Cms](https://developer.cms.gov/marketplace-api/)
[Cms](https://developer.cms.gov/finder-api/)

**openFDA** — drug facts, recalls, shortages. Base `https://api.fda.gov/`, no key needed to start.
Drug endpoints cover adverse events, product labeling, the NDC directory, recall enforcement
reports, the Orange Book, Drugs@FDA, and drug shortages. The label endpoint (`/drug/label.json`)
is the workhorse; it returns Structured Product Labeling data for prescription and OTC drugs,
broken into sections like indications, adverse reactions, and drug interactions.
[Fda](https://open.fda.gov/apis/drug/) [Fda](https://open.fda.gov/apis/drug/label/)

**NPPES NPI Registry** — live provider lookup at `https://npiregistry.cms.hhs.gov/api/`
(query-only, updated daily, no key). **This is the only route to provider data in this project**
— the 4 GB bulk file is deliberately not downloaded, for the reasons above.

**HealthCare.gov Content API** — doubles as a live tool: it's CORS-enabled and supports
cross-domain requests, returning content objects, collections, and a site-wide index. Handy for
pulling the freshest official explanation of a concept at query time.
[HealthCare.gov](https://www.healthcare.gov/developers/)

**General web search tool** — this is where your agent goes when the question isn't answerable
from the corpus or a structured endpoint: "any recent news on drug X," "did this insurer have a
market-conduct action," "what changed for the 2026 plan year that isn't in my index yet." This is
the part that makes it an *agent* rather than a RAG bot.

> ### ⚠️ Licensing note (matters because this repo is public)
> CMS **NCDs** and the **HealthCare.gov content** are freely reusable. But the Medicare Coverage
> Database **LCDs and Billing/Coding Articles embed AMA CPT/HCPCS and ADA CDT codes, which are
> copyrighted**. Keep those code tables **out** of the public repo — index NCDs only. The PUFs
> (Exchange and Part D) are public-domain and safe to vendor. openFDA and NPPES data would also
> be safe — NPPES is FOIA-disclosable even though it names real practitioners — but the question
> is moot here, since both are used live rather than vendored.

## Cross-cutting principles (apply from day one)

- [ ] **Eval harness is the through-line.** Each phase adds exactly one new *thing to grade*:
      retrieval → answer → routing → tri-modal routing → multi-hop + citations → regression. You
      want the harness before the agent, not after.
- [ ] **Source-type tagging.** Every claim in an answer is labeled by lane (reference /
      structured-API / web) with the retrieval or URL behind it. Introduced in Phase 1,
      formalized in Phase 4 — this is what keeps every later phase evaluable, and for a health
      tool it's a trust requirement, not polish.
- [ ] **Synthetic fixtures.** Keep a fixtures set of fake people / plans / drugs / ZIPs so
      demos and evals never depend on live API availability or touch anything sensitive.
- [ ] **Pin the plan year** in every query. CMS keeps multiple years live at once; mixing them
      silently is the most common correctness bug in this domain.
- [ ] **The UI tracks the phases, it doesn't lead them.** A FastAPI + web frontend is part of the
      build from Phase 0 onward, but each phase only adds the surface for the capability that
      phase ships. The API contract is fixed early (Phase 0) so that later phases add *values* to
      existing fields rather than reshaping the response. **All frontend design decisions,
      stack choices, API schemas, and UI specifics live in [frontend_plan.md](frontend_plan.md)
      — this document records only *when* each piece lands.**

## Architecture phases

### Phase 0 — Corpus + eval scaffold (before any agent)

Download and prepare the raw corpus — HealthCare.gov content JSON + Medicare & You + NCDs — into
a local `data/` directory (download → parse → chunk into `data/processed`). No embeddings, no
vector store yet. In parallel, build a tiny gold eval set of ~30 questions with known answers and
known correct source-type. This pays off immediately: you want the corpus and the harness before
the agent, not after.

This is also where the **frontend groundwork** happens: freeze the API contract and stand up a
web UI against a stubbed answer endpoint. Doing it now — before there's an agent — means the
whole interface is proven while the stakes are zero, and Phase 1a only has to swap the stub for
a real agent. See [frontend_plan.md](frontend_plan.md) (Phase F0) for the stack, contract, and
layout; none of it is repeated here.

**Milestone / acceptance test:** you can run the ingestion pipeline and get a clean, chunked
corpus on disk, plus a gold eval set you can load and inspect — and a local web UI that renders
a stubbed answer end to end.

**User-facing capability**
- [ ] Run the ingestion pipeline and get a clean, chunked corpus written to `data/processed`
- [ ] Load and inspect the gold eval set (question → expected source-type → expected answer)
- [ ] Open the web UI locally and see a stubbed answer render with citations and a tool trace
- *(The "user" here is you-as-developer preparing data — no retrieval or synthesized answers yet.)*

**Software capability**
- [ ] Ingestion pipeline (download → parse → chunk → store to `data/processed`) that is idempotent and re-runnable
- [ ] Gold eval set (~30 questions), each tagged with expected source and answer
- [ ] Eval dataset loader / schema (so later phases can attach retrieval, answer, and routing metrics)
- [ ] **Frozen API contract** — request/response models carrying answer, citations, source-type, and abstention as first-class fields
- [ ] FastAPI app skeleton with a stubbed answer endpoint returning canned data
- [ ] Web UI scaffolded and rendering that stub

### Phase 1 — RAG-only MVP

Single tool: `retrieve(query)` over the corpus. Agent answers coverage/terminology questions with
citations back to chunks. No web, no APIs yet. Ship it. This alone is useful and proves your
retrieval quality. Split into two sub-phases so you first prove the RAG loop with the simplest
possible retrieval, then swap in a vector database behind the same interface.

#### Phase 1-a — RAG without a vector database (full-text search)

Retrieve using plain full-text techniques over `data/processed` — `ls`, `grep`, keyword/BM25-style
lexical search — **no vector database and no embeddings**. The point is to stand up the whole
agent → retrieve → cite → abstain loop against the simplest retrieval backend, and to have a
lexical baseline you can later compare the vector approach against.

**Milestone / acceptance test:** you can ask a coverage/terminology question **in the browser**
and get a cited answer sourced from full-text search over `data/processed`, and it abstains when
the question is out of corpus.

This is where the Phase 0 stub gets replaced by the real agent — the UI itself barely changes,
which is the point of having frozen the contract first. Frontend detail:
[frontend_plan.md](frontend_plan.md) (Phase F1).

**User-facing capability**
- [ ] Ask natural-language questions (*"what's a deductible?"*, *"does Medicare cover X?"*) and get a synthesized answer with citations to source documents
- [ ] Get an honest *"not in my reference material"* when the question is out of corpus — no hallucinated answer
- [ ] Do all of the above from the web UI, with the answer streaming in as it's generated
- [ ] Expand any citation to see the retrieved text behind it

**Software capability**
- [ ] PydanticAI agent with a single `retrieve` tool backed by full-text search (grep / lexical / BM25) over `data/processed` — no vector DB, no embeddings
- [ ] Structured output (answer + citation list)
- [ ] Chunk → source provenance plumbing
- [ ] Grounding guardrail: answer only from retrieved context
- [ ] Eval set extended from retrieval-only to **answer correctness** and **faithfulness/groundedness**
- [ ] Stub endpoint replaced by the real agent; streaming response wired through to the UI
- [ ] Eval dashboard in the UI over real eval runs

#### Phase 1-b — RAG with a vector database

Swap the retrieval backend behind the same `retrieve` interface for embeddings + a local vector
store — **LanceDB**, embedded and on-disk, with embeddings computed by us and handed over as
plain vectors. Reuse the Phase 1-a agent, provenance, and eval set — only the retrieval
implementation changes — so you can measure semantic vs. lexical retrieval on the same gold
questions. LanceDB holds vector *and* BM25 search in one table, so the 1-a lexical baseline and
the 1-b vector run can share a store instead of the comparison straddling two systems. See
[lancedb.md](lancedb.md) for the full rationale, the rejected alternatives (Chroma, pgvector,
managed cloud services), and the ingestion/query usage pattern.

**Milestone / acceptance test:** the same questions now route through vector retrieval, and you
can compare retrieval/answer quality against the Phase 1-a full-text baseline.

**User-facing capability**
- [ ] Same Q&A experience as Phase 1-a, now answering from semantic (vector) retrieval

**Software capability**
- [ ] Embedding model configured — same model for documents and queries, fixed dimensionality
- [ ] LanceDB wired up, populated from `data/processed`
- [ ] `retrieve` tool re-backed by vector search behind the same interface
- [ ] Eval comparison: vector vs. full-text baseline on the same gold set (recall@k, MRR, answer correctness)
- [ ] Eval dashboard gains a **run-comparison view** so the vector-vs-lexical call is made from data, not vibes — *the only frontend work this phase needs; the chat UI is untouched by design*

### Phase 2 — Add the web-search tool

Now the agent has two tools and must *choose*. This is the first real routing decision: "is this
in my indexed reference material, or do I need the open web?" Add an eval slice specifically for
routing correctness (did it pick the right lane?), separate from answer correctness.

**Milestone / acceptance test:** you can ask something not in the corpus and get a real
web-sourced answer — and the system chose the right lane on its own.

**User-facing capability**
- [ ] Ask time-sensitive / out-of-corpus questions (*"recent news on [drug]"*, *"2026 enrollment deadline"*) and get an answer
- [ ] See whether each answer came from reference material or the web

**Software capability**
- [ ] Web-search tool integrated
- [ ] Router / tool-selection layer where the agent decides RAG vs. web
- [ ] Source-type tagging in the output
- [ ] New eval slice measuring **routing correctness** (did it pick the right lane?), separate from answer correctness
- [ ] Basic web-result hygiene (dedupe, source filtering)
- [ ] UI: the `web` source badge goes live alongside `reference`, and routing accuracy joins the eval dashboard — see [frontend_plan.md](frontend_plan.md) (Phase F2)

### Phase 3 — Add structured-API tools

Wrap the Marketplace API (plan/drug/provider lookups), openFDA (drug facts/recalls), and NPPES
(provider lookup) as typed tools. Now it's genuinely tri-modal. The interesting failure mode to
eval here: the agent reaching for web search when a deterministic API would've given an exact
answer, or vice versa.

**Milestone / acceptance test:** you can ask for exact facts about a specific plan, drug, or
provider and get a deterministic answer, not prose from a document.

**User-facing capability**
- [ ] Run precise lookups:
  - *"find plans in ZIP 30076 for a family of 3"*
  - *"is drug X covered under plan Y"*
  - *"what's this NPI's specialty"*
  - *"has drug X been recalled"*

**Software capability**
- [ ] Typed tool wrappers (Pydantic models) for Marketplace API, openFDA, and NPPES
- [ ] API-key / secrets management
- [ ] Rate-limit handling, retries, and a response cache
- [ ] Synthetic fixtures so tests/evals don't depend on live APIs
- [ ] Tri-modal routing (reference vs. structured-API vs. web) with an eval slice for it
- [ ] Schema validation on every API response
- [ ] UI: the `structured-API` badge goes live, completing the three-lane vocabulary; plan-year selector wired to every request — see [frontend_plan.md](frontend_plan.md) (Phase F2)

### Phase 4 — Multi-step agent + provenance

Let it chain: decompose a compound question, hit multiple tools, synthesize. Build in the habit
from the start of tagging every claim in the final answer by **source type** (indexed-reference
vs. structured-API vs. web) with the retrieval/URL behind it. For a health tool this isn't
optional polish — it's what makes it trustworthy and what makes it evaluable. Same loop-safety
pattern as the appetite-engine reroute loop.

**Milestone / acceptance test:** you can ask a compound question that needs several lookups and
get one synthesized answer where every claim is traceable.

**User-facing capability**
- [ ] Ask multi-part questions (*"I take [drug] and live in [ZIP] — which marketplace plans cover it and what would they cost?"*) and get a single synthesized answer
- [ ] Inspect the tool trace to see how it got there
- [ ] Every claim carries a source-type label and the retrieval/URL behind it

**Software capability**
- [ ] Multi-step agent loop (plan → act → observe → synthesize) with usage/step limits
- [ ] Question decomposition
- [ ] Per-claim provenance tagging
- [ ] Observability / tracing (Logfire or similar): tool calls, latencies, token usage
- [ ] Multi-hop correctness and citation-accuracy evals
- [ ] Loop safety: cycle detection + hop ceiling
- [ ] UI: trace panel handles nested multi-hop steps; hovering a claim highlights exactly the sources behind it — see [frontend_plan.md](frontend_plan.md) (Phase F3)

### Phase 5 — Growth surface

Once the tri-modal core is solid, the functionality tree is long: plan comparison across the PUFs,
formulary/drug-cost lookup, provider-network checks, appeals guidance under the No Surprises Act,
and a scheduled "what changed for this plan year" monitor (which turns the whole thing from a Q&A
bot into a monitoring product). Each is additive and doesn't disturb the core.

**Milestone / acceptance test:** it stops being a single-shot Q&A bot and becomes a tool —
comparisons, cost breakdowns, and scheduled monitoring.

**User-facing capability**
- [ ] Plan comparison tables
- [ ] Drug-cost breakdowns across the PUFs
- [ ] Provider-network checks
- [ ] Appeals guidance (e.g., No Surprises Act)
- [ ] Scheduled "what changed for this plan year" monitor that alerts on diffs

**Software capability**
- [ ] Structured backend for the PUFs (DuckDB / SQLite) with query tools over it
- [ ] Comparison / aggregation logic
- [ ] Scheduler for monitoring runs + state persistence to diff against
- [ ] Alert / output channel
- [ ] UI: comparison tables, cost breakdowns, and a "what changed" view — the first phase whose UI is more than chat + provenance — see [frontend_plan.md](frontend_plan.md) (Phase F4)
- [ ] Regression eval suite that grows with each capability so earlier phases don't silently break

## Suggested build order recap

```
           backend                              frontend
Phase 0    corpus + gold eval set               API contract frozen, UI on a stub
Phase 1a   cited answers, full-text search      stub → real agent    [SHIPPABLE MVP]
Phase 1b   cited answers, vector retrieval      eval run comparison
Phase 2    RAG-vs-web routing                   web badge + routing metrics
Phase 3    typed API tools                      API badge    [tri-modal core complete]
Phase 4    multi-hop + full provenance          multi-hop trace, per-claim highlight
Phase 5    comparisons, cost, monitoring        tables + monitor    [product, not bot]
```

The tri-modal core is complete at the end of Phase 3 — everything after that is additive and
should not disturb the core.

The frontend column is a schedule, not a spec. Stack, API schemas, UI layout, and the
corresponding F0–F4 checklists live in [frontend_plan.md](frontend_plan.md).
