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

**Status: built and measured (2026-08-22).** All six tools ship and all six live gold questions
pass (`run_2026-08-22_8`: 38/49, live 6/6, `lane_detail_correct` 1.000). `make check-all` is green
(454 Python tests, pyright clean, 20 frontend tests) and `make scan` is clean.
[progress.md](progress.md) is the source of truth for status; §18c records the two defect families
the build produced and §19 what it changed about this design.

---

## 1. What this document holds

Right now, **access**: which of the three sources need a credential, how to obtain one, the
properties of those credentials that constrain the design rather than merely the setup, and the one
key that was available and deliberately declined.

Then the **contracts** — Marketplace in §7, openFDA and NPPES in §10 — every one of them
established by exercising the live API rather than by reading about it. Then, from §9 on, the
**implementation plan**: the tool surface, the module layout, failure and budgets, provenance,
mirror-vs-live reconciliation, the eval slice, the tests, and the build order.

**§1–§8 are findings; §9–§18 were intent and are now built.** Where the build contradicted a
decision recorded here, the decision was changed here first and [progress.md](progress.md) records
why — that is the rule every design document in this repo follows, and this phase exercised it
often: §4a reverses §4, §11 reverses its own county design, §14a-bis and the note under §14a correct
§14a, §14b-bis corrects §14b, and §16a is struck through where it contradicted §11.

**Verified 2026-08-22** — the access facts in §3–§5 against the live CMS and FDA pages, and every
claim in §7 and §10 against the running APIs. All of it is someone else's operational policy or someone
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

### 4a. The tripwire fired, and the key was requested

**Reversed 2026-08-22.** The first condition above was met on paper before it was met in practice:
a 49-question sweep's *worst case* is 49 × 8 × 2 = **784 openFDA requests, 78% of a single day's
per-IP allowance**, because `drug_label` retries a brand miss against the generic name. Measured
traffic on a real sweep was nowhere near that — two `drug_label` calls, no `drug_recalls` — but a
ceiling that close to the limit is not one to leave to luck across repeated sweeps.

