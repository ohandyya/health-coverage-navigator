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
| **AHA** | American Hospital Association | The third party in the AMA/ADA/**AHA** license the Coverage API demands for LCD and Article endpoints; it owns the ICD coding-advice material bundled into that agreement. Named here so the license triple reads as three distinct owners rather than boilerplate. |
| **FDA** | (US) Food and Drug Administration | Drug and device regulator. Reached through **openFDA**, never scraped. |
| **openFDA** | — | FDA's public API at `https://api.fda.gov/`. Keyless, no registration. Supplies drug labels, recalls, and shortages in the `structured_api` lane. It also publishes bulk JSON, which this repo **deliberately does not use** — the questions asked of it are per-drug lookups, so the API is the only route (see [`plan.md`](plan.md)). Always written lowercase-o, camel — `openFDA`, not `OpenFDA`. |
| **NCHS** | National Center for Health Statistics | The CDC unit that maintains **ICD-10-CM** for the United States under WHO authorization. |
| **WHO** | World Health Organization | Owns and publishes the base **ICD** classification that ICD-10-CM modifies. |
| **ResDAC** | Research Data Assistance Center | CMS-funded resource that helps researchers use CMS data. Kept as background only: its NPPES downloadable-file guide no longer applies here, since the bulk file is not used. |
| **FOIA** | Freedom of Information Act | Why NPPES provider data *would* be safe to vendor despite naming real practitioners — it is FOIA-disclosable. Moot in practice: NPPES is queried live and never vendored, so no practitioner name reaches this repo. The distinction matters for `scripts/scan_sensitive.py`, whose `pii:npi` count is therefore expected to stay at zero permanently. |
| **AHIP** | America's Health Insurance Plans | The health-insurer trade association. Runs the annual training and certification that agents and brokers must complete to sell Marketplace plans; its training-programme role mailbox appears in the HealthCare.gov agent/broker corpus and is allowlisted in `scripts/sensitive_baseline.toml`. |
| **NAHU** | National Association of Health Underwriters | The agent/broker professional association named alongside AHIP in the same HealthCare.gov content, with the same allowlisted role mailbox. (Now trading as NABIP; the corpus predates the rename, so `nahu.org` is what the data actually contains.) |

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
| **PDP** | Prescription Drug Plan | A standalone Part D plan, sold on its own rather than bundled into a Part C plan. `CONTRACT_ID` begins with `S`. A correctness trap in `part_d_spuf`: PDPs are sold by **PDP region**, not by county, so every PDP row leaves `STATE` and `COUNTY_CODE` blank — filtering that source by state silently drops all of them. |
| **SNP** | Special Needs Plan | A Medicare Advantage plan restricted to a defined population, with benefits tailored to it. Three kinds: **D-SNP** (dual-eligible for Medicare *and* Medicaid), **C-SNP** (a qualifying chronic condition), **I-SNP** (institutionalized). The `SNP` column in `part_d_spuf`'s plan-information file, and visible in plan names like `Humana Gold Plus SNP-DE (HMO D-SNP)`. Not to be confused with **SNF**. |
| **MMP** | Medicare-Medicaid Plan | A demonstration plan integrating both programs' benefits for dual-eligibles. Notable only as an inclusion rule: `part_d_spuf` excludes demonstration plans generally **but keeps MMPs**. |
| **MA region / PDP region** | — | The two geographic schemes Medicare plans are sold under. MA plans are sold by county (`COUNTY_CODE`); PDPs are sold across multi-state PDP regions (`PDP_REGION_CODE`). `part_d_spuf`'s geographic-locator file is the lookup between them, and the reason a "plans near me" question needs a different join per plan type. |
| **Extra Help / LIS** | Low-Income Subsidy | Federal help paying Part D premiums, deductibles, and copays. |
| **QMB** | Qualified Medicare Beneficiary | A Medicare Savings Program that pays Medicare premiums and cost sharing and bars providers from balance billing the beneficiary. |
| **PACE** | Program of All-Inclusive Care for the Elderly | Combined Medicare/Medicaid program delivering coordinated care to people who would otherwise need nursing-home care. |
| **Medicaid** | — | Joint federal-state coverage for people with low income. State-administered, so rules vary — a routing hazard, since a national corpus cannot answer a state-specific Medicaid question. |
| **CHIP** | Children's Health Insurance Program | Coverage for children in households earning too much for Medicaid. |
| **ACA** | Affordable Care Act | The 2010 law (formally the Patient Protection and Affordable Care Act) that created the Marketplace, essential health benefits, and premium tax credits. The ACA side of the corpus is HealthCare.gov; see [`health_care_data.md`](health_care_data.md). |
| **Marketplace** | Health Insurance Marketplace | The ACA individual-plan exchange — HealthCare.gov federally, or a state-run equivalent. Also called the **Exchange**. |
| **Exchange** | — | Synonym for Marketplace, and the term used in dataset names: the **Exchange PUFs**. |
| **QHP** | Qualified Health Plan | A plan certified to be sold on the Marketplace, meeting essential-health-benefit and cost-sharing rules. The unit of the Exchange PUFs. |
| **SADP** | Stand-alone Dental Plan | A Marketplace dental plan sold separately from a QHP's medical coverage. The Exchange PUFs cover both QHPs and SADPs in the same tables; `Plan_Attributes_PUF`'s `DentalOnlyPlan` column is what distinguishes them. |
| **SHOP** | Small Business Health Options Program | The Marketplace's small-employer track. |
| **FFM** | Federally-facilitated Marketplace | The Marketplace HealthCare.gov runs directly, as opposed to a state running its own. The Exchange PUFs cover **only** FFM plans — they explicitly exclude data from State-Based Exchanges that don't rely on the federal eligibility/enrollment platform, so `exchange_puf` cannot answer coverage questions for the ~19 states running their own full exchange (e.g. California's Covered California, New York State of Health). |
| **SBE** | State-Based Exchange | A state-run Marketplace, as an alternative to the FFM. Some SBEs still use the federal eligibility/enrollment platform (and so *are* in the Exchange PUFs); the fully independent ones are not — see **FFM** above. A routing hazard worth knowing before trusting `exchange_puf` for a given state. |
| **No Surprises Act** | — | 2022 law limiting balance billing for out-of-network emergency care and out-of-network providers at in-network facilities, and creating a patient-provider dispute process. A Phase 5 growth-surface capability (appeals guidance), not a Phase 0–4 concern. |
| **COBRA** | Consolidated Omnibus Budget Reconciliation Act | The right to keep employer coverage temporarily after leaving a job, at full unsubsidized cost. |
| **TRICARE** | — | Health coverage for uniformed-service members, retirees, and families. |

---

## Datasets, files, and APIs

| Term | Expansion | What it means in this repo |
|---|---|---|
| **MCD** | Medicare Coverage Database | CMS's database of coverage policy. **Partly clean**: NCDs are cleared to vendor, LCDs and Billing/Coding Articles are not. The canonical example of "vendor only the cleared subset". Vendored as the `medicare_ncd` source; see [`medicare_ncd_data.md`](medicare_ncd_data.md). |
| **NCD** | National Coverage Determination | A nationwide CMS decision on whether Medicare covers a service. Binds every MAC. Contains **no procedure-code tables**, so it is **cleared for the public repo** — note the precision: NCD prose does occasionally *mention* a CPT or HCPCS code in a revision history, which is a narrative reference in a government work, not a redistributed code set. A stray `CPT` match in this corpus is expected, not a violation. |
| **LCD** | Local Coverage Determination | A MAC's coverage decision for its own jurisdiction, issued where no NCD applies. Embeds AMA CPT/HCPCS and ADA CDT **code tables** — **blocked from the public repo**. |
| **Billing/Coding Article** | — | MCD companion documents listing the codes that bill against a policy. Blocked for the same reason as LCDs. |
| **Coverage API** | MCD Coverage API | CMS's keyless JSON API at `api.coverage.cms.gov/v1/`, the route this repo uses for NCDs instead of the MCD bulk ZIPs. Load-bearing property: it gates endpoints on exactly our licensing line — National coverage answers without auth, LCD and Article endpoints return `401`. |
| **license agreement token** | — | The AMA/ADA/AHA bearer token the Coverage API issues from `/v1/metadata/license-agreement/`, valid one hour, that unlocks LCD and Article data. **Never request one.** The data behind it is blocked from this repo, so not holding a token is a safety property, not a limitation. |
| **NCD section number** | — | The number an NCD is cited by (`30.3` = Acupuncture), grouped into chapters by body system. The corpus's `section_number` field and the basis of its record `id` and raw filenames — not the API's internal `document_id`. |
| **Publication 100-3** | Medicare National Coverage Determinations Manual | The CMS manual NCDs live in; every NCD record's `publication_number`. Its sibling **100-04** (Claims Processing Manual) is where the billing instructions — and therefore the codes — live, which is a compact way to remember why NCDs are clean. |
| **transmittal** | — | A numbered CMS change instruction that puts a policy revision into effect. `transmittal_number` / `transmittal_url` on each NCD record are the provenance link from a determination back to the document that changed it. Often paired with a **CR (Change Request)** number in revision histories. |
| **NCA** | National Coverage Analysis | The evidence review CMS runs before opening or revising an NCD, published with a decision memo. License-free on the Coverage API but **not** vendored: it is the process behind a decision, not the coverage rule. Same for **CAL** (Coding Analysis for Labs), **MEDCAC** (Medicare Evidence Development & Coverage Advisory Committee) meeting materials, and **TA** (Technology Assessment). |
| **PUF** | Public Use File | CMS's bulk CSV/ZIP data dumps. Public-domain, safe to vendor, and large enough to need DuckDB or SQLite rather than a spreadsheet. |
| **Benefits and Cost Sharing PUF** | — | Per-plan, per-benefit cost-sharing detail (copay/coinsurance/is-it-covered) for Marketplace QHPs and SADPs — 1.46M rows for plan year 2026, CMS's own example of a file too large for Excel. Vendored as part of the `exchange_puf` source; see [`exchange_puf_data.md`](exchange_puf_data.md). |
| **Plan Attributes PUF** | — | Plan-variant-level data: max out-of-pocket, deductibles, HSA eligibility, metal level, formulary ID, `ServiceAreaId`. 151 columns for plan year 2026 — the widest table this repo vendors. Part of `exchange_puf`. |
| **Service Area PUF** | — | Maps each plan's `ServiceAreaId` to the counties/ZIP codes it's actually sold in. Not named in `docs/plan.md`'s original two-PUF description, but included in `exchange_puf` anyway: without it, `ServiceAreaId` alone can't answer "which plans are available in ZIP 30076" — the plan's own canonical structured-lookup example. |
| **Rate PUF** | — | Per-plan premium rates by rating area and age. Not currently vendored — Phase 5 territory. |
| **NPPES** | National Plan and Provider Enumeration System | CMS's registry of every US healthcare provider identifier. Published both as a 4 GB+ bulk file and as a live per-provider API; **this repo uses the API only and vendors nothing** — provider lookup is inherently one record at a time, so a mirror would buy staleness and storage for nothing. Consequence worth remembering: no provider-level data ever enters `data/`. |
| **NPI** | National Provider Identifier | The 10-digit ID NPPES assigns to a provider. The lookup key for *"what is this NPI's specialty"* — a `structured_api` question, never a RAG one. Self-validating: the tenth digit is a Luhn check over the prefix `80840` plus the first nine, which is how `scripts/scan_sensitive.py` tells a real NPI from any other ten-digit run. |
| **HIOS** | Health Insurance Oversight System | CMS's system for identifying issuers and plans on the Marketplace. A **HIOS Issuer ID** identifies a company (`IssuerId` in the Exchange PUFs); a **HIOS Product ID** identifies a product line. The base for both plan identifiers below. |
| **Standard Component ID** | — | The 14-character HIOS plan identifier (5-digit issuer ID + 2-letter state + a 7-digit product/plan number, e.g. `21989AK0030001`). `StandardComponentId` in both Exchange PUF tables — one row per underlying plan design, before splitting into its CSR variants (see `CSRVariationType` in **Benefit design and cost sharing**). |
| **Plan ID** | — | The Standard Component ID with a 2-digit **CSR-variant suffix** appended (`21989AK0030001-01`), distinguishing (for example) a plan's standard design from its 73%/87%/94% AV silver cost-sharing-reduction variants. `PlanId` in the Benefits and Cost Sharing PUF — the join key back to `StandardComponentId` in Plan Attributes. |
| **Marketplace API** | — | CMS's API at `marketplace.api.healthcare.gov/api/v1/` for plan search, drug-coverage checks, and cost estimates. Drives Window Shop and Plan Compare on HealthCare.gov. **Requires an API key** from the CMS developer portal; rate-limited. |
| **Finder API** | — | The companion CMS API for private health plans sold *outside* the Marketplace. Separately keyed; keys are rate-limited to 1000 requests/minute and expire every 60 days. |
| **Content API** | HealthCare.gov Content API | The keyless, CORS-enabled JSON feed behind HealthCare.gov's consumer-education content — append `.json` to any post URL. **Not the Marketplace API**: it serves articles and glossary entries, not plans or prices. |
| **Window Shop / Plan Compare** | — | The HealthCare.gov shopping flows the Marketplace API powers. Useful as a model for what a structured plan-comparison answer should contain. |
| **Formulary file** | — | Quarterly Part D file listing each covered drug by **NDC**, its cost-share tier, and its utilization-management flags (prior authorization, step therapy, quantity limits). Vendored as the `basic_drugs_formulary` table of the **SPUF**. Keyed by `FORMULARY_ID`, *not* by plan — one formulary backs many plans, so answering "does my plan cover X" always joins through plan-information first. |
| **SPUF** | (quarterly) Prescription Drug Plan Formulary, Pharmacy Network, and Pricing PUF | CMS's quarterly Part D bulk file, vendored as the `part_d_spuf` source; see [`part_d_spuf_data.md`](part_d_spuf_data.md). Public-domain and free of proprietary code tables (drugs are identified by **NDC**/**RxCUI** only), so fully cleared for this repo. Published as a 2.49 GB container of ten nested per-file zips, tagged **PPUF** plus the quarter (`PPUF_2026Q2`) inside; the monthly variant drops the pricing file. |
| **Medicare Plan Finder (MPF)** | — | The public plan-shopping tool on medicare.gov. Not scraped — it matters because the **SPUF** is *built from* it, which is why that file's data can lag what the tool currently shows. |
| **Contract ID** | — | The Medicare identifier for a plan sponsor's contract with CMS: a letter plus four digits, where `H` = local Medicare Advantage, `R` = regional MA, `S` = standalone **PDP**. `CONTRACT_ID` in `part_d_spuf`, and the anchor for its committed sample. Carries a scanner consequence: `H1671` is shape-identical to an **HCPCS** Level II code (and `H`/`R`/`S` are all real HCPCS letters), so these are allowlisted by path in `scripts/sensitive_baseline.toml` rather than by loosening the detector. |
| **segment** | — | A subdivision of a plan offered in different parts of its service area with different benefits or premiums. `SEGMENT_ID` in `part_d_spuf`, always `000` for `R` and `S` contracts. A plan is uniquely identified only by `CONTRACT_ID` + `PLAN_ID` + `SEGMENT_ID` — none of the three alone. |
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
| **effective date / implementation date** | — | **The NCD analogue of the plan-year rule, and the exception to it.** An NCD applies from its effective date until superseded, cutting across plan years — so `medicare_ncd` records carry `effective_date`, not `plan_year`, and pinning a year against them is the wrong comparison. The implementation date is the later deadline by which MACs had to have the change in place. Caveat that bites: 75 of the 345 NCDs are *longstanding* determinations with no posted effective date, so `effective_date` is `null` there — meaning "in force, date unknown", never "not in force". |
| **benefit category** | — | The statutory bucket a service must fall into before Medicare can cover it at all (Physicians' Services, Inpatient Hospital Services, DME, …). Distinct from *whether it is medically reasonable and necessary* — a service can fail either test. Every `medicare_ncd` record carries one. |
| **premium** | — | The recurring amount paid to keep coverage, whether or not care is used. |
| **deductible** | — | What the member pays before the plan starts paying. The repo's canonical demo question — *"What is a deductible?"* — and the standing example of a `reference`-lane question. |
| **copayment (copay)** | — | A fixed dollar amount per service. |
| **coinsurance** | — | A percentage of the allowed amount, rather than a fixed dollar amount. |
| **cost sharing** | — | The umbrella term for deductible + copay + coinsurance — everything the member pays that is not premium. |
| **out-of-pocket maximum (MOOP)** | Maximum Out-Of-Pocket | The annual ceiling on cost sharing; past it the plan pays 100% of covered in-network care. A **Plan Attributes PUF** column — or rather 36 of them: MEHB/DEHB/TEHB (medical/dental/combined) × in-network tier 1/tier 2/out-of-network/combined × individual/family-per-person/family-per-group. Which column is "the" MOOP for a given plan depends on that plan's benefit design (most carry it under `TEHB*`), which is why picking one is deferred to Phase 5 rather than guessed at ingestion — see [`exchange_puf_data.md`](exchange_puf_data.md). |
| **EHB** | Essential Health Benefits | The 10 benefit categories every ACA-compliant QHP must cover (ambulatory care, emergency, hospitalization, maternity/newborn, mental health/substance use, prescription drugs, rehabilitative services, lab, preventive/wellness, pediatric services). `IsEHB` on each Benefits & Cost Sharing PUF row marks whether that specific benefit line counts toward EHB — the field that separates required coverage from a plan's extra, non-EHB benefits. |
| **metal level** | — | The four ACA cost-sharing tiers — Bronze, Silver, Gold, Platinum (plus Catastrophic) — ranked by actuarial value, not by benefit richness: a higher metal pays a larger share of costs via lower cost sharing, not necessarily a longer benefit list. `MetalLevel` in the Plan Attributes PUF; also appears as **Expanded Bronze**, **High**, and **Low** in the raw data — CSR-driven or issuer-specific sub-tiers, not additional official metal levels. |
| **actuarial value (AV)** | — | The average share of covered costs a plan pays for a standard population, expressed as a percentage — the quantitative basis for the metal levels (Bronze ≈60%, Silver ≈70%, Gold ≈80%, Platinum ≈90%). `IssuerActuarialValue` and `AVCalculatorOutputNumber` in the Plan Attributes PUF; also appears baked into a **CSR variant's** name ("87% AV Level Silver Plan"). |
| **CSR** | Cost-Sharing Reduction | A subsidy, available only on Silver plans to income-eligible enrollees, that raises the plan's effective actuarial value (73/87/94% AV, versus the standard ~70%) by lowering deductibles and copays — distinct from the premium tax credit. Surfaces in the Exchange PUFs as `CSRVariationType` (e.g. "73% AV Level Silver Plan", "Zero Cost Sharing Plan Variation") and the **Plan ID**'s 2-digit variant suffix; each CSR variant of a plan is a separate row/`PlanId`, sharing one `StandardComponentId`. |
| **HSA** | Health Savings Account | A tax-advantaged account usable only with a high-deductible health plan. HSA eligibility is a Plan Attributes PUF column. |
| **formulary** | — | A plan's list of covered drugs and their tiers. Coverage is plan- *and* year-specific, which makes it a `structured_api` question, not a `reference` one. |
| **tier** | — | The formulary bucket that sets a drug's cost share — generic, preferred brand, non-preferred brand, specialty. |
| **prior authorization** | — | Plan approval required before a drug or service is covered. A formulary-file flag. |
| **step therapy** | — | A requirement to try a cheaper drug first before a costlier one is covered. A formulary-file flag. |
| **quantity limit** | — | A cap on how much of a drug is covered per period. A formulary-file flag (`QUANTITY_LIMIT_YN`, with `_AMOUNT` and `_DAYS`). |
| **utilization management (UM)** | — | The collective name for the conditions a plan attaches to a covered drug rather than refusing it outright — **prior authorization**, **step therapy**, and **quantity limits**. The distinction that matters for answering: a UM-flagged drug *is* covered, so "is it covered?" and "can I get it today?" have different answers, and a correct response must carry the flags, not just the tier. |
| **excluded drug** | — | A drug category Part D is statutorily barred from covering (weight-loss and fertility drugs, OTC products, and others). An **enhanced alternative** plan may cover some anyway as a supplemental benefit — which is what `part_d_spuf`'s excluded-drugs file lists. So "excluded" names the statutory default, not a guarantee the plan won't pay. |
| **enhanced alternative** | — | A Part D plan offering benefits richer than the statutory standard — a lower deductible, extra tiers, or coverage of **excluded drugs**. Only these plans have rows in the excluded-drugs file. |
| **indication-based coverage** | — | A plan covering a drug only for some of its FDA-approved uses. `part_d_spuf`'s indication-based file pairs an `RXCUI` with a `DISEASE`. Rare and worth knowing as a routing hazard: nationally only **three contracts** use it, so "is drug X covered" can be answered correctly for the drug and still be wrong for the patient's condition. |
| **selected drug** | — | A drug subject to Medicare drug-price negotiation under the Inflation Reduction Act. `SELECTED_DRUG_YN` in the formulary file. |
| **coverage gap / donut hole** | — | The Part D benefit phase between initial coverage and catastrophic coverage where the enrollee historically paid a larger share. Reshaped by the Inflation Reduction Act, which also sunset the Coverage Gap Discount Program from 1 January 2025 — so any corpus text describing the classic donut hole is a plan-year-sensitive `web`-lane hazard, not a stable reference fact. |
| **coverage level** | — | Which benefit phase a cost-sharing row applies to (initial coverage vs. coverage gap). `COVERAGE_LEVEL` in the beneficiary-cost file — the reason one plan+tier has several rows, and why picking the wrong one silently reports the wrong copay. |
| **days supply** | — | The dispensing quantity a cost-sharing row is priced for — typically 30, 60, or 90 days. `DAYS_SUPPLY` in the beneficiary-cost and pricing files. Part of the key: a copay is meaningless without it. |
| **preferred pharmacy** | — | A network pharmacy where the plan charges lower cost sharing than at other in-network pharmacies. `part_d_spuf`'s beneficiary-cost file carries four parallel cost columns — preferred, non-preferred, mail preferred, mail non-preferred — so "the copay" for a drug is not a single number. |
| **dispensing fee** | — | The pharmacy's per-prescription fee, separate from the drug's ingredient cost. Carried in the SPUF's pharmacy-network file, which this repo does not fetch. |
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
| **SAD** | Self-Administered Drug | A drug a patient normally takes without clinical help, therefore excluded from Part B in the outpatient setting and pushed to Part D. MACs publish SAD exclusion lists as **Articles** — so despite being a coverage question, SAD lists sit on the blocked side of the licensing line and are not in this repo's corpus. |
| **laboratory test** | — | A determination covering a lab service. Flagged as `is_lab` on `medicare_ncd` records (23 of 345) — CMS tracks labs separately because they run through their own analysis track (**CAL**) and their own coding rules. |

---

## Privacy and compliance

| Term | Expansion | What it means in this repo |
|---|---|---|
| **PII** | Personally Identifiable Information | Data identifying a specific person. **Never committed**, in any form, under any directory. |
| **PHI** | Protected Health Information | Individually identifiable health information — health status, care received, or payment for care, tied to a person. HIPAA-regulated and the most sensitive category this project could touch. **Never committed.** The project handles population-level and policy-level data only; it never ingests a real person's records. |
| **HIPAA** | Health Insurance Portability and Accountability Act | The law governing PHI. Not a compliance obligation we take on — the design avoids PHI entirely — but the reason the PHI line is absolute. |
| **BAA** | Business Associate Agreement | The HIPAA contract a vendor signs before it may process PHI on a covered entity's behalf. This project never handles PHI, so no BAA is required — but "would this route health-domain data through a third party that has not signed one?" is the standing test applied to infrastructure choices, and it is why the vector store is local and embedded rather than a managed cloud service (see [`lancedb.md`](lancedb.md)). |
| **SSN** | Social Security Number | The one PII shape that is **blocking** rather than advisory in `scripts/scan_sensitive.py` (marker `pii:ssn`) — there is no benign reason for anything matching `\d{3}-\d{2}-\d{4}` to appear in this corpus, so it needs no judgement call. (Write the *pattern*, never an example — a literal nine-digit specimen in prose trips the scanner, exactly as it should.) Contrast e-mail, phone, and NPI, which this repo legitimately carries and which are therefore advisory. |
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
- [`medicare_ncd_data.md`](medicare_ncd_data.md) — the Medicare Coverage Database NCD source
  guide, including why the Coverage API's auth boundary *is* the licensing boundary.
- [`exchange_puf_data.md`](exchange_puf_data.md) — the Exchange PUF source guide, including
  why its processed layer is a lossless mirror rather than an app-ready model.
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
