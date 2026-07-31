# Glossary

This document decodes every health-insurance, medical, and US-regulatory term the repo uses.
It is the fourth of the four docs — [`plan.md`](plan.md) says *what to build and when*,
[`frontend_plan.md`](frontend_plan.md) says *how the web UI works*,
[`progress.md`](progress.md) says *what is actually built*, and this one says *what the words
mean*. It records no status and no design; when a term's meaning changes, it changes here.

Entries gloss the term **as this repo uses it** — its licensing status, which routing lane it
belongs to, which schema field it maps to — not just the dictionary expansion. Where a term
carries a consequence (a public-repo blocklist, a correctness rule), the entry states it.

> **This is not a consumer glossary.** 256 official HealthCare.gov glossary definitions are
> already vendored under `data/raw/healthcare_gov/posts/glossary_*.json` and are the
> authoritative source for consumer benefit vocabulary — *actuarial value*, *SLCSP*, *MAGI*,
> *metal tiers*, and the rest. This file covers the terms that appear in **our own docs, code,
> and data pipeline**, and deliberately does not duplicate that corpus. See
> [Reference](#reference).

---

## Agencies and organizations

| Term | Expansion | What it means in this repo |
|---|---|---|
| **CMS** | Centers for Medicare & Medicaid Services | The federal agency inside HHS that runs Medicare, Medicaid, CHIP, and HealthCare.gov. Publisher of nearly every source we ingest: the Medicare publications catalog, the Medicare Coverage Database, the Exchange PUFs, NPPES, and the Marketplace API. Also a field value — `pub_id` is a "CMS product number", `category` a "CMS topic category". |
| **HHS** | (US Department of) Health and Human Services | CMS's parent department. Appears in the repo mainly inside the NPPES hostname `npiregistry.cms.hhs.gov`. |
| **MAC** | Medicare Administrative Contractor | A regional private contractor that processes Medicare claims for its jurisdiction and issues **LCDs** where no NCD governs. Why coverage can differ by geography. `Noridian`, linked from [`plan.md`](plan.md), is one. |
| **AMA** | American Medical Association | Owns and copyrights **CPT**. The reason LCDs and Billing/Coding Articles are blocked from this public repo. |
| **ADA** | American Dental Association | Owns and copyrights **CDT**, the dental code set. Blocked for the same reason as CPT. |
| **FDA** | (US) Food and Drug Administration | Drug and device regulator. Reached through **openFDA**, never scraped. |
| **openFDA** | — | FDA's public API and bulk-download service at `https://api.fda.gov/`. Keyless, no registration. Supplies drug labels, recalls, and shortages in the `structured_api` lane. Always written lowercase-o, camel — `openFDA`, not `OpenFDA`. |
| **NCHS** | National Center for Health Statistics | The CDC unit that maintains **ICD-10-CM** for the United States under WHO authorization. |
| **WHO** | World Health Organization | Owns and publishes the base **ICD** classification that ICD-10-CM modifies. |
| **NBER** | National Bureau of Economic Research | Hosts a trimmed "core" mirror of the NPPES bulk file — the practical dev fixture, since the full download is 4 GB+. |
| **ResDAC** | Research Data Assistance Center | CMS-funded resource that helps researchers use CMS data; linked from [`plan.md`](plan.md) for its NPPES downloadable-file overview. |
| **FOIA** | Freedom of Information Act | NPPES provider data is FOIA-disclosable, which is *why* it is safe to vendor despite naming real practitioners. |

---

## Programs and coverage

| Term | Expansion | What it means in this repo |
|---|---|---|
| **Medicare** | — | Federal health coverage for people 65+, and for some younger people with disabilities or ESRD. One of the two halves of the Phase 0 reference corpus; see [`medicare_pubs_data.md`](medicare_pubs_data.md). |
| **Part A** | Hospital Insurance | Inpatient hospital, skilled nursing facility, hospice, and some home health care. |
| **Part B** | Medical Insurance | Outpatient care, physician services, preventive services, and durable medical equipment. The corpus breadcrumb `Part B-covered services > Acupuncture` is a Part B example. |
| **Part C** | Medicare Advantage | Medicare benefits delivered through a private plan instead of Original Medicare, usually bundling Part D and extra benefits, in exchange for a provider network. |
| **Part D** | Prescription Drug Coverage | Outpatient drug coverage, sold as standalone **PDP** plans or bundled into Part C. Source of the quarterly formulary / pharmacy-network / pricing files. |
| **Original Medicare** | — | Parts A and B together, without a private plan. Any provider that accepts Medicare, no network. |
| **Medigap** | Medicare Supplement Insurance | Private policies that pay Original Medicare's cost-sharing gaps. Standardized into lettered plans. |
| **PDP** | Prescription Drug Plan | A standalone Part D plan. The unit of the quarterly formulary files. |
| **Extra Help / LIS** | Low-Income Subsidy | Federal help paying Part D premiums, deductibles, and copays. |
| **QMB** | Qualified Medicare Beneficiary | A Medicare Savings Program that pays Medicare premiums and cost sharing and bars providers from balance billing the beneficiary. |
| **PACE** | Program of All-Inclusive Care for the Elderly | Combined Medicare/Medicaid program delivering coordinated care to people who would otherwise need nursing-home care. |
| **Medicaid** | — | Joint federal-state coverage for people with low income. State-administered, so rules vary — a routing hazard, since a national corpus cannot answer a state-specific Medicaid question. |
| **CHIP** | Children's Health Insurance Program | Coverage for children in households earning too much for Medicaid. |
| **ACA** | Affordable Care Act | The 2010 law (formally the Patient Protection and Affordable Care Act) that created the Marketplace, essential health benefits, and premium tax credits. The ACA side of the corpus is HealthCare.gov; see [`health_care_data.md`](health_care_data.md). |
| **Marketplace** | Health Insurance Marketplace | The ACA individual-plan exchange — HealthCare.gov federally, or a state-run equivalent. Also called the **Exchange**. |
| **Exchange** | — | Synonym for Marketplace, and the term used in dataset names: the **Exchange PUFs**. |
| **QHP** | Qualified Health Plan | A plan certified to be sold on the Marketplace, meeting essential-health-benefit and cost-sharing rules. The unit of the Exchange PUFs. |
| **SHOP** | Small Business Health Options Program | The Marketplace's small-employer track. |
| **No Surprises Act** | — | 2022 law limiting balance billing for out-of-network emergency care and out-of-network providers at in-network facilities, and creating a patient-provider dispute process. A Phase 5 growth-surface capability (appeals guidance), not a Phase 0–4 concern. |
| **COBRA** | Consolidated Omnibus Budget Reconciliation Act | The right to keep employer coverage temporarily after leaving a job, at full unsubsidized cost. |
| **TRICARE** | — | Health coverage for uniformed-service members, retirees, and families. |

---

## Datasets, files, and APIs

| Term | Expansion | What it means in this repo |
|---|---|---|
| **MCD** | Medicare Coverage Database | CMS's database of coverage policy. **Partly clean**: NCDs are cleared to vendor, LCDs and Billing/Coding Articles are not. The canonical example of "vendor only the cleared subset". |
| **NCD** | National Coverage Determination | A nationwide CMS decision on whether Medicare covers a service. Binds every MAC. Contains **no procedure codes**, so it is **cleared for the public repo**. |
| **LCD** | Local Coverage Determination | A MAC's coverage decision for its own jurisdiction, issued where no NCD applies. Embeds AMA CPT/HCPCS and ADA CDT codes — **blocked from the public repo**. |
| **Billing/Coding Article** | — | MCD companion documents listing the codes that bill against a policy. Blocked for the same reason as LCDs. |
| **PUF** | Public Use File | CMS's bulk CSV/ZIP data dumps. Public-domain, safe to vendor, and large enough to need DuckDB or SQLite rather than a spreadsheet. |
| **Benefits and Cost Sharing PUF** | — | Per-plan benefit and cost-sharing detail for Marketplace QHPs. The Phase 5 plan-comparison backbone. |
| **Plan Attributes PUF** | — | Plan-level data: max out-of-pocket, deductibles, cost sharing, HSA eligibility, formulary ID. |
| **Rate PUF** | — | Per-plan premium rates by rating area and age. |
| **NPPES** | National Plan and Provider Enumeration System | CMS's registry of every US healthcare provider identifier. Available as a 4 GB+ bulk file or a live per-provider API. FOIA-disclosable, so safe to vendor. |
| **NPI** | National Provider Identifier | The 10-digit ID NPPES assigns to a provider. The lookup key for *"what is this NPI's specialty"* — a `structured_api` question, never a RAG one. |
| **Marketplace API** | — | CMS's API at `marketplace.api.healthcare.gov/api/v1/` for plan search, drug-coverage checks, and cost estimates. Drives Window Shop and Plan Compare on HealthCare.gov. **Requires an API key** from the CMS developer portal; rate-limited. |
| **Finder API** | — | The companion CMS API for private health plans sold *outside* the Marketplace. Separately keyed; keys are rate-limited to 1000 requests/minute and expire every 60 days. |
| **Content API** | HealthCare.gov Content API | The keyless, CORS-enabled JSON feed behind HealthCare.gov's consumer-education content — append `.json` to any post URL. **Not the Marketplace API**: it serves articles and glossary entries, not plans or prices. |
| **Window Shop / Plan Compare** | — | The HealthCare.gov shopping flows the Marketplace API powers. Useful as a model for what a structured plan-comparison answer should contain. |
| **Formulary file** | — | Quarterly Part D file listing each covered drug by **NDC**, its cost-share tier, and its utilization-management flags (prior authorization, step therapy, quantity limits). |
| **SPL** | Structured Product Labeling | The FDA's XML format for drug labeling. What openFDA's drug-label endpoint returns. |
| **NDC** | National Drug Code | The FDA identifier for a specific drug product, down to manufacturer and package. The join key between formulary files and openFDA. |
| **RxCUI** | RxNorm Concept Unique Identifier | The normalized drug-concept ID from NLM's RxNorm vocabulary. The Marketplace API's drug endpoints take RxCUIs, so a drug name is resolved via `/drugs/autocomplete` before `/drugs/covered` can be called. |
| **Orange Book** | — | FDA's list of approved drugs with therapeutic-equivalence ratings — which generics substitute for which brand. Available via openFDA. |
| **Drugs@FDA** | — | FDA's database of approved drug products and their application histories. Available via openFDA. |
| **SBC** | Summary of Benefits and Coverage | The standardized plan-summary document insurers must provide. Uses a federally standardized public template, which makes it parseable across issuers. |
| **corpus.jsonl** | — | The repo's processed-corpus format: one JSON record per chunk, written to `data/processed/<source>/`. |
| **bite** | — | A HealthCare.gov field name, not an industry term: the one-sentence editorial summary the site writes for each post. Useful as a chunk-level abstract. |

---

## Proprietary code systems

> ### ⚠️ These are the terms that decide what may be committed
> CPT, CDT, and HCPCS Level II are **third-party-copyrighted code tables under restricted
> license**. A dataset that embeds them may not enter this public repo, whatever else is true
> about it. This is the single rule most likely to be violated by accident, because the
> datasets that carry these codes (LCDs, Billing/Coding Articles) sit right next to ones that
> do not (NCDs). Read the
> [public-repo data guardrail](../CLAUDE.md#public-repo-data-guardrail-action-required-before-committing-data)
> before adding any data source.

| Term | Expansion | What it means in this repo |
|---|---|---|
| **HCPCS** | Healthcare Common Procedure Coding System | CMS's billing-code system, in two levels. **Level I is CPT itself** (AMA-owned). **Level II** is CMS-maintained and covers what CPT does not — drugs and biologicals, ambulance, and DMEPOS. Level II is nonetheless license-restricted for redistribution and is on the blocklist. |
| **CPT** | Current Procedural Terminology | The AMA's procedure code set, also known as HCPCS Level I. **Copyrighted by the AMA, license-restricted, blocked from this repo.** The reason we index NCDs only. |
| **CDT** | Code on Dental Procedures and Nomenclature | The ADA's dental procedure code set. Copyrighted by the ADA. Blocked, same as CPT. |
| **ICD** | International Classification of Diseases | The diagnosis-coding classification. The base ICD is owned and published by the **WHO**; the US clinical modification **ICD-10-CM** is maintained by NCHS/CDC under WHO authorization and released free, while **ICD-10-PCS** (inpatient procedures) is maintained by CMS. The repo's blocklist names ICD conservatively alongside CPT/CDT — treat any ICD code table as blocked unless you have confirmed its specific license, and ask rather than assume. |
| **DMEPOS** | Durable Medical Equipment, Prosthetics, Orthotics, and Supplies | The benefit category HCPCS Level II largely exists to code. |

---

## Benefit design and cost sharing

| Term | Expansion | What it means in this repo |
|---|---|---|
| **plan year** | — | The 12-month period a plan's benefits and prices apply to. **The repo's most-emphasized correctness rule**: CMS keeps multiple years live at once, so every query pins a year — a 2025 answer to a 2026 question is wrong, not merely stale. Surfaces as `plan_year` in the API contract and in `corpus.jsonl` records. |
| **benefit year / policy year** | — | Near-synonyms for plan year; a policy year tracks the policy's own effective date rather than the calendar year, so deductibles can reset off-cycle. |
| **premium** | — | The recurring amount paid to keep coverage, whether or not care is used. |
| **deductible** | — | What the member pays before the plan starts paying. The repo's canonical demo question — *"What is a deductible?"* — and the standing example of a `reference`-lane question. |
| **copayment (copay)** | — | A fixed dollar amount per service. |
| **coinsurance** | — | A percentage of the allowed amount, rather than a fixed dollar amount. |
| **cost sharing** | — | The umbrella term for deductible + copay + coinsurance — everything the member pays that is not premium. |
| **out-of-pocket maximum (MOOP)** | Maximum Out-Of-Pocket | The annual ceiling on cost sharing; past it the plan pays 100% of covered in-network care. A **Plan Attributes PUF** column. |
| **HSA** | Health Savings Account | A tax-advantaged account usable only with a high-deductible health plan. HSA eligibility is a Plan Attributes PUF column. |
| **formulary** | — | A plan's list of covered drugs and their tiers. Coverage is plan- *and* year-specific, which makes it a `structured_api` question, not a `reference` one. |
| **tier** | — | The formulary bucket that sets a drug's cost share — generic, preferred brand, non-preferred brand, specialty. |
| **prior authorization** | — | Plan approval required before a drug or service is covered. A formulary-file flag. |
| **step therapy** | — | A requirement to try a cheaper drug first before a costlier one is covered. A formulary-file flag. |
| **quantity limit** | — | A cap on how much of a drug is covered per period. A formulary-file flag. |
| **provider network** | — | The set of providers contracted with a plan. In-network versus out-of-network is the main driver of what a member actually pays. |
| **balance billing** | — | An out-of-network provider billing the member for the difference between their charge and what the plan paid. Restricted by the No Surprises Act in defined situations. |
| **open enrollment** | — | The annual window for enrolling or switching plans. Dates move year to year, so this is the repo's canonical **`web`-lane** eval question — a static corpus should not be trusted for it. |
| **special enrollment period (SEP)** | — | A mid-year enrollment window opened by a qualifying life event such as job loss, marriage, or birth. |
| **appeal** | — | The formal process for contesting a coverage or payment denial. Covered by the Medicare corpus in the "Rights and protections" category. |

---

## Clinical and service categories

These come from the Medicare publications taxonomy and appear as `category` values and
publication titles in `data/raw/medicare_pubs/catalog.json`.

| Term | Expansion | What it means in this repo |
|---|---|---|
| **SNF** | Skilled Nursing Facility | Short-term inpatient rehabilitative and skilled nursing care. A Part A benefit with day limits and a qualifying-hospital-stay rule — a frequent source of coverage confusion. |
| **DME** | Durable Medical Equipment | Reusable medical equipment used at home — wheelchairs, walkers, oxygen, hospital beds. A Part B benefit. |
| **ESRD** | End-Stage Renal Disease | Permanent kidney failure requiring dialysis or transplant. One of the conditions granting Medicare eligibility regardless of age. |
| **home health care** | — | Skilled nursing and therapy delivered at home to a homebound patient. |
| **hospice** | — | Comfort-focused care for terminal illness, replacing curative treatment for the terminal condition. |
| **preventive services** | — | Screenings, vaccines, and wellness visits, most covered at no cost sharing. The Medicare corpus's "Staying healthy" category. |
| **ALJ** | Administrative Law Judge | The third level of Medicare appeals. Appears in the corpus as a form title. |
| **Ombudsman** | Medicare Beneficiary Ombudsman | The office that helps beneficiaries with complaints and appeals. |
| **late enrollment penalty** | — | A permanent premium surcharge for enrolling in Part B or Part D after first eligibility without other creditable coverage. |

---

## Privacy and compliance

| Term | Expansion | What it means in this repo |
|---|---|---|
| **PII** | Personally Identifiable Information | Data identifying a specific person. **Never committed**, in any form, under any directory. |
| **PHI** | Protected Health Information | Individually identifiable health information — health status, care received, or payment for care, tied to a person. HIPAA-regulated and the most sensitive category this project could touch. **Never committed.** The project handles population-level and policy-level data only; it never ingests a real person's records. |
| **HIPAA** | Health Insurance Portability and Accountability Act | The law governing PHI. Not a compliance obligation we take on — the design avoids PHI entirely — but the reason the PHI line is absolute. |
| **SSN** | Social Security Number | The specific PII the corpus verification greps scan for, via the `[0-9]{3}-[0-9]{2}-[0-9]{4}` pattern. |
| **public-domain / U.S.-government work** | — | Works authored by the federal government carry no copyright and are safe to vendor. The clearing test for most sources here — but verify per source, since a government publisher can still embed third-party copyrighted tables. |
| **synthetic fixture** | — | Fake people, plans, drugs, and ZIP codes used in tests and evals. Exists so nothing sensitive is touched and so tests never depend on live, key-gated APIs. |

---

## Project vocabulary

Domain-adjacent terms with a specific meaning here. [`frontend_plan.md`](frontend_plan.md)
owns the frozen API contract these map into — this section names them, it does not restate
the schema.

| Term | What it means in this repo |
|---|---|
| **lane** | One of the three routing destinations a sub-question can go to. Choosing correctly is the central engineering problem of the project. |
| **`reference`** | The RAG lane, over the static public corpus. Answers "what does the rule or benefit say". |
| **`structured_api`** | The deterministic-lookup lane. Answers "what is the specific fact for this plan, drug, or provider". Written `structured-API` in prose, `structured_api` as a value. |
| **`web`** | The general-web-search lane. Answers "what is happening now" or anything outside the corpus. |
| **source type** | Which lane produced a claim. A first-class field on every answer, not a guess. |
| **abstain / `abstained`** | Declining to answer because the question falls outside the corpus. A **first-class boolean** on the response — never inferred by pattern-matching the answer text. Abstaining is a correct outcome, not a failure. |
| **groundedness / faithfulness** | Whether an answer's claims are actually supported by the retrieved context, as opposed to merely being true. Graded separately from correctness. |
| **provenance** | The chain from a claim back to its source. Two senses in this repo: *answer* provenance (which chunk or URL backs a claim) and *fetch* provenance (the `_meta.json` recording when and from where a file was downloaded). |
| **citation / claim / trace** | The three structured answer components: the sources cited, the individual assertions made, and the step-by-step record of how the agent got there. |
| **chunk** | One retrievable unit of corpus text — the granularity of both retrieval and citation. |
| **gold eval set** | The hand-written question set with known answers and known correct lane, used to grade the system. Built before the agent, not after. |
| **recall@k** | The fraction of questions whose correct chunk appears in the top *k* retrieved results. The Phase 0 retrieval metric. |
| **MRR** | Mean Reciprocal Rank — the average of 1/(rank of the first correct result). Rewards ranking the right chunk higher, which recall@k alone does not. |

---

## Reference

- [`CLAUDE.md`](../CLAUDE.md) — data sources, the public-repo guardrail, and the rule that keeps
  this file current.
- [`plan.md`](plan.md) — the routing lanes, the source catalog, and the phase schedule.
- [`health_care_data.md`](health_care_data.md) — the HealthCare.gov Content API source guide.
- [`medicare_pubs_data.md`](medicare_pubs_data.md) — the medicare.gov publications source guide.
- `data/raw/healthcare_gov/posts/glossary_*.json` — 256 official HealthCare.gov consumer
  definitions, already vendored. The authoritative source for benefit vocabulary this file
  does not cover.
- [CMS Medicare Coverage Determination Process](https://www.cms.gov/medicare/coverage/determination-process)
  — the official NCD/LCD distinction.
- [CMS Overview of Coding & Classification Systems](https://www.cms.gov/cms-guide-medical-technology-companies-and-other-interested-parties/coding/overview-coding-classification-systems)
  — the official HCPCS Level I/II split.
- [CDC/NCHS ICD-10-CM files](https://www.cdc.gov/nchs/icd/icd-10-cm/files.html) — the US
  clinical modification, published free.
- [CMS Developer Portal](https://developer.cms.gov/public-apis/) — Marketplace API and Finder
  API specifications and key requests.