So `OPENFDA_API_KEY` is configured, and openFDA sends `api_key` (its spelling; the Marketplace's is
`apikey` — §4's last paragraph turned out to be load-bearing rather than trivia).

**The key is optional and the client degrades to keyless**, which is the difference from the
Marketplace: a missing openFDA key costs *headroom*, a missing CMS key removes *tools*. So there is
no `OpenFdaNotConfiguredError`, and keyless remains a supported, tested path.

**One test bug this exposed, worth keeping:** `OpenFdaClient.open()` with no `api_key` reads `.env`,
so the keyless test passed on a machine without a key and failed on one with it — `make check-all`
quietly meant different things on different machines. `api_key=""` now means *explicitly keyless*.

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

### 8a. Provider fixtures are synthesised — decided

`/providers/autocomplete` and `/providers/search` return **real practitioners: real names, real
NPIs**, and NPPES (§10a) adds street addresses and telephone numbers to the same problem. That
collides with two commitments already written down:

- CLAUDE.md: *"no provider-level data is ever vendored here"*, stated as a consequence of never
  bulk-downloading NPPES.
- [glossary.md](glossary.md) under **FOIA**: `scripts/scan_sensitive.py`'s `pii:npi` count *"is
  expected to stay at zero permanently"*.

Both were written when the only provider source was NPPES, which is queried live and never stored. A
recorded fixture is a stored response, so this phase reopens the question from a direction neither
anticipated.

**Decided 2026-08-22: provider fixtures are synthesised. The invariant stands.** No real clinician's
name, NPI, address, or telephone number enters this repo. Drug and plan fixtures are unaffected —
this is specifically about provider-shaped responses, from either upstream.

The invariant is cheap to keep and expensive to re-establish once broken, and `pii:npi` is a useful
tripwire precisely because it has no legitimate exceptions. Recording a handful of real practitioners
would also be the one place this repo published information about **private individuals** who never
chose to appear in it — FOIA-disclosable is not the same as *published by us*.

#### What synthesising actually costs, which is more than it first appears

An NPI is Luhn-checked over the prefix `80840` plus its first nine digits, so a synthetic NPI has to
be **constructed** rather than typed. That much was obvious. The part that was not:

**A Luhn-valid synthetic NPI still trips `pii:npi`.** The marker is two filters — Luhn *and* the
literal word "NPI" within 40 characters — and a fixture whose field is named `npi` supplies the
second. Verified against the live marker:

| Fixture shape | Luhn-valid synthetic NPI | Flagged? |
|---|---|---|
| Marketplace `{"npi": …}` | yes | **yes** |
| NPPES `{"number": …}` | yes | no — NPPES names the field `number` |
| any shape | no (invalid check digit) | no |

So the naive reading of "synthesise and the count stays at zero" is **wrong**, and the NPPES case
passes only by the accident that NPPES calls the field `number`. The moment a wrapper model, a test,
or a docstring names it `npi` next to the digits — which `ProviderRecord.npi` naturally will — the
marker fires.

**The escape is not to use invalid NPIs.** A fixture whose check digits fail would be rejected by
the very validation the wrappers exist to perform, so the fixtures could never exercise the happy
path. Synthetic identifiers must be *structurally real*.

#### Therefore, one requirement on the build — and two that were wrong

1. **Synthetic NPIs are Luhn-valid**, generated by a helper rather than hand-picked, so a fixture
   exercises real validation. Built: `tests/synthetic.py`, checked against the scanner's own
   `npi_luhn` by `tests/test_synthetic.py` so the generator and the validator are two independent
   implementations that must agree.

2. ~~`sensitive_baseline.toml` gains an allowlist entry.~~ **Not done, and it should not be.**
   Approved 2026-08-22 after the build showed why. An allowlist scoped to the fixture directory
   would forgive a *real* recorded provider response landing there later — **disarming the exact
   tripwire this section exists to preserve.** The alternative costs nothing: every identifier is
   **computed at test time and never written as a literal**, so `pii:npi` stays at a true zero and
   the marker stays fully armed. `make scan` confirms it.

3. ~~The glossary's `pii:npi` claim is restated.~~ **Unnecessary, given (2).** "The count stays at
   zero" turned out to be exactly right rather than a proxy that had been outgrown.

**The rule in (2) is enforced everywhere, not only in fixtures** — and the build proved it needed
to be. The first draft of gold question `live-05` put a Luhn-valid NPI in its *question text*, and
`make scan` caught it as `pii:npi` above baseline. It was fixed by changing the question to use a
check-digit-invalid number (which grades a real branch: "that is not a well-formed NPI"), not by
allowlisting the gold set. `tests/test_gold_set.py::test_the_gold_set_carries_no_valid_npi` keeps it
from coming back.

**Do not write an example NPI into any tracked file, including this one.** A Luhn-valid ten-digit
run beside the word "NPI" is exactly what the marker catches, and prose is not exempt — the same
convention the glossary already states for SSNs: *write the pattern, never a specimen.* The helper
generates them; no document quotes one.

## 9. What this phase adds, and what it must not

**Adds:** six tools over three live APIs, registered beside the three relational tools **in the same
lane**, under the same `source_type: structured_api`. Plus the eval questions that make live-vs-mirror
routing measurable, and the fixtures that keep all of it offline in `make check-all`.

**Must not:**

- **Must not open a fourth lane.** Phase 1-c opened the structured lane; this extends it. The badge
  exists, `CitationCard` renders row-shaped evidence, and [frontend_plan.md](frontend_plan.md) F2
  already says this phase adds "live-API citations rendering beside mirror ones". A `source_type` of
  `"live_api"` would be a contract change to express something the contract already expresses.
- **Must not let an outage look like an answer** — [web_search_tool.md](web_search_tool.md) §8's
  rule, inherited unchanged and now applying to three upstreams instead of one. §13.
- **Must not let an *answer* look like an outage** — the inverse, and new this phase. openFDA
  returns **HTTP 404 with `{"error": {"code": "NOT_FOUND"}}` when a search matches nothing**, and
  for *"has drug X been recalled"* that 404 **is the answer: no recalls.** Treating it as a failure
  would abstain on the one question the tool exists to answer. §10b.
- **Must not vendor provider PII.** Settled in §8a: provider fixtures are **synthesised**, and no
  real clinician's name, NPI, address, or telephone number enters this repo.
- **Must not reshape `AnswerDeps`' citable-set pattern.** A live record joins `seen_rows` rather
  than growing a fourth dictionary — §14.

### The one risk worth naming up front

The agent currently sees **nine** tools (five reference, three relational, one web). Six more makes
fifteen, and Phase 2's measured success — `web_search` called on 0 of 30 reference questions — was
achieved with *one* new tool whose over-use the prompt explicitly warned against. **Tool-count
inflation is this phase's characteristic failure mode**, and it will show up as routing accuracy
falling on the *reference* slice, not the new one. The eval slice in §16 is designed to catch that
specifically, and §11 spends its budget justifying why six and not nine.

## 10. The other two contracts, verified live

§7 established Marketplace. openFDA and NPPES were verified the same way, on 2026-08-22.

### 10a. NPPES — one endpoint, and it is all PII

`GET https://npiregistry.cms.hhs.gov/api/?version=2.1&number={npi}` → `200` with:

```
{result_count: 1, results: [{number, enumeration_type, basic{...}, addresses[], taxonomies[],
                             identifiers[], other_names[], practiceLocations[], endpoints[],
                             created_epoch, last_updated_epoch}]}
```

`basic` carries `first_name`, `last_name`, `credential`, `enumeration_date`, `certification_date`;
`taxonomies[]` carries the specialty (`desc`, `primary`, `state`, `license`); `addresses[]` carries
**street address and telephone number** per location.

Three consequences:

1. **`result_count: 0` is the "no such NPI" answer**, returned with `200` — unlike openFDA, which
   uses a 404 for the same meaning. Two upstreams, two conventions, and neither is an outage. Each
   client owns its own mapping; there is no shared "is this empty" helper that could be right for
   both.
2. **The specialty question is answered from `taxonomies[]`, not `basic`** — and a provider can hold
   several, exactly one flagged `primary: true`. A wrapper that takes `taxonomies[0]` will
   occasionally report a secondary specialty as *the* specialty.
3. **This response is more PII-dense than the Marketplace's.** §8a was framed around names and
   NPIs; NPPES adds street addresses and telephone numbers to the same fixture problem — so the
   synthetic-identity helper §8a requires has to cover four field families, not one. Note the one
   piece of luck: NPPES names the NPI field `number`, which is why an NPPES-shaped fixture does not
   trip `pii:npi` on its own. Do not rely on it — §8a's table says where it stops being true.

### 10b. openFDA — an envelope, a disclaimer, and everything in lists

`GET https://api.fda.gov/drug/label.json?search=openfda.brand_name:"Lipitor"&limit=1` → `200` with
`{meta, results}`. Two endpoints are in scope: `/drug/label.json` (indications, warnings,
interactions) and `/drug/enforcement.json` (recalls).

Four properties that shape the wrappers:

1. **No match is HTTP 404**, body `{"error": {"code": "NOT_FOUND", "message": "No matches found!"}}`.
   Verified on both endpoints. Per §9 this is an *answer* for recalls and an *absence* for labels,
   and the client must distinguish that 404 from a transport failure before anything else.
2. **Nearly every field is a list, including scalar-looking ones.** `openfda.brand_name` is
   `["Lipitor"]`; `openfda.generic_name` is `["ATORVASTATIN CALCIUM"]`; `indications_and_usage` is a
   one-element list holding the entire section as one string. `effective_time` and `id` are bare
   strings. This is an SPL artefact, not an accident, and it is the single most likely source of a
   silent `str`-vs-`list[str]` bug in the phase. **Model them as lists and unwrap explicitly**,
   never with a `[0]` that a one-element assumption makes invisible.
3. **A label result has 38 top-level fields, most of them multi-paragraph prose.** Returning one
   whole would blow a large fraction of the context window on a single tool call. §11 makes section
   selection a parameter rather than an afterthought.
4. **`meta.disclaimer` says "Do not rely on openFDA to make decisions regarding medical care."**
   This repo cites its sources; a source that disclaims its own reliability is worth carrying into
   the citation rather than dropping at the boundary. §14.

### 10d. CMS serves only the states that use HealthCare.gov

**Found while building, and it invalidates one of `plan.md`'s acceptance tests.** A plan search for
a state that runs its own exchange returns `400 "state is not a valid marketplace state"`. Verified
2026-08-22: **CA, GA and NY are refused; NC and TX work.**

Two consequences, and the second is the sharper one:

1. **A 400 here is an answer, not an outage.** The reader is not out of options — they are in the
   wrong place, and *"your state runs its own marketplace"* is the useful reply. `PlanMatches`
   carries `state_not_served` as a first-class field beside `unavailable` for exactly this, and
   `find_plans`'s description tells the model not to report it as "no plans available".
2. **`plan.md`'s acceptance test cannot be satisfied as written.** It asks for *"plans in ZIP 30076
   for a family of 3"* — and 30076 is in **Georgia**, which this API refuses. The gold set uses ZIP
   27360 (North Carolina) for the plan-search question instead, and keeps 30076 as `live-04`, whose
   correct answer is precisely that CMS cannot price plans there.

### 10e. A ZIP can span more than one county, and premiums differ

`/counties/by/zip/30341` returns **two** counties (DeKalb and Fulton), verified 2026-08-22.
Premiums are set per county via rate areas, so which one is not a detail a wrapper may pick: a
silent choice returns a real number that answers a different question, and the reader cannot tell.
`find_plans` prices the first county and returns the rest in `other_counties`, so the answer names
which county it is about — see §11. An explicitly named `countyfips` that does not belong to the ZIP
is still a `ModelRetry`, because that one really is a mistake to correct rather than a fact to
report.

### 10c. The join that makes the two drug sources one lane

openFDA's `openfda.rxcui` is a **list of every RxCUI on the label** — for Lipitor it contains both
`262095` (the branded 80 mg tablet) and `259255`. Marketplace's `/drugs/covered` on that same plan
returned `coverage: "GenericCovered", generic_rxcui: "259255"`.

**The two APIs agree, in the same identifier space, without a mapping table.** That is what lets a
compound question — *"is Lipitor covered, and has it been recalled"* — resolve to one drug rather
than to two coincidentally-similar names, and it is the concrete reason RxCUI is the drug key
throughout this phase rather than a brand-name string. It is also a real Phase 4 multi-hop, arriving
early enough to design for.

**Plan IDs join the same way.** Marketplace returned `77264NC0010049` — a 14-character HIOS
**Standard Component ID**, the same key space as the Exchange PUF's `StandardComponentId` that the
Phase 1-c mirror is already keyed by. Verify against the mirror on first contact, but if it holds,
the live and vendored halves of the structured lane share a primary key, and §15's reconciliation
rule has something to reconcile *on*.

## 11. The tool surface — six tools, named for questions

Tool descriptions are prompt surface ([relational-tool.md](relational-tool.md) §3,
[web_search_tool.md](web_search_tool.md) §3). These are named for the **user question** they answer,
not the endpoint they wrap, because the model routes on the description.

| Tool | Source | Signature | Answers |
|---|---|---|---|
| `find_drug` | Marketplace | `(name: str) -> DrugMatches` | resolves a drug name to RxCUIs |
| `check_drug_coverage` | Marketplace | `(rxcuis, plan_ids, year=None) -> CoverageResult` | *"is drug X covered under plan Y"* |
| `find_plans` | Marketplace | `(zipcode, people, income, year=None) -> PlanMatches` | *"find plans in ZIP 30076 for a family of 3"* |
| `drug_label` | openFDA | `(name, section: LabelSection) -> LabelSection` | *"what is drug X indicated for"* |
| `drug_recalls` | openFDA | `(name) -> RecallResult` | *"has drug X been recalled"* |
| `lookup_provider` | NPPES | `(npi) -> ProviderRecord` | *"what's this NPI's specialty"* |

### Why six, and not the nine the endpoints suggest

Three endpoints are deliberately **not** tools, and each absence is a decision:

- **`GET /counties/by/zip/{zip}` is folded into `find_plans`.** `/plans/search` requires a
  `countyfips`, and no user knows theirs. Exposing the resolution as a tool would make the model
  perform a mechanical lookup it cannot get wrong in an interesting way, and would spend a routing
  decision on plumbing. **The tool takes what a person says (a ZIP); the wrapper does what the API
  needs.** One user-visible call, two HTTP requests.

  **A ZIP that spans two counties is answered, then qualified** (§10e originally said the opposite).
  The first build raised a `ModelRetry` asking the model to choose, on the reasoning that a silent
  pick answers a different question than the one asked. Right about *silent*, wrong about *pick*:
  measured, the agent asked the reader which of Davidson and Randolph county to use and **abstained
  on a question it could answer**. Prices plus "these are for Davidson County; Randolph differs"
  serves a reader better than a clarifying question, so the ambiguity now travels in
  `PlanMatches.other_counties` where the answer can carry it.
- **`GET /market-years` is folded into every year-taking tool** as the default, per §7c. A `year`
  the caller omits resolves to `current` once per run and is cached on `AnswerDeps`, not re-fetched
  per call.
- **`/providers/covered` and `/providers/search` are deferred to Phase 5**, not built here.
  [plan.md](plan.md) Phase 5 owns network checks; Phase 3's acceptance test asks only *"what's this
  NPI's specialty"*, which NPPES answers. Deferring them also halves the provider-fixture surface
  §8a has to synthesise. Recorded in §18b.

Two more shaping decisions:

- **`find_drug` stays separate from `check_drug_coverage`** even though every coverage question
  needs both (§7b). Merging them would hide the resolution step from the trace, and the resolution
  is exactly where an ambiguous drug name goes wrong — *"Lipitor"* returns four strengths, and which
  one the user meant is a question the model should be seen to answer, not one a wrapper should
  silently pick.
- **`drug_label` takes a `section` argument** — `Literal["indications", "warnings", "interactions",
  "adverse_reactions", "dosage"]` — because §10b.3's 38 prose fields cannot all reach the model.
  Selection at the boundary, not truncation after it: a truncated warnings section in a health tool
  is worse than an absent one.

## 12. Module layout

Follows the shape both prior lanes already established — a `client` module per upstream that imports
nothing from `agent/`, and one `agent/` module that adds the trace and the run-scoped bookkeeping.

```
src/health_coverage_navigator/
  live/                     # NEW — the read side, no PydanticAI import anywhere in it
    __init__.py
    models.py               # ApiRecord + per-source typed models
    _http.py                # shared: httpx client factory, timeout, retry/backoff transport
    marketplace.py          # MarketplaceClient
    openfda.py              # OpenFdaClient
    nppes.py                # NppesClient
  agent/
    live_tools.py           # NEW — the six tools; mirrors web_tools.py
    deps.py                 # + three optional clients, + resolved market year
    tools.py                # select_tools grows a `live: bool` axis
tests/
  test_live_clients.py      # NEW — per-client, offline, fixture-driven
  test_live_agent.py        # NEW — tool behaviour through the agent, mirrors test_web_agent.py
tests/fixtures/live/        # NEW — recorded responses, §8
```

**`live/` rather than extending `structured/`.** The lane is shared; the *machinery* is not.
`structured/` is DuckDB over local Parquet — no network, no key, no rate limit, and its `store.py`
holds a connection whose lifetime is the process. Putting an HTTP client beside it would give one
package two failure models and force `structured/` to import `httpx`. The lane is expressed by
`source_type` and by both toolsets registering together, not by sharing a directory.

**One `_http.py`, three clients, no base class.** The shared part is genuinely shared — timeout
policy, a `Retry-After`-honouring transport (Phase 2 already learned that this belongs in a
transport, not in a client), a `User-Agent`. The *non*-shared part is everything that matters: three
different auth mechanisms (`apikey` query param / none / none), three different empty-result
conventions (§10a.1 vs §10b.1), three different envelopes. An abstract base class would have to
declare all three as overridable, which is a base class in name only.

## 13. Failure, budgets, and caching

### 13a. The failure table

Every case below returns a **typed result with `unavailable` set**, never an exception escaping into
the agent loop — `WebSearchResults.unavailable` is the precedent and the pattern is copied whole.

| Condition | Marketplace | openFDA | NPPES |
|---|---|---|---|
| No match | `{coverage: []}` → empty result | **404 `NOT_FOUND` → answer, not failure** | `result_count: 0` → answer |
| Missing/blank key | tool not registered (§15) | n/a | n/a |
| Expired key (§3) | 401 → `unavailable="credential"` | n/a | n/a |
| Rate limited | 429 → honour `Retry-After`, then `unavailable="rate_limit"` | 429 → same | 429 → same |
| Timeout / 5xx | `unavailable="upstream"` | `unavailable="upstream"` | `unavailable="upstream"` |
| Malformed body | `ValidationError` → `unavailable="upstream"`, logged with `request-id` | same | same |

**The 401 case deserves its own value rather than folding into `upstream`.** §3's 60-day expiry
makes it the *likely* Marketplace failure in month three, and it is the one an operator can fix in a
minute — but only if the message says "the key" rather than "the API". An expired key is an outage
with a due date, and the answer must say so.

### 13b. Budgets

`WebConfig` bounds a lane that costs money; this lane costs someone else's capacity. Same mechanism,
different justification. Per **run**, not per process:

- `max_calls` across all six tools — the ceiling that stops a loop, mirroring the web lane's search
  budget. A spent budget is **not** a `ModelRetry` (Phase 2 has a test asserting exactly this
  distinction); it is a terminal, honest "I have used my lookups for this question".
- `timeout_s` per call.
- Concurrency via `asyncio.Semaphore`, never a thread pool — CLAUDE.md's async rule, and the case
  where it bites is `check_drug_coverage` fanning out over several RxCUIs.

### 13c. Caching

Phase 3's checklist calls for a response cache, and §4 makes it load-bearing for openFDA
specifically (a per-**IP** daily limit that a restart does not reset).

**Recommended: a run-scoped in-memory cache in the first increment, keyed by (source, endpoint,
normalised params) — and nothing persistent.** The argument is that the value is almost entirely
*within* a run: `find_drug` then `check_drug_coverage` then `drug_recalls` on the same drug is the
common shape, and the eval sweep's repetition is across questions, not within them. A cross-run disk
cache buys the openFDA daily limit real headroom, but it introduces staleness into a lane whose
entire selling point is being current — *"is this covered now"* answered from yesterday's cache is
the exact failure the lane exists to avoid.

**So: persistent caching is deferred, with a stated tripwire** — if an eval sweep is observed
approaching openFDA's 1,000/day, add a disk cache with a TTL measured in hours and a
`--no-cache` escape for smoke runs, not before. §18b.

**The cache key trap, for the fourth time.** [web_search_tool.md](web_search_tool.md) §11 records it
twice and [relational-tool.md](relational-tool.md) once: any cache key that omits a dimension the
result depends on returns another dimension's answer. Here the dimensions are `year` and, for
`find_plans`, the whole household — two people in the same ZIP with different incomes get different
plans. **The key includes the resolved `year`, never the caller's `None`.**

## 14. Provenance: what a citable live-API record is

### 14a. It is a row, and that is not a shortcut

A live record joins **`seen_rows`**, the dictionary Phase 1-c already built, and is cited with the
existing `row_id` + `cells` shape. No fourth citation shape, no fourth dictionary, no change to
`AgentCitation`'s validator.

The reason is not economy — it is that the claim being made is the same claim. A mirror row says
*"this exact record exists in this source"*; so does an NPPES result. Both are exact, both are
per-record, both carry `source_type: structured_api`, and `CitationCard` already renders the shape.
[web_search_tool.md](web_search_tool.md) §6 had to add a third shape because a web page's evidence
genuinely differs — it is a quotation from prose that may vanish. A record is not prose.

**That last sentence is right about a recall and a premium and wrong about a drug label.** An FDA
label section measured 2,199 characters and its warnings sections run to 11,000 — a passage, not a
value. The row contract's byte-exact cell comparison was built for `'$4,500 '`, and demanding a
verbatim copy of a page to cite a sentence is a thing no model can do; the first sweep spent
`live-01`'s entire retry budget failing exactly that. So above a length threshold a cell is
**quoted from** rather than reproduced — verbatim containment, the same guarantee the chunk path
gives passages. `runtime._PROSE_CELL_CHARS` carries the threshold and the reasoning.

### 14a-bis. A negative finding is citable, and that is not a loophole

**Approved and built 2026-08-22, after the build hit the hole.** *"The FDA holds no recall for this
drug"* is true, useful, and exactly what was asked — but with an empty result there is nothing in
`seen_rows`, so `_validate_grounding` forces the model to either **abstain** (false: it did answer)
or cite something it never saw. Neither is acceptable in a tool whose whole selling point is that an
empty result is an answer (§9).

So **the search itself is the citable record.** An empty `drug_recalls` still emits one row — the
query that ran and the fact that it matched nothing — and a not-found `lookup_provider` does the
same. The claim being made is *"I looked here and found nothing"*, and that row is precisely the
evidence for it. The grounding rule is applied, not bent.

**This hole exists in the mirror half too and is not closed there.** [relational-tool.md](relational-tool.md)
§6 says "empty is an answer" without saying how such an answer gets cited, so a `query_structured`
returning zero rows lands in the same bind. Left alone deliberately — it is Phase 1-c code, and
changing it belongs in a change that can measure the effect on the mirror slice rather than riding
along with this one. Recorded here so it is a known gap rather than an inconsistency.

**Id namespacing carries the live/mirror distinction**, since that is the one thing a reader must
not lose: mirror rows keep `#q{n}.{m}`; live records take a source-tagged form —
`mkt#1.1`, `fda#2.1`, `npi#3.1`. Same forgery property as every other id in this repo: validity is
membership in a dictionary the tools alone write, not a well-formed string.

### 14b. Live records have a URL, and mirror rows do not

`Citation.url` is `None` for a mirror row — there is no public address for row 41,922 of a Parquet
file. **A live-API record has one: the query URL that produced it.** `GET /drug/label.json?search=…`
is re-fetchable by anyone, and NPPES's is too.

So this phase populates `Citation.url` for structured-lane citations for the first time, **with no
contract change** — the field has existed since Phase 0 and `CitationCard` already renders both
branches. It is the most user-visible improvement in the phase and it costs nothing.

**One exception, and it is not optional: the Marketplace URL carries the API key as a query
parameter.** A citation URL is rendered in the browser and serialised into eval run files. **Strip
`apikey` before the URL is stored**, and add a test asserting no citation URL contains it — this is
the one place in the phase where a plumbing mistake leaks a credential into a tracked artefact.

#### 14b-bis. The Marketplace exception is two things, not one

The paragraph above is right and incomplete, and the half it left out cost this section its own
premise. **A stripped Marketplace URL is not re-fetchable.** `apikey` is required on *every* CMS
Marketplace endpoint (§5), so what survives the strip returns `401` to anyone who clicks it —
verified 2026-08-27 against `/plans/search` and `/drugs/covered`. `/plans/search` is a **POST**
besides, so the GET-shaped URL built for it was never an address a reader could fetch.

So §14b's opening claim — *a live record has the URL that produced it, re-fetchable by anyone* —
holds for openFDA and NPPES, which are keyless GETs, and **is false for the Marketplace**.

Two failures followed from it, and the second is the worse one:

- Four negative branches and the *positive* drug-match branch emitted `url=None`. `source_url()`
  ([`structured/catalog.py`](../src/health_coverage_navigator/structured/catalog.py)) keys only the
  vendored mirrors, so `_row_citation`'s `row.url or source_url(row.source)` fallback had nothing to
  fall back to, and those citations rendered unlinkable.
- The plan and coverage branches, which *did* carry a URL, linked to that 401. **A citation that
  looks checkable and is not undercuts the provenance guarantee more than one that plainly is not**
  — the reader who clicks is told the source is broken, not that it is elsewhere.

**The rule: `Row.url` is read by a human; the exact query is machine provenance and belongs in a
cell.** So every Marketplace row — positive and negative alike — links to the consumer page a person
can actually open (`MARKETPLACE_PUBLIC_URL` in
[`agent/live_tools.py`](../src/health_coverage_navigator/agent/live_tools.py)), and the query URL
travels as a `source_url` cell, which `_plan_rows` was already doing. `state_not_served` is the one
row with a better page than the plan finder: *"Georgia runs its own exchange"* is checkable against
[marketplace-in-your-state](https://www.healthcare.gov/marketplace-in-your-state/), which makes that
citation genuinely verifiable rather than decorative.

openFDA and NPPES get **no fallback entry**. Both populate `source_url` on every branch, so one
would be unreachable code implying a gap that does not exist.
`test_a_marketplace_row_links_somewhere_a_reader_can_open` is the structural guard, parametrised
over all seven Marketplace shapes because the next instance will be in whichever branch nobody
thought to re-check — the same argument as
`test_a_reached_lookup_is_always_citable`, one field over.

### 14c. openFDA's disclaimer travels with the citation

§10b.4: openFDA disclaims its own accuracy in every response. The honest handling is to carry it —
the citation's `title` names the source and its recency (`openFDA · label effective 2024-04-15`),
and the tool's own description tells the model that openFDA is a labelling record, not clinical
advice. This is the same instinct as Phase 2's domain-and-date citation titles: provenance the
reader can act on, rather than a bare source name.

## 15. Mirror-vs-live reconciliation

Phase 3's checklist asks *"which source is authoritative for a given question, and what the agent
does when they disagree"*. §10c makes it answerable: the two halves share a key space.

**The rule, by question rather than by source:**

| Question shape | Authoritative | Why |
|---|---|---|
| *"What did plan X file for plan year Y"* | **mirror** | The PUF *is* the filing, versioned by year and stable. The live API has no notion of "as filed". |
| *"Is drug X covered right now"* | **live** | Formularies change mid-year. A mirror answer is a claim about a snapshot, stated as if about today. |
| *"What plans can I buy in ZIP Z"* | **live** | Availability and price are current-state by definition. |
| *"What is this NPI's specialty"* | **live (only)** | No provider data is vendored, ever (CLAUDE.md). |

**When they disagree on the same question, the agent says so and cites both.** It does not pick.
A silent preference is indistinguishable from a wrong answer to the person reading it, and this repo
has a citation mechanism precisely so a conflict can be shown rather than resolved by fiat. Two
citations with the same claim and different values is a *good* answer to a question where the
sources genuinely differ — and it is also a bug report, since a systematic disagreement means one of
the two is being queried wrong.

**The `plan_year` field already on `AnswerDeps` is the coordination point.** It exists so the
relational tools pick a partition; the live tools read the same value so both halves answer about
the same year rather than each assuming. Where the request pins no year, §7c's `current` fills it —
once per run.

## 16. Evals

Phase 3's slice is **tri-modal routing**: reference vs. structured vs. web, with structured now
split into mirror and live. Each phase adds exactly one thing to grade, and this is it.

### 16a. What the gold set grows

`expected_source_type` already carries `structured_api`. Three additions:

- **~6 live-API questions** — two per source, covering the acceptance tests in
  [plan.md](plan.md) Phase 3: a drug-coverage question, a plan-search question, an NPI-specialty
  question, a recall question.
- **A `expected_lane_detail: mirror | live` field** on structured questions, additive, so
  "routed to the structured lane" and "routed to the *right half* of it" score separately. Without
  it the phase's two named failure modes are invisible: reaching for the live API when the mirror
  answers offline, and trusting the mirror where only live is current (§15).
- ~~**One abstention that turns answerable.**~~ **`abs-01` does not turn answerable at Phase 3, and
  the plan contradicted itself here.** The question is *"which dermatologists near ZIP 30076
  currently accept Aetna?"* — a **network** question. Its own gold-set note always said it needed
  *"NPPES **plus a plan-network source**"*, and §11 defers the plan-network source
  (`/providers/covered`) to Phase 5. NPPES answers what a provider *is*, never who pays for them.
  Two of this document's own sections disagreed, and §11 was right: `plan.md` gives Phase 5 network
  checks.

  A second, independent reason it could not have worked: **ZIP 30076 is in Georgia**, which CMS's
  API refuses outright (§10d). `becomes_answerable_at_phase` is re-dated to `"5"`, with the
  reasoning in the question's notes so it is not silently re-dated back.

  **The phase therefore has no headline abstention-to-answer flip**, which is worth saying plainly
  rather than finding a substitute for.

### 16b. The metric that must not move

The existing 30 reference questions and their recall@5. §9's tool-count risk shows up here or
nowhere. **Report reference-slice routing accuracy with and without the live toolset registered**,
exactly as Phase 2 reported `web_search` called on 0 of 30 reference questions. If it falls, the fix
is tool descriptions and prompt, not more tools.

### 16c. What is *not* graded, and why

**Answer correctness on live-API questions is not scored against a fixed expected string.** A
premium changes, a formulary changes, a provider retires. This is Phase 2's reasoning
([web_search_tool.md](web_search_tool.md) §12) applied to a second volatile lane: grade **routing**
(did it use the right tool) and **groundedness** (is every claim backed by a record the tool
actually returned), and let correctness be asserted by the offline fixture tests, where the answer
*is* fixed because the response is.

## 17. Tests

**All offline, all inside `make check-all`, no key required** — the suite sets
`ALLOW_MODEL_REQUESTS = False` and reaching a live API in a unit test would break the property that
`check-all` costs nothing.

- `test_live_clients.py` — per client, against recorded fixtures: envelope parsing, the
  list-vs-scalar unwrapping of §10b.2, and **one test per row of §13a's failure table**. The
  highest-value tests in the phase are the two that assert *no match is not an outage*: openFDA's
  404 and NPPES's `result_count: 0` must both produce a confident negative answer, not
  `unavailable`.
- `test_live_agent.py` — tools through the agent with a stubbed model, mirroring
  `test_web_agent.py`: a tool returning `unavailable` produces an honest failure not a fabrication;
  a spent budget is terminal and **not** a `ModelRetry`; a citation to a record no tool returned
  fails the grounding validator.
- **`test_no_apikey_in_citation_urls`** — §14b's leak guard. Small, and the only test here whose
  failure would be a security finding rather than a bug.
- **`test_a_marketplace_row_links_somewhere_a_reader_can_open`** — §14b-bis, parametrised over
  every Marketplace shape that produces a row. Asserts each carries a `url` *and* that it is not
  the key-gated API host, which is the pair of failures §14b's incomplete rule allowed.
- **A fixture-freshness test**, following the `x-version` stamp of §8: assert every fixture carries
  its provenance headers, so a fixture recorded without them cannot land.

## 18. Build order

Ordered by **risk retired per step**, not by source. Each step ends green.

### 18a. The steps

**Step 0 — the synthetic-identity helper, and its allowlist entry.** §8a is **decided**, so this is
a build task rather than a question: a generator for Luhn-valid synthetic NPIs plus synthetic names,
addresses and phone numbers, and the `sensitive_baseline.toml` entry that declares them. First
because it is small, because it unblocks step 4, and because the scanner conversation is better had
now than in the diff that also adds a tool. Ends with `make scan` green and no fixture yet existing.

**Step 1 — `live/_http.py` + `OpenFdaClient` + `drug_label` / `drug_recalls`.** openFDA first, and
deliberately not the source Phase 3's headline is about: it needs **no key and has no PII**, so it
carries none of the phase's blocked decisions — while forcing both of its hardest *shape* problems
immediately. The 38-field response makes section selection real in step 1 rather than a retrofit,
and the 404-is-an-answer case builds the failure table's most interesting row first. Ends with two
tools registered, fixtures recorded, `make check-all` green.

**Step 2 — `MarketplaceClient` + `find_drug` + `check_drug_coverage`.** The demo key (§3a) suffices.
This is where §10c's RxCUI join gets exercised and where the `generic_rxcui` conditional (§7b) gets
its test — the single most consequential correctness detail in the phase.

**Step 3 — `find_plans`.** The ZIP→FIPS fold (§11), the household request model, and the largest
response in the phase. Last of the Marketplace tools because it is the only one whose *request* is
non-trivial.

**Step 4 — `NppesClient` + `lookup_provider`**, on whatever step 0 decided. The `taxonomies[]`
primary-flag trap (§10a.2) is the correctness detail here.

**Step 5 — reconciliation + evals.** §15's rule, the gold-set additions, and `abs-01`'s promotion.
Eval work lands with the phase, not after it — but it lands *last* because it grades routing across
tools that must all exist first.

**Step 6 — the demo-key guard** (§3a): the eval runner refuses to start on CMS's published key.
Three lines, and it belongs after step 5 because that is when a runner exists to guard.

### 18b. Deferred deliberately

*None of these was built. [future_enhancements.md](future_enhancements.md) §2–§3 carries them
forward, with the cache tripwire from §13c.*

- **`/providers/covered` and `/providers/search`** → Phase 5, which owns network checks (§11).
- **`POST /households/eligibility/estimates`** — APTC/CSR subsidy estimates. A genuinely useful
  answer and squarely a Phase 5 "plan comparison" capability; Phase 3's acceptance tests do not ask
  for it, and it is the one Marketplace endpoint whose output is a *calculation* rather than a
  record, which makes citing it a different problem.
- **A persistent response cache** — §13c, with its tripwire.
- **openFDA's other endpoints** (NDC directory, Orange Book, Drugs@FDA, adverse events). Label and
  enforcement answer the questions [plan.md](plan.md) names; the rest is scope.
- **Automatic key-rotation handling** (§3). The 60-day expiry is real, but the fix — re-reading
  `.env` without a restart — means unfreezing `Secrets`, and a clear 401 message is most of the
  value for none of the risk.

## 18c. Two defect families, and the rules that came out of them

Eight defects were found building this phase. Six were instances of **two families**, and both are
specific to a lane whose evidence comes back from someone else's server. They are written as rules
because each one recurred after being fixed once.

### Family 1 — a finding with nothing to cite

The grounding validator requires a non-abstained answer to cite something. So **any true finding
with no citable row forces a false abstention, or a worse source.** Four instances were found while
building the phase:

| Finding | What went wrong before it was citable |
|---|---|
| openFDA returns no recalls | forced abstention on *"no recalls on record"* — a true answer |
| NPPES holds no such NPI | same |
| NPPES rejects a malformed NPI | same, for a fact about the *input* |
| CMS does not serve a state | agent had CMS's own answer and **cited a web page for it** |

**The rule: if a tool can establish something, it must emit a row for it — including when what it
established is an absence.** The search that found nothing *is* the evidence that nothing is there.
This is not a loophole in the grounding rule; it is the rule applied to a negative claim.

**Fixing it four times is not the same as closing it, and the difference cost five more instances.**
Each fix was made where someone noticed, the rule was written down but never enforced, and a
2026-08-25 walkthrough found the identical shape in five further paths — `drug_label` with no
matching label, `drug_label` with no such section, `find_plans` with an empty result, `find_drug`
with an unrecognised name, and `check_drug_coverage` with an empty envelope. Closed structurally on
2026-08-27:

- **`_search_row` in `agent/live_tools.py`** is the one place a reached-but-empty lookup becomes a
  row, and its docstring carries the rule and both of its boundaries.
- **`_ROW_BUILDERS` / `rows_for`** replaces six direct builder calls with a registry keyed on result
  type, so a result shape with no builder raises rather than silently recording nothing.
- **A citable row is necessary, not sufficient.** Measured on `live-07`: with the row in place and
  its cells correct, the agent still abstained, because `drug_label`'s docstring said only "try the
  generic name, or say the drug was not found" where `drug_recalls` says *"an empty result is a real
  answer, not a failed search ... do not soften it"*. The row removes the **obstacle** to answering;
  the tool's own text still has to supply the **instruction**. Both halves are now present on both
  tools.
- **Three tests in `tests/test_live_agent.py` enforce it** instead of memory:
  `test_a_reached_lookup_is_always_citable` over all nine negative shapes,
  `test_an_outage_stays_uncitable` for the inverse, and `test_every_live_tool_has_a_row_builder`,
  which walks `LIVE_TOOLS` and reads each tool's return annotation — so **the next tool anyone adds
  inherits the invariant rather than having to remember it.**

Two boundaries the fix must not cross, and the first is the one a well-meaning version breaks:

- **`unavailable` still emits nothing.** An outage is not a finding — nothing was looked up, so
  there is nothing to cite. A row there would let the model cite the fact that it *failed*, turning
  "I could not check" into a sourced claim. That is the same defect pointing the other way, and a
  worse one.
- **A negative row is still a row**: source-namespaced id readable off the result (family 2 below),
  and cells that are short values rather than prose. `find_drug` gained per-match ids in the same
  change, because *"I checked the 20 mg tablet"* is a claim about a lookup's output like any other.

**Still open: the mirror half.** [relational-tool.md](relational-tool.md) §6 says "empty is an
answer" without saying how one gets cited, so a `query_structured` returning zero rows is in the
same bind. Deliberately left — it is Phase 1-c code whose change should be measured against the
mirror slice, so it is a separate change with its own measurement.

### Family 2 — a name the model can see but cannot cite

A citation names a row by id and a cell by key, and the only names available are what the tool
handed back. **When those drift, the model is rejected for doing exactly the right thing.** Four
instances:

| Mismatch | Consequence |
|---|---|
| result exposes `record_id`, citation field is `row_id` | model cited it as `result_id` — the *web* shape — and was refused |
| `DrugLabelResult.row_id` was `fda#l1.1`, rows were `fda#l1.1.1` | an id it could see and could not cite |
| `LabelSection.field` vs cell key `section` | rejected for naming a column that did not exist |
| not-served row recorded with **no id on `PlanMatches`** | agent had the answer and abstained, nothing to point at |

**The rule, in two halves:** every cell key must be a field name the model was shown, and every
recorded row's id must be readable off the result it came with.
`tests/test_live_agent.py::test_live_row_cells_use_names_the_model_was_shown` checks both
structurally, because the next instance will be in whichever tool nobody thought to re-check.

**Four more instances, 2026-08-27, all in the *negative* rows** — and the reason they survived is
that the structural guard above only ever ran over *positive* ones. `labels_found` where the model
was shown `label_found` (measured: two retries, budget exhausted, on a question the agent had
answered); `sections_found`, `matches_found` and `coverage_found` the same way; `rejected_because`
where the model was shown `invalid`; and `drug` / `recalls_found` / `searched` on the empty-recall
row, latent only because live-02's drug has recalls and never takes that branch.
`test_a_reached_lookup_is_always_citable` now asserts the cell-key rule over every negative shape.

**A second corollary, from the same run: a negative row must carry the fields that *express* the
emptiness.** With the keys corrected the model still spent a retry reaching for `sections` — the
natural thing to point at when the claim is "there is nothing here", and a real field it had been
shown. A negative row has nothing else to copy, so it carries the empty collection too, spelled as
the JSON the model read (`"[]"`). The positive-row rule is *don't invent names*; the negative-row
rule is that **plus** *carry what the absence is made of*.

**A corollary that cost a fifth round on `live-04`: a cell is something to copy, not something to
read.** That row's `reason` cell held a prose sentence; the model paraphrased it and was refused.
Cells are short values; explanation belongs in the field's docstring, which the model reads and does
not have to reproduce.

### And one that was neither: a prompt that argues with itself

`_WEB_STEPS` told the model to search the web for "a recent recall" — true at Phase 2, false once
`drug_recalls` existed. The first fix **added** a step saying the web was the wrong source for
recalls, leaving two rules in contradiction; the model resolved it by ignoring the new one. The
second fix **removed the stale example**. Same shape in `_out_of_reach`, where "no source you have
names doctors" survived NPPES landing and made the agent decline a question it could answer.

**The rule: when a lane lands, stale instructions are deleted, not counter-argued.** `prompt.py`'s
own docstring warned about exactly this in the abstention direction; Phase 3 hit it twice.

## 19. What building it changed

Every entry here is a decision this document got wrong or did not anticipate, corrected in place
above and listed here so the diff is readable. §18c generalises the six that fell into two families;
this section covers the rest.

**The plan contradicted itself about `abs-01`** — §16a said the provider-directory abstention turns
answerable, §11 deferred the endpoint that would answer it. §11 was right. §16a is struck through
rather than quietly rewritten, because "the phase's headline metric" turning out not to exist is
exactly the kind of thing a plan should be caught doing.

**§8a's requirements 2 and 3 were wrong**, and in the direction that matters: allowlisting the
fixture directory would have disarmed the tripwire the decision existed to protect. Computed
identifiers keep `pii:npi` at a true zero instead. The rule then caught its own author — a
Luhn-valid NPI in a gold question — which is the best evidence available that it is worth keeping.

**A negative finding had nowhere to be cited** (§14a-bis). The grounding validator forced an
abstention on *"no recalls on record"*, which is a true answer. Fixed by making the search itself a
citable row; the same hole is still open in the mirror half and is recorded rather than closed.

**Two contract facts nobody would have guessed from the spec.** SPL section names differ between
prescription and over-the-counter labels, so `SECTION_FIELDS` had to become one-to-many or half the
drug catalogue would have returned a confident nothing (§10b). And CMS serves only FFM states, which
invalidated one of `plan.md`'s acceptance tests (§10d).

**A sixth cache axis, and the bug taken for the sixth time.** `live` is derived from *either* live
client while `marketplace` is derived from one, and `tests/conftest.py` derived them differently
from `stream_answer` on the first try. `ALLOW_MODEL_REQUESTS = False` caught it. The `_agent`
docstring now says "a fifth and a sixth".

**The tool count landed at fifteen, as §9 predicted — and §16b's answer is that it costs the
reference slice nothing measurable.** Measured across a control (`--no-live`) and a live arm: seven
questions changed state between them, three lost and two *gained*, and **none of the losers touched
a live tool**. Over-reach onto pre-existing questions across all 43 was exactly one (`abs-02` called
`find_drug`). recall@5 moved 0.733 → 0.667, well inside the 0.200 spread progress.md already records
at fixed config. **No evidence of harm** is the honest phrasing, not "no harm".

**The eval harness had two bugs of its own, and both scored guaranteed misses as findings.** The
runner never wired the live lane at all, so `make eval` silently dropped all six live questions —
`_build` composed its question list from the other three lanes and nothing added them back. And
`structured_exact_match` scored live questions on `expected_cells` they are *forbidden* to carry,
reporting 0.444 when all four mirror questions had scored 1.000. `is_live` is now checked before
`is_structured`, since both halves share a `source_type`.

**A demo-key guard that refused work it did not protect.** §3a's rule fired whenever the key was
present, including on `--no-live` runs that could not send CMS a request. Scoped to runs that
register the Marketplace tools, and given an explicit `--allow-demo-key` override — a rule with a
deliberate, logged exception survives; a rule people route around does not.

## References

- [Marketplace API key request](https://developer.cms.gov/marketplace-api/key-request.html) ·
  [Marketplace API overview](https://developer.cms.gov/marketplace-api/) ·
  [API specifications](https://developer.cms.gov/marketplace-api/api-spec)
- [Finder API key request](https://developer.cms.gov/finder-api/key-request.html) (not requested — §3)
- [openFDA authentication and rate limits](https://open.fda.gov/apis/authentication/) ·
  [api.data.gov signup](https://api.data.gov/signup/) · [openFDA drug endpoints](https://open.fda.gov/apis/drug/)
- [NPPES NPI Registry API](https://npiregistry.cms.hhs.gov/api-page)
