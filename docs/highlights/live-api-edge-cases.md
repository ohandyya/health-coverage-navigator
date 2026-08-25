# Three APIs, three ways of saying "nothing" — and why an empty result is an answer

One of the [technical highlights](../technical_highlights.md).

## The problem

Phase 2 established a rule that is easy to state and easy to get wrong: **an outage must never be
served as an answer.** A Tavily failure returns a result whose `unavailable` field says the web
could not be checked, so the model can never report "the web does not cover this" when what actually
happened is that nobody asked.

Phase 3 needs that rule *and its inverse*, which turns out to be the harder half.

Ask *"has atorvastatin been recalled?"* and the honest answer is often **no** — and the FDA
communicates that by returning **HTTP 404**. A client written the obvious way treats a 404 as a
failure, the agent is told the FDA could not be checked, and it hedges or abstains on the one
question the endpoint exists to answer. Nothing throws. Nothing logs. The answer is simply worse, and
worse in a direction that looks like caution.

Now multiply that by three upstreams that agree on nothing:

| "Nothing matched" | How it arrives |
|---|---|
| **openFDA** | HTTP **404** with `{"error": {"code": "NOT_FOUND"}}` |
| **NPPES** | HTTP **200** with `result_count: 0` and an empty `results` list |
| **CMS Marketplace** | HTTP **400** with `{"message": "... not a valid marketplace state"}` — which is not "no plans", it is *"that state runs its own exchange"* |

Three services, three status codes, three envelopes, for one meaning. And two of those codes are in
the range every HTTP client on earth treats as an error. NPPES adds a fourth wrinkle: it reports
*malformed input* the same way it reports success — HTTP 200, with an `Errors` array inside — so a
mistyped NPI and a working lookup are distinguishable only by reading the body.

**The trap is that all three failures are silent.** There is no exception, no 500, no test that goes
red. Each one produces a fluent, confident answer that is wrong about whether anything was checked.

## The approach: three outcomes, not two

The existing rule in [tool-retries.md](tool-retries.md) is two-way — *retry what the model got wrong;
degrade what the world got wrong.* This lane needs a third category, and naming it is most of the
design:

| Outcome | Means | Mechanism | Can the model act? |
|---|---|---|---|
| **Retry** | the model called the tool wrongly | `ModelRetry` — text enters the conversation | yes — call again, differently |
| **Degrade** | the world is broken; **nothing was looked up** | a returned result with `unavailable` set | no — report that it could not be checked |
| **Answer** | the world genuinely holds nothing | a returned result with `unavailable` **unset** and an empty/negative field | no — this *is* the finding; say it plainly |

The whole lane is built so that the second and third can never be confused, in either direction.

### 1. Each client maps its own convention, before anything generic can see it

There is no shared "is this empty" helper, and that is deliberate — one that was right for two of
these upstreams would be wrong for the third. openFDA's 404 is intercepted ahead of all status
handling:

```python
if response.status_code == 404:
    # The whole reason this client exists in the shape it does.
    return None, ""
```

That returns `(payload, unavailable_reason)`. `(None, "")` is *no match*; only a **non-empty second
element** is an outage. The caller then decides what the absence means for its own question, because
the answer differs by endpoint: for recalls it is "no recalls", for a label it is "the FDA holds no
label under that name".

NPPES does the same work against a 200 body, distinguishing three states that share a status code —
`Errors` present (bad input), `result_count: 0` (no such NPI), and a real record. CMS inspects the
*text* of a 400 to separate "this state runs its own exchange" from a genuine rejection, and returns
a **three**-tuple because that API has three outcomes rather than two.

### 2. `describe_status()` enforces the rule by omission

The shared helper that turns an HTTP failure into words a model can repeat has a conspicuous hole in
it, and the hole is the mechanism:

```python
def describe_status(status: int) -> str:
    """One clause naming an HTTP failure. `404` is **not** here on purpose.

    A 404 from openFDA means "nothing matched", which is an answer and never a failure — so it must
    never reach a function whose job is to describe outages.
    """
    if status == 401 or status == 403:
        return "the service rejected this server's credentials"
    if status == 429:
        return "the service is rate-limiting requests"
    if 500 <= status < 600:
        return "the service is temporarily unavailable"
    return f"the service refused the request (HTTP {status})"
```

This is worth dwelling on, because it is a small idea doing real work. The function *could* have a
404 branch returning something careful. Instead it has none — so any future code path that lets a
404 reach it produces the visibly wrong sentence *"the service refused the request (HTTP 404)"*
rather than a plausible one. **A missing branch that fails loudly beats a present branch that
hedges**, and the docstring tells the next reader why the gap is load-bearing rather than an
oversight.

Its sibling `describe_transport_error()` does the same job for exceptions — timeout, connect
failure, pool exhaustion — mapping each to one clause, with a neutral fallback because *a
stack-trace fragment in an answer would be worse than being vague*.

