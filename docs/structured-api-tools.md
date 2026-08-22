# Live structured-API tools — Phase 3 design

The design document for [plan.md](plan.md)'s [Phase 3](plan.md#phase-3--add-structured-api-tools),
the way [relational-tool.md](relational-tool.md) is Phase 1-c's and
[web_search_tool.md](web_search_tool.md) is Phase 2's. `plan.md` records *when* this phase happens
and what "done" means; everything here is *how*.

Read [relational-tool.md](relational-tool.md) first. Phase 3 **extends** the structured lane rather
than opening it: `list_tables` / `describe_table` / `query_structured` already answer deterministic
questions over vendored rows, and the tools below sit beside them in the same lane, under the same
`source_type`. What is new is that some rows now come from someone else's server — with the keys,
expiries, rate limits, and outages that implies.

**Status: not started.** [progress.md](progress.md) is the source of truth for what is built; this
line exists only so a reader of this file is not misled by its level of detail.

---

## 1. What this document holds

Right now, **access**: which of the three sources need a credential, how to obtain one, the
properties of those credentials that constrain the design rather than merely the setup, and the one
key that was available and deliberately declined.

Also the **Marketplace contract** (§7) and the **fixture strategy** (§8), both established by
exercising the live API rather than by reading about it. Still to come as the phase is built: the
tool surface and its descriptions, the typed models themselves, the mirror-vs-live reconciliation
rule, caching, provenance for a live-API citation, and the eval slice. Sections are numbered so
those can be added without renumbering what is here.

**Verified 2026-08-22** — the access facts in §3–§5 against the live CMS and FDA pages, and every
claim in §7 against the running API. All of it is someone else's operational policy or someone
else's deployment; re-check before blaming the code.

## 2. The three sources at a glance

| Source | Credential | Status | Blocks the build? |
|---|---|---|---|
| **Marketplace API** | **Required** | Web form → key emailed; turnaround not published (§3) | **No — see §3a** |
| **openFDA** | Optional — **deliberately not requested** | Keyless limits are sufficient; §4 records why and what would reverse it | No |
| **NPPES NPI Registry** | **None exists** | Nothing to request, ever | No |

**Exactly one credential enters this repo for Phase 3** — `CMS_MARKETPLACE_API_KEY`, live in
`.env.example`. The other two lanes are keyless: one because the API has no such concept, one by the
decision in §4.

[progress.md](progress.md) records when the Marketplace key was requested and where that stands;
this document does not carry status.

**No tool is blocked on a credential.** NPPES needs none, openFDA runs keyless by decision, and the
Marketplace lane can be built and fixtured against CMS's own published demo key (§3a) before a
personal key arrives.

## 3. Marketplace API — the one real request

**Form:** <https://developer.cms.gov/marketplace-api/key-request.html>

What it asks for:

| Field | Notes |
|---|---|
| Name | |
| Email | where the key is delivered, and where every 60-day replacement arrives |
| Company/Organization | optional |
| **Phone** | **must be able to receive SMS** — not a landline, not a VOIP number that cannot take texts |
| Application URL | optional; the GitHub repo URL is fine |
| "How will you use the API?" | the only free-text field |

CMS states that keys "can be issued to anyone who requests access and is granted a key", so this is
a request rather than a review — but **no turnaround time is published**, which is the whole reason
it is the first thing to do in the phase.

A description that matches what this repo actually does:

> Personal open-source project (health-insurance question-answering agent) that routes plan, drug,
> and coverage questions to authoritative sources. Uses `/drugs/autocomplete`, `/drugs/covered`, and
> `/households/eligibility/estimates` for low-volume per-question lookups — a handful of calls per
> user question, responses cached. Not extracting or mirroring the dataset.

Base URL `https://marketplace.api.healthcare.gov/api/v1/`; the key is passed as `?apikey={key}`.

### Three properties that constrain the design, not just the setup

