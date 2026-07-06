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

### Bulk-downloadable RAG corpus

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

**Medicare Coverage Database — National Coverage Determinations (NCDs).** The MCD offers bulk ZIP
downloads of the full NCD, LCD, and Article datasets, refreshed weekly. The Downloads page
provides data sets containing Local Coverage data (Articles and LCDs) or National Coverage data
(NCDs), with "All" or "Current" options for local coverage. **Licensing caveat:** stick to NCDs
for your public corpus. NCDs don't contain procedure codes. The LCDs and Billing/Coding Articles
do — and CPT codes and similar are copyrighted by the AMA and ADA, with use restricted under
license. So the local-coverage code tables are the one thing you should *not* vendor into a public
GitHub repo. [CMS](https://www.cms.gov/medicare-coverage-database/downloads/downloads.aspx)
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

**Medicare Part D formulary files (bulk drug coverage).** If you go deeper on the drug-cost angle,
CMS publishes quarterly formulary/pharmacy/pricing files. The Quarterly Prescription Drug Plan
Formulary, Pharmacy Network, and Pricing files contain formulary details including NDCs,
cost-share tier, and indicators for step therapy, quantity limits, and prior authorization.
[CMS Data](https://data.cms.gov/provider-summary-by-type-of-service/medicare-part-d-prescribers/quarterly-prescription-drug-plan-formulary-pharmacy-network-and-pricing-information)

**NPPES provider registry (bulk).** For provider lookups you can download the whole thing: NPPES
data is available as a downloadable file, updated weekly and monthly, with each file containing
the most current information. Heads up on size — the data dissemination file exceeds 4 GB, so
you'll want to reshape it (the NBER mirror offers a slimmer "core" version if you want a lighter
dev fixture). [ResDAC](https://resdac.org/articles/overview-nppesnpi-downloadable-file)
[CMS](https://www.cms.gov/medicare/regulations-guidance/administrative-simplification/data-dissemination)

**openFDA bulk (optional).** Beyond the API, openFDA endpoint data can be downloaded as zipped
JSON files in the same format as API responses — useful if you want drug labels in your index
rather than hitting the API live. [Fda](https://open.fda.gov/apis/drug/label/download/)

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
(query-only, updated daily). Use this instead of the 4 GB bulk file when you just need one
provider.

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
> copyrighted**. Keep those code tables **out** of the public repo — index NCDs only. openFDA,
> the PUFs, and NPPES (FOIA-disclosable) are all safe to vendor.

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

## Architecture phases

### Phase 0 — Corpus + eval scaffold (before any agent)

Download and prepare the raw corpus — HealthCare.gov content JSON + Medicare & You + NCDs — into
a local `data/` directory (download → parse → chunk into `data/processed`). No embeddings, no
vector store yet. In parallel, build a tiny gold eval set of ~30 questions with known answers and
known correct source-type. This pays off immediately: you want the corpus and the harness before
the agent, not after.

**Milestone / acceptance test:** you can run the ingestion pipeline and get a clean, chunked
corpus on disk, plus a gold eval set you can load and inspect.

**User-facing capability**
- [ ] Run the ingestion pipeline and get a clean, chunked corpus written to `data/processed`
- [ ] Load and inspect the gold eval set (question → expected source-type → expected answer)
- *(The "user" here is you-as-developer preparing data — no retrieval or synthesized answers yet.)*

**Software capability**
- [ ] Ingestion pipeline (download → parse → chunk → store to `data/processed`) that is idempotent and re-runnable
- [ ] Gold eval set (~30 questions), each tagged with expected source and answer
- [ ] Eval dataset loader / schema (so later phases can attach retrieval, answer, and routing metrics)

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

**Milestone / acceptance test:** you can ask a coverage/terminology question and get a cited
answer sourced from full-text search over `data/processed`, and it abstains when the question is
out of corpus.

**User-facing capability**
- [ ] Ask natural-language questions (*"what's a deductible?"*, *"does Medicare cover X?"*) and get a synthesized answer with citations to source documents
- [ ] Get an honest *"not in my reference material"* when the question is out of corpus — no hallucinated answer

**Software capability**
- [ ] PydanticAI agent with a single `retrieve` tool backed by full-text search (grep / lexical / BM25) over `data/processed` — no vector DB, no embeddings
- [ ] Structured output (answer + citation list)
- [ ] Chunk → source provenance plumbing
- [ ] Grounding guardrail: answer only from retrieved context
- [ ] Eval set extended from retrieval-only to **answer correctness** and **faithfulness/groundedness**

#### Phase 1-b — RAG with a vector database

Swap the retrieval backend behind the same `retrieve` interface for embeddings + a local vector
store (Chroma / LanceDB / pgvector). Reuse the Phase 1-a agent, provenance, and eval set — only
the retrieval implementation changes — so you can measure semantic vs. lexical retrieval on the
same gold questions.

**Milestone / acceptance test:** the same questions now route through vector retrieval, and you
can compare retrieval/answer quality against the Phase 1-a full-text baseline.

**User-facing capability**
- [ ] Same Q&A experience as Phase 1-a, now answering from semantic (vector) retrieval

**Software capability**
- [ ] Embedding model configured
- [ ] Vector database wired up (Chroma / LanceDB / pgvector), populated from `data/processed`
- [ ] `retrieve` tool re-backed by vector search behind the same interface
- [ ] Eval comparison: vector vs. full-text baseline on the same gold set (recall@k, MRR, answer correctness)

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
- [ ] UI beyond the CLI
- [ ] Regression eval suite that grows with each capability so earlier phases don't silently break

## Suggested build order recap

```
Phase 0  →  downloaded + chunked corpus, gold eval set
Phase 1a →  cited answers via full-text search (no vector DB)  [SHIPPABLE MVP]
Phase 1b →  cited answers via vector retrieval
Phase 2  →  RAG-vs-web routing
Phase 3  →  exact lookups via typed API tools         [tri-modal core complete]
Phase 4  →  multi-hop reasoning + full provenance
Phase 5  →  comparisons, cost, monitoring             [product, not bot]
```

The tri-modal core is complete at the end of Phase 3 — everything after that is additive and
should not disturb the core.
