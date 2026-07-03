# Health Coverage Navigator — Roadmap

An open-source AI agent that answers U.S. health-coverage questions by routing between three
tool "lanes": **RAG** over public reference documents, **structured public APIs** for exact
facts, and **web search** for the fresh, open-ended world. Built on PydanticAI, using only
public data.

The organizing idea: almost every health-coverage question decomposes into one of three
sub-types, and picking the right lane is the core engineering problem.

| Sub-question type | Example | Lane |
|---|---|---|
| "What does the rule/benefit say?" | *What is a deductible?* | **RAG** (indexed reference) |
| "What's the fact for this plan/drug/provider?" | *Is drug X covered under plan Y?* | **Structured API** |
| "What's happening now / not in my corpus?" | *Any recent recall on drug X?* | **Web search** |

Each phase below is independently shippable and has an **acceptance test** — the phase is done
when you can do the thing in the milestone line.

---

## Data sources (verified live; see licensing note)

**RAG corpus (bulk-downloadable reference text)**
- HealthCare.gov Content API — all consumer-education articles + glossary as JSON (also usable live)
- Medicare & You handbook + related CMS guides (PDF, public domain)
- Medicare Coverage Database — **National** Coverage Determinations (NCDs) bulk ZIP, weekly
- Health Insurance Exchange Public Use Files (PUFs) — Plan Attributes + Benefits/Cost Sharing (PY2014–2026)
- Medicare Part D quarterly formulary / pharmacy / pricing files (optional, drug-cost depth)

**Structured API tools (deterministic lookups)**
- Marketplace API — `https://marketplace.api.healthcare.gov/api/v1/` (plans, drug coverage, cost estimates; API key, rate-limited)
- openFDA — `https://api.fda.gov/` (drug labels, recalls, shortages, NDC; no key to start)
- NPPES NPI Registry — `https://npiregistry.cms.hhs.gov/api/` (live provider lookup, updated daily)

**Web search tool**
- General web search for time-sensitive / out-of-corpus questions

> ### ⚠️ Licensing note (matters because this repo is public)
> CMS **NCDs** and the **HealthCare.gov content** are freely reusable. But the Medicare Coverage
> Database **LCDs and Billing/Coding Articles embed AMA CPT/HCPCS and ADA CDT codes, which are
> copyrighted**. Keep those code tables **out** of the public repo — index NCDs only. openFDA,
> the PUFs, and NPPES (FOIA-disclosable) are all safe to vendor.

---

## Cross-cutting principles (apply from day one)

- [ ] **Eval harness is the through-line.** Each phase adds exactly one new *thing to grade*:
      retrieval → answer → routing → tri-modal routing → multi-hop + citations → regression.
- [ ] **Source-type tagging.** Every claim in an answer is labeled by lane (reference /
      structured-API / web) with the retrieval or URL behind it. Introduced in Phase 1,
      formalized in Phase 4 — this is what keeps every later phase evaluable.
- [ ] **Synthetic fixtures.** Keep a fixtures set of fake people / plans / drugs / ZIPs so
      demos and evals never depend on live API availability or touch anything sensitive.
- [ ] **Pin the plan year** in every query. CMS keeps multiple years live at once; mixing them
      silently is the most common correctness bug in this domain.

---

## Phase 0 — Corpus + eval scaffold

**Milestone / acceptance test:** you can paste a question and see which reference chunks come
back, plus a retrieval score against a gold set.

### User-facing capability
- [ ] Run a question through a CLI/notebook and get back the top-k retrieved chunks with their source document
- [ ] Run an eval command and get a retrieval-quality number
- *(The "user" here is you-as-developer inspecting retrieval — no synthesized answers yet.)*

### Software capability
- [ ] Ingestion pipeline (download → parse → chunk → embed → store) that is idempotent and re-runnable
- [ ] Vector database wired up (Chroma / LanceDB / pgvector)
- [ ] Embedding model configured
- [ ] Gold eval set (~30 questions), each tagged with expected source and answer
- [ ] Eval runner reporting retrieval metrics (recall@k, MRR)

---

## Phase 1 — RAG-only MVP

**Milestone / acceptance test:** you can ask a coverage/terminology question and get a cited
answer, and it abstains when the question is out of corpus.

