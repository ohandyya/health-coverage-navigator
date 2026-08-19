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

**B. An output validator rejects any answer whose provenance does not hold up.**

Registered on the agent, so it runs on **every** candidate answer before anything is served
([`agent/runtime.py`](../../src/health_coverage_navigator/agent/runtime.py)):

```python
agent.output_validator(_validate_grounding)
```

It refuses four things, each raising `ModelRetry` — which hands the model the reason and lets it
try again:

| Rejected | Why it matters |
|---|---|
| a `chunk_id` no tool returned this run | a fabricated source: the worst failure this tool has |
| a `snippet` not verbatim in that chunk | a real source with words put in its mouth — **worse**, because it reads as more trustworthy |
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

**C. Citations are then rebuilt from the corpus.** The model contributes exactly two things —
*which* chunk and *which words* — and both are checked. Title, URL, `doc_id` and `source_type` are
read off the real `Chunk`, so **an invented title has no path to the browser.**

## Details that decide whether it actually works

- **Snippets compare whitespace-normalised.** The corpora wrap mid-sentence, so a byte-exact test
  would reject genuinely verbatim quotations and trap the model in a retry loop it cannot win.
- **Retries are a bounded budget** (`agent.retries: 2`). Exhausting it raises rather than serving —
  a model that will not ground its answer must fail loudly, not degrade quietly.
- **The prompt was written *not* to duplicate any of this.** Whatever a guardrail can enforce, the
  guardrail enforces; the prompt spends its words on what only the model can do — which tool to
  reach for, when the evidence is enough, when to decline.
- **`claims` are derived, not requested.** The contract requires each claim's text to be a verbatim
  substring of the answer, and a model reproducing its own prose character-for-character is a coin
  flip. Splitting on the citation markers is exact by construction.

## The property that made it extensible

When semantic search was added in Phase 1b, **the guardrail needed no changes at all.**
`vector_search` resolves LanceDB's ids back through the same `CorpusIndex` the lexical tools use,
so a semantic hit reaches `seen_chunks` by the identical path and is citable on identical terms.

That is not a happy accident — it is the failure the design was checked against. Returning store
rows directly would have meant `remember()` silently skipping ids it could not resolve, after which
every vector citation would fail grounding, and the error would surface *two layers away* looking
like a model problem. The mechanism is only cheap to extend because the extension point is
"resolve to a real `Chunk`", not "be a particular retriever".

## Evidence

- **`groundedness` and `citation_resolution` read 1.000 on every agent run ever recorded.** They
  are measured anyway, and the reason is worth stating: *a number below 1.0 would be a bug in the
  validator, not a score to improve.* A guardrail nobody checks is one that has already stopped
  working.
- Guardrail tests are written as **"what would a model do wrong"** — citing an unretrieved chunk,
  paraphrasing a quotation, leaving a dangling marker, answering with no sources — and they assert
  the retry **message**, not just the rejection. A retry the model cannot act on is a retry wasted.
- `make smoke-abstain` runs the whole path live against an out-of-corpus question, where an invented
  citation would be the most damaging possible output.

## Why it presents well

It converts a **probabilistic worry into a structural property**, and it does so in about forty
lines. The claim is not "the model rarely hallucinates citations" — it is "an ungrounded answer
cannot be served, and here is the code path that makes that true." It also demonstrates the
distinction worth having an opinion about: *prompts express intent; code enforces invariants*, and
knowing which to reach for is most of the craft in building on top of an LLM.

Full design rationale: [agent.md §4](../agent.md).
