# Hallucinated citations are made structurally impossible, not discouraged

One of the [technical highlights](../technical_highlights.md).

## The problem

An LLM asked to answer with citations will, some fraction of the time, cite a document it never
read, or attach real-looking quotation marks to words the source never said. For a health-coverage
tool this is the **worst failure available** — worse than a wrong answer, because a fabricated
citation is what makes a wrong answer *credible*. Someone makes a financial or medical decision on
it.

The usual mitigation is to ask nicely: *"only cite passages you retrieved."* That is not a
guarantee, it is a preference, and it fails silently and unobservably.

## The approach: two mechanisms that meet in the middle

**A. The citable set is recorded by the tools, not declared by the model.**

Every tool routes its results through one method before returning them
([`agent/tools.py`](../../src/health_coverage_navigator/agent/tools.py)):

```python
def remember(self, hits: list[ChunkHit]) -> list[ChunkHit]:
    """Mark hits as citable. Returns them unchanged, so it can wrap a return value."""
    for hit in hits:
        chunk = self.index.chunk(hit.chunk_id)
        if chunk is not None:
            self.seen_chunks[chunk.id] = chunk
    return hits
```

`seen_chunks` is run-scoped state on the dependency object. It accumulates *what the agent has
actually been shown* — a fact about the run, produced as a side effect of retrieval, with no input
from the model. Returning `hits` unchanged is what lets it wrap a return value, so a tool cannot
accidentally forget to call it.

There are **three such sets now, one per kind of evidence** — `seen_chunks` for passages,
`seen_rows` for queried rows (Phase 1-c), `seen_results` for web results (Phase 2) — and each is
filled the same way, by the tool that produced the evidence rather than by the model that cites it.

They differ in one respect that the web lane forces. `seen_chunks` stores chunks it *resolved
through the corpus index*, because a chunk id can always be re-read from `chunks.jsonl` later. **A
web result can not.** Once the run ends, the page Tavily extracted exists nowhere on this machine —
so `remember_results` records the `WebResult` **whole**, text included, because the validator has to
hold the words it will check a quotation against. The citable set is not a list of identifiers here;
it is the evidence itself.

**B. An output validator rejects any answer whose provenance does not hold up.**

Registered on the agent, so it runs on **every** candidate answer before anything is served
([`agent/runtime.py`](../../src/health_coverage_navigator/agent/runtime.py)):

```python
agent.output_validator(_validate_grounding)
```

It refuses five things, each raising `ModelRetry` — which hands the model the reason and lets it
try again:

| Rejected | Why it matters |
|---|---|
| a `chunk_id` / `row_id` / `result_id` no tool returned this run | a fabricated source: the worst failure this tool has |
| a `snippet` not verbatim in that chunk or web result | a real source with words put in its mouth — **worse**, because it reads as more trustworthy |
| a cell value not byte-identical to what the query returned | the same failure one lane over, where `'$4,500 '` and `'$4,500'` are different claims |
| a `[cN]` marker with no matching citation | a dangling reference the contract would reject with a 500 |
| an answer with no citations, not marked as an abstention | an assertion with nothing behind it |

The first check is the one that closes the loop:

```python
chunk = ctx.deps.seen_chunks.get(citation.chunk_id)
if chunk is None:
    raise ModelRetry(
        f"Citation {citation.id} names chunk_id {citation.chunk_id!r}, which no tool "
        f"returned in this conversation. ..."
    )
```

**There is no wording the model can choose that gets around this.** The set was assembled by code
that ran before the model spoke.

**C. Citations are then rebuilt from the evidence.** The model contributes exactly two things —
*which* piece of evidence and *which words* — and both are checked. Title, URL, `doc_id` and
`source_type` are read off the real `Chunk`, `Row` or `WebResult`, so **an invented title has no
path to the browser.**

## Details that decide whether it actually works

- **Two comparison rules, and they disagree on purpose.** A passage or web snippet compares
  **whitespace-normalised**: prose wraps and re-wraps, so a byte-exact test would reject genuinely
  verbatim quotations and trap the model in a retry loop it cannot win. A table cell compares
  **byte-exact**: it has no wrapping to survive, and its whitespace is data — `'$4,500 '` carries a
  trailing space that distinguishes the published value from a tidied one. Each rule is right about
  its own evidence, and applying either everywhere would be wrong somewhere.
- **Retries are a bounded budget** (`agent.retries: 2`). Exhausting it raises rather than serving —
  a model that will not ground its answer must fail loudly, not degrade quietly.
- **The prompt was written *not* to duplicate any of this.** Whatever a guardrail can enforce, the
  guardrail enforces; the prompt spends its words on what only the model can do — which tool to
  reach for, when the evidence is enough, when to decline.
- **`claims` are derived, not requested.** The contract requires each claim's text to be a verbatim
  substring of the answer, and a model reproducing its own prose character-for-character is a coin
  flip. Splitting on the citation markers is exact by construction.

## What three extensions did to it

**Phase 1b — free.** When semantic search arrived, the guardrail needed **no changes at all**.
`vector_search` resolves LanceDB's ids back through the same `CorpusIndex` the lexical tools use, so
a semantic hit reaches `seen_chunks` by the identical path and is citable on identical terms.

That is not a happy accident — it is the failure the design was checked against. Returning store
rows directly would have meant `remember()` silently skipping ids it could not resolve, after which
every vector citation would fail grounding, and the error would surface *two layers away* looking
like a model problem. The extension was cheap because the extension point is "resolve to a real
`Chunk`", not "be a particular retriever".

