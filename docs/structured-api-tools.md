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

As the phase is built it grows the sections its two peer documents have — the **endpoint contracts**
(§7, reserved below), the tool surface and its descriptions, the typed request/response models, the
mirror-vs-live reconciliation rule, caching and rate-limit handling, provenance for a live-API
citation, the fixture strategy, and the eval slice. Sections are numbered so those can be added
without renumbering what is already here.

**Verified against the live CMS and FDA pages on 2026-08-22.** Everything in §3–§5 is someone
else's operational policy and can change without warning; re-check before blaming the code.

## 2. The three sources at a glance

| Source | Credential | Status | Blocks the build? |
|---|---|---|---|
| **Marketplace API** | **Required** | Web form → key emailed; turnaround not published (§3) | **Yes** |
| **openFDA** | Optional — **deliberately not requested** | Keyless limits are sufficient; §4 records why and what would reverse it | No |
| **NPPES NPI Registry** | **None exists** | Nothing to request, ever | No |

**Exactly one credential enters this repo for Phase 3**, and `.env.example` has carried its
commented `CMS_MARKETPLACE_API_KEY` placeholder since Phase 0. The other two lanes are keyless — one
because the API has no such concept, one by the decision in §4.

[progress.md](progress.md) records when the Marketplace key was requested and where that stands;
this document does not carry status.

One consequence for build order: **NPPES is the only tool of the three that is unblocked today**,
which makes it the right place to prove the typed-wrapper and fixture patterns while the Marketplace
key is in flight.

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
3. **Rate limits are returned in response headers, not documented.** So the budget is read off a
   live call rather than hardcoded from a doc that does not state one.

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
- **`.env.example`:** uncomment `CMS_MARKETPLACE_API_KEY`; leave it blank, as that file is committed
  and must never hold a real value. **Do not add an `OPENFDA_API_KEY` placeholder** — §4 decided
  against the key, and a commented placeholder for a credential nobody holds is an invitation to
  request one without re-reading the reasoning. If §4's tripwire ever fires, the placeholder and a
  second `SecretStr | None` field are added together, in the change that reverses the decision.
- **Nothing goes in `config.yaml`.** It is committed, and `make scan` treats a credential-shaped
  assignment there as a blocking error. What *may* go there is the non-secret half — whether a
  missing key is fatal, per-source budgets, cache TTLs — the way `agent.web_tools` decides Tavily's
  case today. Which flags Phase 3 actually needs is an open question for the build, not settled here.

## 7. Endpoint contracts

*Reserved.* Per-endpoint request and response shapes, the Pydantic models that wrap them, and what a
malformed response does — to be written as each tool is built.

## References

- [Marketplace API key request](https://developer.cms.gov/marketplace-api/key-request.html) ·
  [Marketplace API overview](https://developer.cms.gov/marketplace-api/) ·
  [API specifications](https://developer.cms.gov/marketplace-api/api-spec)
- [Finder API key request](https://developer.cms.gov/finder-api/key-request.html) (not requested — §3)
- [openFDA authentication and rate limits](https://open.fda.gov/apis/authentication/) ·
  [api.data.gov signup](https://api.data.gov/signup/) · [openFDA drug endpoints](https://open.fda.gov/apis/drug/)
- [NPPES NPI Registry API](https://npiregistry.cms.hhs.gov/api-page)