### User-facing capability
- [ ] Ask natural-language questions (*"what's a deductible?"*, *"does Medicare cover X?"*) and get a synthesized answer with citations to source documents
- [ ] Get an honest *"not in my reference material"* when the question is out of corpus — no hallucinated answer

### Software capability
- [ ] PydanticAI agent with a single `retrieve` tool
- [ ] Structured output (answer + citation list)
- [ ] Chunk → source provenance plumbing
- [ ] Grounding guardrail: answer only from retrieved context
- [ ] Eval set extended from retrieval-only to **answer correctness** and **faithfulness/groundedness**

---

## Phase 2 — Add the web-search tool

**Milestone / acceptance test:** you can ask something not in the corpus and get a real
web-sourced answer — and the system chose the right lane on its own.

### User-facing capability
- [ ] Ask time-sensitive / out-of-corpus questions (*"recent news on [drug]"*, *"2026 enrollment deadline"*) and get an answer
- [ ] See whether each answer came from reference material or the web

### Software capability
- [ ] Web-search tool integrated
- [ ] Router / tool-selection layer where the agent decides RAG vs. web
- [ ] Source-type tagging in the output
- [ ] New eval slice measuring **routing correctness** (did it pick the right lane?), separate from answer correctness
- [ ] Basic web-result hygiene (dedupe, source filtering)

---

## Phase 3 — Add structured-API tools

**Milestone / acceptance test:** you can ask for exact facts about a specific plan, drug, or
provider and get a deterministic answer, not prose from a document.

### User-facing capability
- [ ] Run precise lookups:
  - *"find plans in ZIP 30076 for a family of 3"*
  - *"is drug X covered under plan Y"*
  - *"what's this NPI's specialty"*
  - *"has drug X been recalled"*

### Software capability
- [ ] Typed tool wrappers (Pydantic models) for Marketplace API, openFDA, and NPPES
- [ ] API-key / secrets management
- [ ] Rate-limit handling, retries, and a response cache
- [ ] Synthetic fixtures so tests/evals don't depend on live APIs
- [ ] Tri-modal routing (reference vs. structured-API vs. web) with an eval slice for it
- [ ] Schema validation on every API response

---

## Phase 4 — Multi-step agent + provenance

**Milestone / acceptance test:** you can ask a compound question that needs several lookups and
get one synthesized answer where every claim is traceable.

### User-facing capability
- [ ] Ask multi-part questions (*"I take [drug] and live in [ZIP] — which marketplace plans cover it and what would they cost?"*) and get a single synthesized answer
- [ ] Inspect the tool trace to see how it got there
- [ ] Every claim carries a source-type label and the retrieval/URL behind it

### Software capability
- [ ] Multi-step agent loop (plan → act → observe → synthesize) with usage/step limits
- [ ] Question decomposition
- [ ] Per-claim provenance tagging
- [ ] Observability / tracing (Logfire or similar): tool calls, latencies, token usage
- [ ] Multi-hop correctness and citation-accuracy evals
- [ ] Loop safety: cycle detection + hop ceiling

---

## Phase 5 — Growth surface

**Milestone / acceptance test:** it stops being a single-shot Q&A bot and becomes a tool —
comparisons, cost breakdowns, and scheduled monitoring.

### User-facing capability
- [ ] Plan comparison tables
- [ ] Drug-cost breakdowns across the PUFs
- [ ] Provider-network checks
- [ ] Appeals guidance (e.g., No Surprises Act)
- [ ] Scheduled "what changed for this plan year" monitor that alerts on diffs

### Software capability
- [ ] Structured backend for the PUFs (DuckDB / SQLite) with query tools over it
- [ ] Comparison / aggregation logic
- [ ] Scheduler for monitoring runs + state persistence to diff against
- [ ] Alert / output channel
- [ ] UI beyond the CLI
- [ ] Regression eval suite that grows with each capability so earlier phases don't silently break

---

## Suggested build order recap

```
Phase 0  →  retrieval you can inspect + measure
Phase 1  →  cited answers, honest abstention          [SHIPPABLE MVP]
Phase 2  →  RAG-vs-web routing
Phase 3  →  exact lookups via typed API tools         [tri-modal core complete]
Phase 4  →  multi-hop reasoning + full provenance
Phase 5  →  comparisons, cost, monitoring             [product, not bot]
```

The tri-modal core is complete at the end of Phase 3 — everything after that is additive and
should not disturb the core.