1. **Keys expire every 60 days**, and a replacement is emailed automatically. `Secrets` is
   `frozen=True` and `get_secrets()` is `@lru_cache(maxsize=1)`, so a rotation means editing `.env`
   *and* restarting the process — the running server will not pick up a new key on its own. The
   failure mode is a 401 that looks like a code bug and is really a calendar. Whatever this phase
   does about it, the one thing it must not do is let that 401 reach the user as a confident answer;
   [web_search_tool.md](web_search_tool.md) §8's "an outage must not look like an answer" applies
   here unchanged, and an expired key is just an outage with a due date.
2. **"Not designed to be scraped or for the whole data set to be extracted"** — CMS's own terms.
   This is the same rule CLAUDE.md already enforces from the other direction (no bulk downloader for
   a live source), and it settles the fixture question before it is asked: test fixtures are a few
   recorded responses, never a harvested slice.
3. **Rate limits live in response headers, not in the documentation** — so they were read off a
   live call rather than guessed. Measured 2026-08-22:

   | Window | Limit | Header |
   |---|---|---|
   | Per second | **200** | `x-ratelimit-limit-second` / `-remaining-second` |
   | Per minute | **1000** | `x-ratelimit-limit-minute` / `-remaining-minute` |

   **No daily-limit header is returned**, so either there is no daily cap or it is not advertised —
   do not assume the former. Both windows are generous relative to a per-question handful of calls;
   what they do *not* survive is an unthrottled fixture-recording loop or a parallel eval sweep. The
   budget belongs where Phase 2 put Tavily's: enforced in code, not in a comment.

### 3a. Developing before the key arrives, on CMS's published demo key