### 3. `unavailable` is a field, not an exception — and it is written as an instruction

Every result model carries `unavailable: str | None`. Nothing in `live/` raises into the agent loop.
And the string is not an error message; it is **text that lands in the model's context, immediately
before the model decides whether to answer** — so it is phrased accordingly:

```python
UNAVAILABLE_TEMPLATE = (
    "The FDA drug database could not be checked ({reason}). You have seen no FDA labelling or "
    "recall data for this question. Say plainly that you could not check it; do not report an "
    "absence of warnings or recalls, because nothing was looked up."
)
```

That last clause is the one that matters. Without it, a model told "the FDA could not be checked"
will still occasionally reach for its own weights and report that a drug has no recalls.

### 4. Distinct states get distinct fields, never one overloaded flag

The states a reader must be able to tell apart are modelled apart. `DrugLabelResult` has three:
`unavailable` set (nothing looked up), `label_found=False` (no such label), and `label_found=True`
with empty `sections` (the label exists and does not carry that section — routine for
over-the-counter drugs). `ProviderResult` has four, adding `invalid` for a malformed NPI, because
*"your number is wrong"* is a fact about the input and materially more useful than *"not found"*.
`PlanMatches` has four, including `state_not_served`.

Every one of those fields is documented **for the model**, in the second person, saying what to do:

> **Empty with `label_found=True` means the label genuinely does not carry this section** — say
> that, rather than implying the drug has no warnings.

### 5. A negative finding is citable

This is the part that only shows up once the pieces are assembled. The grounding validator rejects a
non-abstained answer carrying no citations, and a citation may only name a row the tools recorded.
So *"the FDA holds no recall for this drug"* — true, useful, exactly what was asked — leaves the
model with two bad moves: abstain (false, it did answer) or cite something it never retrieved.

Measured, it did the second: the agent had CMS's own *"Georgia is not served"* answer, had nothing
to point at, searched the web, and cited a worse source for a fact it already held authoritatively.

So **the search itself becomes the record**:

```python
if not result.recalls:
    return [Row(row_id=result.row_id, view="openfda/drug_enforcement", source="openfda",
                cells={"drug": result.query, "recalls_found": "0",
                       "searched": "FDA enforcement (recall) database"},
                url=result.source_url, title=f"FDA recall search · {result.query} · no matches")]
```

Not a loophole in the grounding rule — the rule applied to a negative claim. The assertion is *"I
looked here and found nothing"*, and that row is precisely the evidence for it.

## Retry versus degrade, concretely

The user-visible question is: *when does the agent get told "you called this wrong, try again", and
when does it get told "there is nothing here, stop asking"?*

**Retry — the model can fix it by calling differently.** All of these raise `ModelRetry`, whose text
enters the conversation as the tool's reply:

```python
if section not in SECTIONS:
    raise ModelRetry(f"section must be one of {list(SECTIONS)}; got {section!r}.")
```

```python
if not rxcuis or not plan_ids:
    raise ModelRetry(
        "check_drug_coverage needs at least one rxcui and one plan_id. Use find_drug to "
        "resolve a drug name first, and find_plans to find plan IDs."
    )
```

```python
raise ModelRetry(
    f"countyfips {countyfips!r} is not one of the counties for ZIP {zipcode}. Choose one "
    f"of: {', '.join(f'{c.name} ({c.fips})' for c in counties)}."
)
```

Each carries the three clauses [tool-retries.md](tool-retries.md) argues for — what was wrong, which
value caused it, **what to send instead**. The section vocabulary is derived from the type via
`get_args(LabelSectionName)`, so the runtime check and the signature cannot drift apart.

**Degrade — retrying cannot help.** These come back as a *result* the model reads, never as a retry:

```python
BUDGET_SPENT = (
    "This run's live-lookup budget is spent ({limit} lookups). Looking up again will not help — "
    "answer from what you have already retrieved, or say what you could not check."
)
```

A spent budget does not refill inside a run, so a `ModelRetry` could only produce the same refusal
while consuming the grounding guardrail's separate allowance. Same for every outage: 429 after the
one permitted `Retry-After` wait, 5xx, timeout, unreadable body, and an expired CMS key. That last
one gets its own wording rather than folding into the generic text, because CMS keys expire every 60
days and it is the one failure an operator can fix in a minute — *if* the message says "the key"
rather than "the API":

```python
_CREDENTIAL_REASON = (
    "this server's Marketplace API key was rejected — it may have expired, as CMS keys do every 60 "
    "days"
)
```

**And the third case — no retry, no degradation, just an answer.** An empty `recalls` list with
`unavailable` unset is not a problem to report. The tool's own docstring says so in the imperative,
because this is where a model's instinct to hedge does damage:

> **An empty result is a real answer, not a failed search.** […] say so plainly and with confidence.
> Do not soften it into "I could not find any", which a reader hears as the search having failed.

## Details that decide whether it actually works