**Phases 1-c and 2 — not free, and the honest version is more interesting.** A row has no chunk and
a web page has no chunk, so "resolve to a real `Chunk`" is exactly the assumption they break. Each
needed its own citable set, its own validator branch, and — for rows — its own comparison rule. What
*did* survive untouched is the shape: evidence recorded by the tool that produced it, checked
against a citation before anything is served, and the served object rebuilt from the evidence rather
than from the model. **The mechanism generalised; the implementation grew.** A design that claims to
cost nothing on every extension is usually a design nobody has extended.

Phase 2 also charged a small, specific price worth recording, because it is the kind of thing that
looks like a nit and is not. `AgentCitation` carries all three shapes in one flat class (a
discriminated union would put an `anyOf` in the output schema, which strict-JSON modes handle
unevenly). Its shape check detected a passage as `chunk_id is not None or snippet is not None` — and
a **web** citation also carries a `snippet`, so adding the third shape naively made every web
citation read as a malformed passage. The fix was to discriminate on the *id field alone*. Found by
reading rather than by running, but only because someone went looking: nothing about adding a field
announces that an existing predicate has quietly stopped meaning what it says.

## Where the mechanism does the most work: the web lane

Across the first two lanes the guardrail defends against a failure that is at least *visible*. An
invented `chunk_id` (`healthcare_gov:glossary_deductible#000`) or an invented `row_id`
(`exchange_puf/2026/plan_attributes#q1.1`) is self-evidently internal machinery — a reader who saw
one would know something was wrong, and a grader can check it against the corpus after the fact.

**A URL is different, and this is the argument for the whole approach.** A model can write
`https://www.cms.gov/newsroom/press-releases/2026-open-enrollment` — well-formed, plausible,
authoritative-looking, and never retrieved — and *neither a reader nor a post-hoc grader could tell
by looking*. There is no artifact to check it against, because the thing it claims to cite is the
open web.

So a web citation names a `result_id` that only a search can assign, never the URL. The id is
positional — `web#s1.2` is *search 1, result 2* — and that is what makes it unforgeable: **nothing
carries an id of that shape until a search hands one out.** A model cannot reason its way to
`web#s1.2` being valid, the way it can reason that a CMS press-release URL is plausible, because
validity here is not a property of the string. It is membership in a dictionary that code filled
before the model spoke. The URL is then read off the recorded result, exactly as a title is.

**The lane where "do not invent sources" would have been least enforceable by inspection is the lane
where it is enforced by construction** — which is the whole point of preferring a code path to a
prompt instruction.

### The one thing the prompt does say, and why it is not duplication

Everywhere else this design keeps the prompt out of the guardrail's business. The web lane is the
exception, and the distinction is worth being precise about:

```
- a **web result**: its `result_id`, plus a `snippet` copied **exactly** from that result's
  `content`. Cite the `result_id`, never the URL — a URL you did not get back from `web_search` is
  not a source you have, however plausible it looks;
```

That is not the prompt asking the model not to fabricate — the validator already makes fabrication
impossible. It is telling the model **which field to put the identifier in**, which no guardrail can
do: a model that writes a real, retrieved URL into `result_id` has not lied about anything, it has
filled in the wrong box, and the only outcomes available are a wasted retry or a rejected answer.
Guardrails enforce invariants; prompts describe shapes. Confusing the two in either direction is how
this kind of system gets brittle.

### Provenance the reader can act on

A checked citation is worth less if the reader cannot tell *whose page it is*. The citation card
shows a title and hides the URL behind a link, so the web lane's wire citation deliberately builds
the domain — and the publication date, when Tavily returned one — into the title:

```python
dated = f", {result.published_date}" if result.published_date else ""
title = f"{result.title} — {result.domain}{dated}"
```

Both halves come off the recorded result, so this is the same rebuilt-from-evidence rule as
everywhere else rather than an exception to it. The reason it earns its place: for a health
question, *who is saying this* is part of the claim, and a card reading only *"Ebola Outbreak: What
CDC is Doing"* renders a forum post in the same chrome as `cms.gov`. Surfacing the source is also
the deliberate **alternative to filtering the web to an allowlist** — an allowlist would guarantee
an abstention on exactly the questions this lane exists to answer, so the design shows the reader
where a claim came from instead of pretending it vetted it
([web_search_tool.md §7](../web_search_tool.md)).

## Evidence

- **`groundedness` and `citation_resolution` read 1.000 on every agent run ever recorded.** They
  are measured anyway, and the reason is worth stating: *a number below 1.0 would be a bug in the
  validator, not a score to improve.* A guardrail nobody checks is one that has already stopped
  working.
- Guardrail tests are written as **"what would a model do wrong"** — citing an unretrieved chunk,
  paraphrasing a quotation, tidying a cell value, passing off a plausible URL as a `result_id`,
  leaving a dangling marker, answering with no sources — and they assert the retry **message**, not
  just the rejection. A retry the model cannot act on is a retry wasted.
- `make smoke-abstain` runs the whole path live against an out-of-corpus question, where an invented
  citation would be the most damaging possible output. `make smoke-web` does the same against a
  question only the open web can answer, and asserts that every web citation carries a URL a tool
  actually returned.

## Why it presents well

It converts a **probabilistic worry into a structural property**, and it does so in about forty
lines. The claim is not "the model rarely hallucinates citations" — it is "an ungrounded answer
cannot be served, and here is the code path that makes that true." It also demonstrates the
distinction worth having an opinion about: *prompts express intent; code enforces invariants*, and
knowing which to reach for is most of the craft in building on top of an LLM.

Full design rationale: [agent.md §4](../agent.md).