CMS embeds a **shared, rate-limited demo key** in the Quickstart section of its
[spec page](https://developer.cms.gov/marketplace-api/api-spec), and repeats it as the `x-example`
on the spec's `securityDefinitions`. **The value is deliberately not written down here or anywhere
else in this repo** — it is two clicks away at that URL, and a 32-hex literal in a tracked file is
exactly what `make scan` exists to block. Copy it from CMS into `.env`; `.gitignore` excludes that
file, and the scanner enumerates candidates with `git ls-files --others --exclude-standard`, so an
ignored file is never a candidate. `.env.example` carries the same instruction, by location and not
by value.

**Everything in §7 and §8 was established with that key**, which is the evidence it suffices: all
four endpoints Phase 3 needs answer on it, for the current market year.

**Its boundary, which is not a nicety.** The key is *shared* — every reader of CMS's quickstart uses
the same one, against the same 1000/minute. That makes it fine for hand-run calls and for recording
a handful of fixtures, and wrong for anything sustained: an eval sweep run on it is both a burden on
a public resource and a source of 429s that will look like a CMS outage. **Nothing in the code
distinguishes it from a real key**, so the distinction has to be enforced somewhere deliberate. The
cheap version is three lines — the eval runner refuses to start when the configured key equals the
published demo value — and it converts a comment into a rule. Deferred to the build, recorded here
so it is not forgotten.

### The Finder API is deliberately not requested

CMS's companion [Finder API](https://developer.cms.gov/finder-api/key-request.html) covers private
plans sold *outside* the Marketplace (separate key, separate 60-day expiry, 1000 req/min).
[plan.md](plan.md) mentions it under *Live Web / API tools*, but Phase 3's scope names only
Marketplace, openFDA, and NPPES. **Off-exchange plans are not in scope**, so the key is not
requested — recorded here so the omission reads as a decision rather than an oversight.

## 4. openFDA — run keyless, by decision

A free key is available (<https://open.fda.gov/apis/authentication/>, signing up through
<https://api.data.gov/signup/>, instant and emailed). **This project does not request one.** The
decision was taken 2026-08-22, before the phase was built, and the rest of this section is the
reasoning and the tripwire that would reverse it.

| | Per minute | Per day |
|---|---|---|
| **No key** — what this repo uses | 240 per IP | **1,000 per IP** |
| With key | 240 per key | 120,000 per key |

**The judgement: 1,000 requests a day is not a ceiling this project reaches.** The openFDA questions
Phase 3 asks are per-drug lookups — *has drug X been recalled*, *what are its indications* — a
handful per user question, against a gold set of tens of questions, not thousands. Nothing in this
repo does whole-corpus scanning against openFDA; [plan.md](plan.md) rules the bulk files out for the
same reason. A credential that buys 120× headroom over a limit that is not approached buys nothing,
and costs a secret to rotate, a `Secrets` field to validate, and one more way for `cp .env.example
.env` to produce a live 401.

**The consequence to build against, since the headroom is not there:** the per-minute limit is
identical either way, but **the daily limit is per *IP*, not per process** — it is shared with
anything else on the same address, and it is not reset by restarting the run. That makes the
response cache in Phase 3's checklist load-bearing for openFDA specifically, rather than a
nice-to-have: a repeated eval sweep must not re-fetch what it fetched an hour ago.

**What would reverse this** — record it here rather than rediscovering it from a 429: a sustained
eval or smoke loop that issues openFDA calls in the hundreds per run, or any use from a shared or
CI IP address where the 1,000 is not this project's alone. Either one, and the key is two minutes
away; §6's wiring notes what would then have to be added.

**One detail that survives the decision:** openFDA's key parameter is `api_key` where the
Marketplace API's is `apikey`. Two adjacent tools in the same lane spell it differently — a small
argument for each typed wrapper owning its own auth detail rather than a shared helper guessing from
the hostname.

## 5. NPPES — nothing to request

`https://npiregistry.cms.hhs.gov/api/?version=2.1&...` — no key, no registration, no terms to
accept. The `version` parameter is **required**, not optional.

Rate limits are real but undocumented, and records-per-request is capped. Practically that means the
ceiling is discovered by hitting it, which is an argument for the response cache landing before the
evals that would hammer it.

## 6. Where the keys live in this repo

Per §2 and §4, this phase adds **exactly one** credential — the Marketplace key. Unchanged rules,
restated because it is the first one to arrive since Tavily:

- **It is optional** — `SecretStr | None`, following `tavily_api_key` in
  [settings.py](../src/health_coverage_navigator/settings.py) and not `openai_api_key`. The
  asymmetry is the same one Phase 2 recorded: there is no agent without a model, but there *is* an
  agent without live APIs, and a clone that wants only the reference and relational lanes must still
  boot. Suggested name: `cms_marketplace_api_key`.
- **It needs the `_blank_is_none` treatment.** `cp .env.example .env` with no edit yields `""`,
  which is a valid `str` that reads as "configured", boots cleanly, and 401s on the first question.
  Extend the existing validator's field list rather than writing a second copy of it.
- **`.env.example` already carries it**, uncommented and blank, with the 60-day expiry and §3a's
  demo-key instruction written as comments — the value by location, never inline. **No
  `OPENFDA_API_KEY` placeholder goes there** — §4 decided against the key, and a commented
  placeholder for a credential nobody holds is an invitation to request one without re-reading the
  reasoning. If §4's tripwire fires, the placeholder and a second `SecretStr | None` field are added
  together, in the change that reverses the decision.
- **Nothing goes in `config.yaml`.** It is committed, and `make scan` treats a credential-shaped
  assignment there as a blocking error. What *may* go there is the non-secret half — whether a
  missing key is fatal, per-source budgets, cache TTLs — the way `agent.web_tools` decides Tavily's
  case today. Which flags Phase 3 actually needs is an open question for the build, not settled here.

## 7. The Marketplace contract, and how far the spec can be trusted

CMS publishes a complete **OpenAPI 2.0 spec** — 38 endpoints, 91 model definitions, request and
response schemas, enums — embedded as inline YAML in the
[spec page](https://developer.cms.gov/marketplace-api/api-spec). **It is readable without a key**,
which is why the typed wrappers were never actually blocked on one. It is not offered as a
downloadable file; it is extracted from the page's `swaggerUIOptions.spec` string.

### 7a. The rule: authoritative for requests, not for responses

This is the load-bearing finding of the phase's access work, and it was established by comparing
declared schemas against live calls rather than by trusting either.

**Requests: correct.** Every parameter, required flag, and enum the spec declares held on every call
made — path shapes, `year` semantics, `Household` / `Place` / `Person` field names, the lot.

**Responses: wrong on four of the five endpoints checked**, and wrong in a way that would break a
generated model on the *first* real call, not on some edge case:

| Endpoint | Spec envelope | Live envelope | |
|---|---|---|---|
| `POST /plans/search` | `plans, total, rate_area, facet_groups, ranges` | identical | ✅ |
| `GET /drugs/autocomplete` | `{drugs: [...]}` | **bare array** | ❌ |
| `GET /providers/autocomplete` | `{providers: [...]}` | **bare array** | ❌ |
| `GET /drugs/covered` | `{"Provider & Drug Coverage": [...]}` | `{coverage: [...]}` | ❌ |
| `GET /providers/covered` | `{"Provider & Drug Coverage": [...]}` | `{coverage: [...]}` | ❌ |

The pattern is not random: **the POST endpoint is accurate and every GET is not.** And
`"Provider & Drug Coverage"` is the Swagger **tag** name leaking into the schema — an authoring bug,
which is why it appears identically on two otherwise unrelated endpoints. Neither is a field that
was renamed and can be mapped; the spec simply describes a response the server does not send.

**Field level is much better than envelope level**, which is the half that makes the spec still
worth having:

- `ProviderCoverage` — exact match, 5/5 fields.
- `Coverage` enum — exact match: `Covered`, `NotCovered`, `DataNotProvided`, `GenericCovered`.
- `Plan` — 36 fields match. 3 spec-only (`certification`, `network_adequacy`, `sbcs`), 3 live-only
  (`tiered_deductibles`, `tiered_moops`, `waiting_period_duration`). Stale in both directions.
- `Provider` — the spec's `type` is the live `provider_type`, a real rename; live also adds `sex`,
  `imported_on`, `group_id`.
- `Drug` — the spec carries an `id` the live response does not, and marks **nothing** required, so
  optionality cannot be inherited from it at all.

**So: request models are derived from the spec; response models are derived from recorded
fixtures.** The spec remains the map of what exists — which endpoints, which parameters, which enum
values — and is never the source for what comes back. §8 is the other half of that rule.

### 7b. The four endpoints Phase 3 needs

Verified live. `apikey` is a **query** parameter on every one (`securityDefinitions: {type: apiKey,
in: query}`), and `year` defaults to the current market year where it is optional.

| Endpoint | Request | Live response |
|---|---|---|
| `GET /drugs/autocomplete` | `q` (≥3 chars) | bare array of `{rxcui, name, strength, route, full_name, rxterms_dose_form, rxnorm_dose_form}` |
| `GET /drugs/covered` | `drugs` (comma-joined RxCUIs), `planids`, `year` | `{coverage: [{rxcui, plan_id, coverage, generic_rxcui?}]}` |
| `GET /providers/covered` | `providerids` (NPIs), `planids`, `year` | `{coverage: [{npi, plan_id, coverage, addresses, accepting}]}` |
| `POST /plans/search` | `PlanSearchRequest`: `household{income, people[]}`, `place{countyfips, state, zipcode}`, `market`, `year` | `{plans[], total, rate_area, facet_groups[], ranges}` |

Two notes that matter more than they look:

- **`generic_rxcui` is conditional, not optional-and-usually-present.** It appears exactly when
  `coverage == "GenericCovered"` — the plan does not cover the branded drug but does cover its
  generic, and the field names which one. That is a *different answer to the user's question*, not a
  missing field, and a wrapper that models it as a plain optional will let the agent report "not
  covered" when the truthful answer is "not as branded; the generic is".
- **`/drugs/autocomplete` is a required first hop, not a convenience.** `/drugs/covered` takes
  RxCUIs, and no user types one. Every drug-coverage question is therefore at least two calls, which
  is worth knowing before a per-question call budget is chosen.

### 7c. Plan year is discoverable, and the default is wrong

`GET /market-years` returns `{"supported": [2016...2026], "current": 2026}` — so the plan year is
asked for, never hardcoded. This is the live counterpart to
[relational-tool.md](relational-tool.md) §7's plan-year handling, and it lets the two halves of the
structured lane agree on which year they are talking about instead of each assuming.

**The reason this is not a footnote:** CMS's own quickstart uses `year=2019`, and against 2019 data
both `/drugs/covered` and `/providers/covered` return `coverage: "DataNotProvided"` — the
quickstart's prose still claims "the API confirms that ibuprofen is covered", which no longer
reproduces. Against a **2026** plan the same call returns real answers, including a `GenericCovered`
with its `generic_rxcui` populated. **Fixtures recorded from the quickstart's year would be nearly
empty and would teach the wrappers the wrong optionality** — precisely the `generic_rxcui` trap
above. Record against the current market year.

## 8. Fixtures

CLAUDE.md's rule is fixtures over live calls, and §7a settles where they come from: **recorded from
the live API, not generated from the spec.** The spec's response schemas are wrong often enough that
a fixture built from them would assert the wrong shape and pass — the worst of both worlds, a test
that is green and false.

**Stamp each fixture with the `x-version` and `request-id` of the call that produced it.** The API
reports its own build (`x-version: r1.2635.0` at time of writing); a fixture that starts failing
after a CMS deploy then says *which* deploy, and drift becomes a diagnosis instead of a mystery.
This costs two header reads and is the only provenance available for a response that cannot be
re-fetched identically.

Fixture content is CMS public data and does not depend on which key fetched it, so recording with
the demo key of §3a is fine — with one exception, which is not.

### 8a. The provider endpoints return real people, and this repo has promised they will not

`/providers/autocomplete` and `/providers/search` return **real practitioners: real names, real
NPIs.** That collides directly with two commitments already written down:

- CLAUDE.md: *"no provider-level data is ever vendored here"*, stated as a consequence of never
  bulk-downloading NPPES.
- [glossary.md](glossary.md) under **FOIA**: `scripts/scan_sensitive.py`'s `pii:npi` count *"is
  expected to stay at zero permanently"*.

Both were written when the only provider source was NPPES, which is queried live and never stored.
The Marketplace API reopens the question from a direction neither anticipated — a provider fixture
is a *recorded response*, and recording it is vendoring. **A fixture from either endpoint would put
real clinician names and real NPIs into a public repo and break a baseline the scanner enforces.**

The drug and plan endpoints have no such problem; this is specifically about `/providers/*`.

**Recommended: synthesise provider fixtures rather than relax the invariant.** The invariant is
cheap to keep and expensive to re-establish once broken, and `pii:npi == 0` is a genuinely useful
tripwire precisely because it has no legitimate exceptions. The cost is real but small: an NPI
carries a Luhn check over the prefix `80840` plus its first nine digits, so a synthetic NPI must be
*constructed* to pass — and a fixture whose NPIs fail the check would be caught by the very
validation the wrappers exist to perform. **This is an open decision, not a settled one**; it is
recorded in [progress.md](progress.md) as such, and it must be resolved before the first
`/providers/*` fixture is written, not after.

## References

- [Marketplace API key request](https://developer.cms.gov/marketplace-api/key-request.html) ·
  [Marketplace API overview](https://developer.cms.gov/marketplace-api/) ·
  [API specifications](https://developer.cms.gov/marketplace-api/api-spec)
- [Finder API key request](https://developer.cms.gov/finder-api/key-request.html) (not requested — §3)
- [openFDA authentication and rate limits](https://open.fda.gov/apis/authentication/) ·
  [api.data.gov signup](https://api.data.gov/signup/) · [openFDA drug endpoints](https://open.fda.gov/apis/drug/)
- [NPPES NPI Registry API](https://npiregistry.cms.hhs.gov/api-page)