- **The 429 retry lives in a transport, not in a client.** By the time an exception surfaces to
  client code the `Retry-After` header is gone, so the decision is made one layer down where the
  information still exists. One retry, only for 429, only when the server names a wait under the
  configured cap. A 5xx is retried by nobody — it is far more often a real outage than a blip, and
  an honest degradation beats a second round trip while a reader waits.
- **The discarded 429 response is drained before the retry.** It owns a live stream; leaking it
  would exhaust the connection pool over an eval sweep.
- **`Retry-After` parses as seconds *or* an HTTP date** (RFC 9110 permits both), and an unparseable
  value means *do not retry* — guessing a delay for a server that named none is how one request
  holds an SSE stream open for a minute.
- **No base class over the three clients.** Everything genuinely shared is in one small module —
  timeout, transport, `User-Agent`, two describe functions. Three auth mechanisms, three
  empty-result conventions and three envelopes are *not* shared, and a base class declaring all
  three as overridable would be a base class in name only.
- **A live record is cited as a row**, same shape and validator as a vendored one, because it makes
  the same kind of claim — with one addition a mirror row cannot have: a `url` that re-fetches the
  exact record.

## Evidence

- **The inverse rule has a test per upstream.**
  `test_no_recalls_is_an_answer_not_an_outage` (annotated *"the most important test in this file"*),
  `test_no_such_npi_is_an_answer_not_an_outage`, and
  `test_a_state_that_runs_its_own_exchange_is_an_answer` pin all three conventions.
  `test_a_malformed_npi_is_reported_as_a_bad_input` pins NPPES's fourth state.
- **The forward rule is parametrised over the failure table.**
  `test_every_http_failure_degrades_honestly` runs one case per row of
  [structured-api-tools.md §13a](../structured-api-tools.md); none raises and none is a 404.
  Timeouts, connection failures and unreadable bodies each have their own test.
- **The wording is asserted, not just the flag.** `test_an_unavailable_result_never_claims_an_absence`
  checks the text a model would read — because this string lands in the context immediately before
  the model decides whether to answer.
- **The retry/degrade split is pinned in both directions.**
  `test_a_spent_budget_is_terminal_and_not_a_retry` and
  `test_an_unsupported_section_is_a_retry_naming_the_alternatives`, plus
  `test_every_offered_section_is_one_the_client_can_resolve`, which parametrises over the
  `Literal` so an offered vocabulary word that no client can resolve fails the build.
- **Measured end to end:** the Phase 3 gold slice includes `live-02` (*"has atorvastatin been
  recalled?"*) specifically to grade whether an empty-or-negative result reaches the reader as a
  confident finding, and the phase's `lane_detail_correct` metric read **6/6**.
- **What is *not* claimed — and the bug that proves the limit.** A fixture suite asserts response
  *shape*, and cannot assert that the request was correct: a `MockTransport` returns its canned body
  no matter what nonsense it is asked. openFDA writes disjunction as `+OR+`, where `+` is an encoded
  space; writing a literal `+` re-encodes to `%2B` and produces a term that matches nothing. That
  shipped a **false "no recalls" on a drug with 44 of them** — on the one endpoint whose entire
  premise is that an empty result is trustworthy — and was caught only by comparing against a live
  call. `test_the_recall_query_is_a_valid_disjunction` now inspects the outgoing request rather than
  the canned reply. The honest statement is that offline tests pin the *handling* of every edge case
  and only one test pins the *asking*.
- **A known residual gap**, recorded rather than papered over: three negative findings still emit no
  citable row — `drug_label` when no label matches, `drug_label` when the label lacks the requested
  section, and `find_plans` when the search returns nothing. See
  [negative-finding-gaps.md](../negative-finding-gaps.md).

## Why it presents well

The interesting content is not "we handled errors". It is that **the same HTTP status means opposite
things at two different endpoints**, so correctness could not live in a shared abstraction — it had
to live in three deliberate, separately-argued mappings sitting on top of a very small shared base.
Resisting the base class is the judgement call, and the module docstring argues it explicitly rather
than leaving it as an absence.

The second thing worth pointing at is the three-way taxonomy. Most agent codebases have two buckets:
*it worked* and *it failed*. This lane needs a third — *it worked, and the answer is nothing* — and
that bucket is invisible until you notice that the honest response to *"has this drug been
recalled?"* arrives dressed as an HTTP 404. Everything else follows from taking that seriously: the
`unavailable` field, the missing 404 branch in `describe_status`, the per-state fields, and the row
that makes an absence citable.

And the failure mode it defends against is the one nobody catches in review: no exception, no red
test, just a confident answer that quietly reports an outage as a fact about the world.

Full design rationale: [structured-api-tools.md §9, §10, §13a](../structured-api-tools.md) ·
[§14a-bis](../structured-api-tools.md) · [§18c](../structured-api-tools.md) ·
[web_search_tool.md §8](../web_search_tool.md) (the rule this inverts).
