# Health Coverage Navigator

Created by: Andy Tseng
Created time: July 3, 2026 2:55 PM
Status: In Progress
Tags: Learn

# Planning

## Resources

- Google Drive
    - [https://drive.google.com/drive/folders/1DR4dGU8eLrRXLk_Hsfz68sgAQ4ySMqp4?usp=drive_link](https://drive.google.com/drive/folders/1DR4dGU8eLrRXLk_Hsfz68sgAQ4ySMqp4?usp=drive_link)
- 

## What does it do

Nearly everyone deals with health insurance, so impact is enormous. The public-data story is the best in all of insurance. RAG over genuinely public reference material: the Medicare & You handbook, CMS coverage determinations (the Medicare Coverage Database of national/local determinations is a deep, structured, public corpus), ACA marketplace rules, and Summary of Benefits & Coverage documents (which use a federally standardized public template). Live public tools: the [Healthcare.gov](http://healthcare.gov/) Marketplace plan API, the NPI registry for provider lookup, and openFDA / drug-pricing data. Web search for the fresh half — a specific drug's recent news, a provider, a plan-year change. Answers things like "is this treatment typically covered, what plans cover my doctor, and what changed for this plan year?" Growth path is long: plan comparison → formulary/drug-cost lookup → provider-network checks → appeals guidance under the No Surprises Act.

## Bulk Downloadable sources

**HealthCare.gov consumer-education content (the cleanest starting corpus).** HealthCare.gov publishes every article and glossary term as machine-readable JSON, explicitly for third-party reuse. All educational content is available in machine-readable formats, published as HTML pages and JSON data, and everything is accessible through an API. There's a site-wide content index endpoint plus per-post JSON (you append `.json` to any post URL). This is your best MVP corpus: clean, unambiguously reusable, and written at exactly the "explain coverage to a human" level. [HealthCare.gov](https://www.healthcare.gov/developers/)

**Medicare & You handbook + related CMS guides.** The annual handbook is a US-government work (public domain), distributed as PDF. Great for the Medicare side of the domain. Pair it with the HealthCare.gov glossary for terminology grounding.

**Medicare Coverage Database — National Coverage Determinations (NCDs).** The MCD offers bulk ZIP downloads of the full NCD, LCD, and Article datasets, refreshed weekly. The Downloads page provides data sets containing Local Coverage data (Articles and LCDs) or National Coverage data (NCDs), with "All" or "Current" options for local coverage. **Licensing caveat:** stick to NCDs for your public corpus. NCDs don't contain procedure codes. The LCDs and Billing/Coding Articles do — and CPT codes and similar are copyrighted by the AMA and ADA, with use restricted under license. So the local-coverage code tables are the one thing you should *not* vendor into a public GitHub repo. [CMS](https://www.cms.gov/medicare-coverage-database/downloads/downloads.aspx)[Noridian](https://med.noridianmedicare.com/web/jea/policies/ncd)

**Health Insurance Exchange Public Use Files (the structured plan corpus).** These are the bulk CSV/ZIP dumps behind the ACA marketplace, and they're actively maintained. The Exchange PUFs are available for plan years 2014 through 2026, with the Benefits and Cost Sharing PUF updated April 28, 2026. The two you'll care about most: the Benefits and Cost Sharing PUF and the Plan Attributes PUF. The Plan Attributes PUF contains plan-level data on max out-of-pocket, deductibles, cost sharing, HSA eligibility, and formulary ID. These are large enough that the Rate PUF and Benefits and Cost Sharing PUF exceed Excel's row limit and need a database or statistical tool to open — good, because loading them into DuckDB/SQLite is exactly the kind of structured-tool backend you want to practice against. [Centers for Medicare & Medicaid Services + 2](https://www.cms.gov/marketplace/resources/data/public-use-files)

**Medicare Part D formulary files (bulk drug coverage).** If you go deeper on the drug-cost angle, CMS publishes quarterly formulary/pharmacy/pricing files. The Quarterly Prescription Drug Plan Formulary, Pharmacy Network, and Pricing files contain formulary details including NDCs, cost-share tier, and indicators for step therapy, quantity limits, and prior authorization. [CMS Data](https://data.cms.gov/provider-summary-by-type-of-service/medicare-part-d-prescribers/quarterly-prescription-drug-plan-formulary-pharmacy-network-and-pricing-information)

**NPPES provider registry (bulk).** For provider lookups you can download the whole thing: NPPES data is available as a downloadable file, updated weekly and monthly, with each file containing the most current information. Heads up on size — the data dissemination file exceeds 4 GB, so you'll want to reshape it (the NBER mirror offers a slimmer "core" version if you want a lighter dev fixture). [ResDAC](https://resdac.org/articles/overview-nppesnpi-downloadable-file)[CMS](https://www.cms.gov/medicare/regulations-guidance/administrative-simplification/data-dissemination)

**openFDA bulk (optional).** Beyond the API, openFDA endpoint data can be downloaded as zipped JSON files in the same format as API responses — useful if you want drug labels in your index rather than hitting the API live. [Fda](https://open.fda.gov/apis/drug/label/download/)

## Live Web / API Tools

This is the other two-thirds of your tool-routing problem: **structured public APIs** (deterministic lookups) and **general web search** (the open-ended, freshest layer).

**Marketplace API** — the big one. This is the API that literally powers HealthCare.gov shopping. The Marketplace API drives Window Shop and Plan Compare on HealthCare.gov, showing plans available based on location and household, whether plans cover specific providers and drugs, and estimated yearly costs. Interface highlights: [Cms](https://developer.cms.gov/marketplace-api/)

- Base: `https://marketplace.api.healthcare.gov/api/v1/`
- Drug autocomplete → RxCUI: `GET /drugs/autocomplete?q={query}&apikey={key}`
- Drug coverage check: `GET /drugs/covered?year={yr}&drugs={rxcui}&planids={planid}&apikey={key}`
- Plan search + cost estimates: `POST /households/eligibility/estimates`

You request a key via the CMS developer portal; note API keys are rate limited, with the limit passed back in the response headers. There's also a companion **Finder API** for private health plans available outside the Marketplace, with keys rate-limited to 1000 requests/minute and expiring every 60 days. [Cms](https://developer.cms.gov/marketplace-api/)[Cms](https://developer.cms.gov/finder-api/)

**openFDA** — drug facts, recalls, shortages. Base `https://api.fda.gov/`, no key needed to start. Drug endpoints cover adverse events, product labeling, the NDC directory, recall enforcement reports, the Orange Book, Drugs@FDA, and drug shortages. The label endpoint (`/drug/label.json`) is the workhorse; it returns Structured Product Labeling data for prescription and OTC drugs, broken into sections like indications, adverse reactions, and drug interactions. [Fda](https://open.fda.gov/apis/drug/)[Fda](https://open.fda.gov/apis/drug/label/)

**NPPES NPI Registry** — live provider lookup at `https://npiregistry.cms.hhs.gov/api/` (query-only, updated daily). Use this instead of the 4 GB bulk file when you just need one provider.

**HealthCare.gov Content API** — doubles as a live tool: it's CORS-enabled and supports cross-domain requests, returning content objects, collections, and a site-wide index. Handy for pulling the freshest official explanation of a concept at query time. [HealthCare.gov](https://www.healthcare.gov/developers/)

**General web search tool** — this is where your agent goes when the question isn't answerable from the corpus or a structured endpoint: "any recent news on drug X," "did this insurer have a market-conduct action," "what changed for the 2026 plan year that isn't in my index yet." This is the part that makes it an *agent* rather than a RAG bot.

The routing insight worth internalizing, because it's the actual engineering problem: almost every health-coverage question decomposes into three sub-types — **"what does the rule/benefit say"** (→ RAG), **"what's the specific fact for this plan/drug/provider"** (→ structured API), and **"what's happening now / not in my corpus"** (→ web search). Getting the agent to classify a sub-question into the right lane is the meat of the project, and it's directly borrowable from your binary-decomposition eval thinking — each sub-question becomes an independently gradable unit with a known correct source-type.

## Architecture phases

### **Phase 0 — Corpus + eval scaffold (before any agent).**

#### What

 Ingest HealthCare.gov content JSON + Medicare & You + NCDs into a vector store (start with something local — Chroma/LanceDB/pgvector). In parallel, build a tiny gold eval set of ~30 questions with known answers and known correct source-type. This pays off immediately and echoes your evalrunner approach — you want the harness before the agent, not after.

#### *Milestone:*

*you can paste a question and see which reference chunks come back, plus a retrieval score against a gold set.*

#### User-facing:

 run a question through a CLI/notebook and get back the top-k retrieved chunks with their source document; run the eval command and get a retrieval-quality number. No synthesized answers yet — the "user" here is really you-as-developer inspecting retrieval.

#### Software:

an ingestion pipeline (download → parse → chunk → embed → store) that's idempotent and re-runnable; a vector database wired up (Chroma/LanceDB/pgvector); an embedding model configured; a gold eval set of ~30 questions each tagged with expected source and answer; an eval runner reporting retrieval metrics (recall@k, MRR). This is the phase where your evalrunner instinct pays off — the harness exists before the agent does.

### **Phase 1 — RAG-only MVP.**

#### What

Single tool: `retrieve(query)` over the corpus. Agent answers coverage/terminology questions with citations back to chunks. No web, no APIs yet. Ship it. This alone is useful and proves your retrieval quality.

#### Milestone

Milestone: you can ask a coverage/terminology question and get a cited answer, and it abstains when the question is out of corpus.

#### User-facing

ask natural-language questions ("what's a deductible?", "does Medicare cover X?") and get a synthesized answer with citations to the source documents; get an honest "not in my reference material" when the question falls outside the corpus rather than a hallucinated answer.

#### Software

a PydanticAI agent with a single `retrieve` tool; structured output (answer + citation list); chunk→source provenance plumbing; a grounding guardrail that answers only from retrieved context; the eval set extended from retrieval-only to answer correctness and faithfulness/groundedness.

### **Phase 2 — Add the web-search tool.**

#### What

Now the agent has two tools and must *choose*. This is the first real routing decision: "is this in my indexed reference material, or do I need the open web?" Add an eval slice specifically for routing correctness (did it pick the right lane?), separate from answer correctness.

#### Milestone

you can ask something not in the corpus and get a real web-sourced answer — and the system chose the right lane on its own.

#### User-facing

Ask time-sensitive or out-of-corpus questions ("recent news on [drug]", "2026 enrollment deadline") and get an answer; see whether each answer came from reference material or the web.

#### Software

a web-search tool integrated; a router/tool-selection layer where the agent decides RAG vs. web; source-type tagging in the output; a new eval slice measuring *routing correctness* (did it pick the right lane?) separately from answer correctness; basic web-result hygiene (dedupe, source filtering).

### **Phase 3 — Add structured-API tools.**

#### What

 Wrap the Marketplace API (plan/drug/provider lookups), openFDA (drug facts/recalls), and NPPES (provider lookup) as typed tools. Now it's genuinely tri-modal. The interesting failure mode to eval here: the agent reaching for web search when a deterministic API would've given an exact answer, or vice versa.

#### Milestone

you can ask for exact facts about a specific plan, drug, or provider and get a deterministic answer, not prose from a document.

#### User-facing

run precise lookups — "find plans in ZIP 30076 for a family of 3", "is drug X covered under plan Y", "what's this NPI's specialty", "has drug X been recalled" — with exact answers.

#### Software

typed tool wrappers (Pydantic models) for the Marketplace API, openFDA, and NPPES; API-key/secrets management; rate-limit handling, retries, and a response cache; synthetic fixtures so tests and evals don't depend on live APIs; tri-modal routing (reference vs. structured-API vs. web) with an eval slice for it; schema validation on every API response.

**Phase 4 — Multi-step agent + provenance.** 

#### What

Let it chain: decompose a compound question, hit multiple tools, synthesize. Build in the habit from the start of tagging every claim in the final answer by **source type** (indexed-reference vs. structured-API vs. web) with the retrieval/URL behind it. For a health tool this isn't optional polish — it's what makes it trustworthy and what makes it evaluable.

#### Milestone

You can ask a compound question that needs several lookups and get one synthesized answer where every claim is traceable.

#### User-facing

 ask multi-part questions ("I take [drug] and live in [ZIP] — which marketplace plans cover it and what would they cost?") and get a single synthesized answer; inspect the tool trace to see how it got there; every claim carries a source-type label and the retrieval/URL behind it.

#### Software

a multi-step agent loop (plan → act → observe → synthesize) with usage/step limits; question decomposition; per-claim provenance tagging; observability/tracing (Logfire or similar) capturing tool calls, latencies, and token usage; multi-hop correctness and citation-accuracy evals; loop safety with cycle detection and a hop ceiling — the same pattern as your appetite-engine reroute loop.

### **Phase 5 — Growth surface.**

#### What

Once the tri-modal core is solid, the functionality tree is long: plan comparison across the PUFs, formulary/drug-cost lookup, provider-network checks, appeals guidance under the No Surprises Act, and a scheduled "what changed for this plan year" monitor (which turns the whole thing from a Q&A bot into a monitoring product). Each is additive and doesn't disturb the core.

#### Milestone

it stops being a single-shot Q&A bot and becomes a tool — comparisons, cost breakdowns, and scheduled monitoring.

#### User-facing

plan comparison tables, drug-cost breakdowns across the PUFs, provider-network checks, appeals guidance, and a scheduled "what changed for this plan year" monitor that alerts on diffs.

#### Software

a structured backend for the PUFs (DuckDB/SQLite) with query tools over it; comparison/aggregation logic; a scheduler for monitoring runs plus state persistence to diff against; an alert/output channel; likely a UI beyond the CLI; and a regression eval suite that grows with each new capability so earlier phases don't silently break.