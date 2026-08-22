# A model will call your tools wrongly — so every rejection is written as a correction

One of the [technical highlights](../technical_highlights.md).

## The problem

An agent that composes its own tool calls will get some of them wrong. Not occasionally, and not
because the model is bad — because the surface is genuinely hard. It writes a regular expression
with an unbalanced paren. It passes `topic="finance"` to a search that accepts two topics. It writes
SQL naming a column that does not exist in a table with 151 of them, 36 of which are different
flavours of out-of-pocket maximum. It queries the 2026 table on a question pinned to 2027.

The instinctive treatment is to make these **errors**: let the exception propagate, fail the run,
show the user a 500. That is the wrong shape, and it is wrong in a specific way. A malformed regex
is not a fault in the system — the corpus is fine, the index is fine, the question was answerable.
It is a **first draft**. Failing the run throws away everything the agent had already established in
order to punish a typo.

The equally instinctive alternative is worse: swallow the error and return an empty result. Now the
model is told the corpus does not contain what it asked for, which is false, and it abstains or
searches somewhere useless. A silent failure has become a wrong answer.

## The approach: rejection is a message the model can act on

PydanticAI's `ModelRetry` is the third option. Raising it inside a tool does not fail the run and
does not return a result — it puts **the exception's text into the conversation** as the tool's
reply and lets the model try again. So the question stops being *"did the call succeed"* and becomes
*"what does this tool say to a model that got it wrong"*.

Which makes the message the actual engineering. Every `ModelRetry` in this repo is written to carry
three things:

| | | why |
|---|---|---|
| **what was wrong** | *"is not a valid regular expression: missing ), unterminated subpattern"* | without it the model guesses at the failure and often guesses the input was simply not found |
| **which value caused it** | the pattern, the column, the topic — echoed back | a model that made three calls needs to know which one is being rejected |
| **what to do instead** | the valid set, the right table name, or a different tool | the difference between a retry the model can act on and one it can only repeat |

The third is the one that gets skipped, and skipping it is how a retry budget gets spent producing
the same call three times.

## Examples, from smallest to most interesting

**1. A malformed regex — and a nudge toward the right tool.**

```python
except re.error as exc:
    raise ModelRetry(
        f"{pattern!r} is not a valid regular expression: {exc}. Escape any literal "
        f"metacharacters, or use search_corpus if you meant a plain-language query."
    ) from exc
```

The model sees `'(unclosed' is not a valid regular expression: missing ), unterminated subpattern
at position 0. Escape any literal metacharacters, or use search_corpus if you meant a
plain-language query.` The second sentence matters more than the first: an unbalanced paren usually
means the model reached for `grep_corpus` when it wanted a search, so the correction offered is
**a different tool**, not a fixed pattern.

**2. An out-of-vocabulary argument — and why the signature is deliberately loose.**

```python
if topic not in TOPICS:
    raise ModelRetry(f"topic must be one of {list(TOPICS)}; got {topic!r}.")
```

`web_search`'s signature takes `topic: str`, not `Literal["general", "news"]` — which looks like a
weaker type and is a deliberate choice. **This is a boundary a model writes to.** A value outside
the vocabulary rejected by the output schema gives the model a validation error from a layer it
cannot see; rejected here it gets a sentence naming the two values it may use. The vocabulary is
derived from the type (`get_args(WebTopic)`) so the check and the signature cannot drift apart.

**3. A rejected SQL query — where the best message is one you did not write.**

The relational lane hands the model DuckDB's own binder error, verbatim:

```python
except QueryRejected as exc:
    raise ModelRetry(str(exc)) from exc
```

Asked for a column that does not exist, DuckDB replies with *"Candidate bindings:
TEHBDedInnTier1Individual, …"* — a ranked list of near-miss column names out of 151. **No message
written by hand here would be as useful**, because the database knows the schema and the near-misses
and this code does not. Knowing when *not* to write your own error text is part of the technique.

**4. A query against the wrong plan year — a correction, not a refusal.**

```python
raise QueryRejected(
    f"{table.view} holds {table.source} data for {table.partition}, but this question "
    f"is pinned to plan year {plan_year}. {correction}."
)
```

Where `correction` is `use exchange_puf.plan_attributes_2026` — the exact view name to write
instead. This is the difference the whole highlight turns on. *"Wrong year"* is a rejection.
*"Wrong year, here is the right table"* is a retry that succeeds. And it is what makes `plan_year` a
rule rather than a suggestion: without it, the model answers from whichever year it happened to
query, and a number from the wrong year reads exactly as authoritative as one from the right year.

**5. A query that ran too long — pointing at the fix, not the symptom.**

```python
raise QueryRejected(
    f"the query was still running after {self._config.query_timeout_s:g}s and was "
    f"cancelled. Add a filter on an indexed identifier, or aggregate rather than "
    f"scanning the whole table."
)
```

**6. An ungrounded citation — the valid set, supplied.**

The [grounding validator](grounded-citations.md) is the same mechanism at the output boundary rather
than the tool boundary, and its messages follow the same rule — including handing back the set the
model should have chosen from:

```python
raise ModelRetry(
    f"Citation {citation.id} names chunk_id {citation.chunk_id!r}, which no tool "
    f"returned in this conversation. You may only cite passages you retrieved. "
    f"Chunks you have seen: {known or 'none — search first'}."
)
```

`none — search first` is the case worth noticing: a model that cited before retrieving is told the
*next action*, not just the violation.

## Knowing when a rejection is not a retry

This is the half that makes it a design rather than a habit. `ModelRetry` is right when **the model
can fix the problem by acting differently**. When it cannot, a retry is worse than useless: it burns
a bounded budget and a model call to produce the same failure.

So three things in this repo that look retryable are deliberately not:

- **A spent search budget.** When a run has used its `max_searches_per_run`, `web_search` returns a
  *result* whose `unavailable` field explains that the budget is gone — it does not raise. The
  budget will not refill inside this run, so a retry could only produce the same refusal while
  spending the grounding guardrail's separate allowance on it.
- **A search-service outage.** Every Tavily failure — 429, timeout, 5xx, unreadable body — becomes
  the same `unavailable` result. The model cannot fix a network condition, and `ModelRetry` would
  invite it to try. What it *can* do is tell the reader the web could not be checked, which is what
  the message instructs.
- **A missing credential or an unbuilt store.** These are deployment facts, so they surface as a
  startup error or a 503 naming the fix, long before a model is involved.

The rule underneath: **retry what the model got wrong; degrade what the world got wrong.**

## Details that decide whether it actually works

- **The budget is bounded and exhaustion is loud.** `agent.retries: 2` on the output validator; a
  third failure raises `UnexpectedModelBehavior` rather than serving. A model that will not ground
  its answer must fail visibly, not degrade quietly — and this repo has the receipts for what that
  looks like: one recorded run lost 17 of 35 eval questions to `Exceeded maximum output retries`.
  It has not recurred, and it is written down as a shape to recognise rather than a diagnosis.
- **Retries cost model calls, and the ceilings know it.** `request_limit: 12` is sized to include
  the retry budget rather than in ignorance of it, so a run that retries twice still has room to
  finish.
- **A rejected attempt stays in the trace.** `query_structured` records the rejection *before*
  raising, so the user-facing trace shows `rejected: … Nonexistent …` and then the successful
  retry. A rejected query that vanished would hide the most interesting thing the agent did — and
  a test asserts the trace entry survives, not just the recovery.
- **Tool descriptions do the work retries should not have to.** `describe_table` reports example
  values and per-table correctness notes; `web_search`'s description says when *not* to call it. A
  retry is a second chance, and the cheapest retry is the one that never happens.

## Evidence

- **Recovery is tested per tool, not assumed.** `test_a_malformed_regex_is_a_retry_not_a_crash`,
  `test_a_rejected_query_is_a_retry_not_a_crash`, `test_an_unsupported_topic_is_a_retry` and
  `test_an_unsupported_time_range_is_a_retry` each script a wrong call followed by a good one, and
  assert the run **produces a cited answer** — the recovery, not merely the rejection.
- **Guardrail tests assert the message, not just the failure.** Written as *"what would a model do
  wrong"* and checked for the text the model would read back, on the principle that a retry the
  model cannot act on is a retry wasted.
- **`test_the_search_budget_is_enforced_and_is_not_a_retry`** pins the negative case, so the
  distinction in the section above cannot quietly erode into "retry everything".
- **What is *not* claimed:** there is no A/B here measuring answers-with-retries against
  answers-without. It would need a build with the mechanism removed, and the mechanism is load-
  bearing for the grounding guarantee, so the honest statement is that recovery is demonstrated
  per-tool and the failure mode when the budget is exhausted is recorded — not that a number
  attributable to `ModelRetry` exists.

## Why it presents well

It is a small idea that changes the shape of the system: **a wrong tool call is an expected input,
not an error path.** Most of the cost is in writing error messages for an unusual reader — one that
will act on the text immediately and cannot see your stack trace, your schema, or your code. Getting
that right is a distinct skill from writing errors for humans, and the tell that someone has thought
about it is the third clause: not just *what broke* and *what you sent*, but *what to send instead*.

The other half is knowing where the mechanism stops. A retry is a bet that the model can do better;
where that bet is unwinnable — a spent budget, an outage, a missing key — the honest move is to
degrade and say so. Deciding which of those a given failure is, is the actual design work.

Full design rationale: [agent.md §4](../agent.md) ·
[relational-tool.md §6](../relational-tool.md) · [web_search_tool.md §3](../web_search_tool.md).
