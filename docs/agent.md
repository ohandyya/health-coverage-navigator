# The agent

The Phase 1a agent: one PydanticAI `Agent` over the reference corpus, with a small full-text
toolset and **no database of any kind**. Written alongside the code rather than before it, unlike
[chunking.md](chunking.md) and [lancedb.md](lancedb.md) — the decisions here needed a running loop
to make.

Code: `src/health_coverage_navigator/agent/`. Ask it something with `make dev`; measure it with
`make eval`.

---

## 1. Shape

```
question ──▶ Agent ──▶ tools ──▶ CorpusIndex ──▶ BM25 over chunks.jsonl
               │                      │
               │                      └─ every returned chunk lands in deps.seen_chunks
               ▼
          AgentAnswer ──▶ output validator ──▶ ChatResponse
       (abstained,          (rejects an          (citations rebuilt
        answer, citations)   ungrounded answer)   from the real chunks)
```

| module | holds |
|---|---|
| `bm25.py` | the ranking formula and an inverted index. Standard library only |
| `index.py` | `CorpusIndex` — the chunks and the four search primitives. No `pydantic_ai` import |
| `models.py` | what the *model* sees: `ChunkHit` in, `AgentAnswer` out |
| `prompt.py` | the system prompt |
| `tools.py` | the four tools, plus `AnswerDeps` — the run-scoped trace and citable set |
| `runtime.py` | `build_agent()`, the grounding validator, `stream_answer()`, `answer_question()` |

`index.py` sits below `tools.py` deliberately: retrieval is then testable and tunable with no
model, no key and no network, which is what makes `make eval-retrieval` possible.

## 2. Four tools, not one `retrieve()`

| tool | for |
|---|---|
| `search_corpus(query, k, source?)` | ranked lexical retrieval. The general-purpose one |
| `grep_corpus(pattern, source?, ...)` | an exact string: an NCD number, a statutory phrase |
| `get_chunk(chunk_id, before, after)` | widen a hit that landed mid-definition |
| `list_documents(source?, limit)` | what is in the corpus at all |

A single `retrieve(query)` would hide the search strategy inside a ranking function, which is the
part of the exercise worth doing. With narrow tools a bad query and the recovery from it are both
visible in the trace, and Phase 2's lane routing becomes a change of degree — the agent is already
choosing between tools before a second lane exists. The cost is accepted: more surface to get
wrong.

**Each tool docstring is a prompt.** The model reads it as the tool description, which is why they
are longer than an ordinary internal docstring, name their siblings, and say what a bad result
means. `search_corpus`'s says outright that matching is on words and not meaning, and to query with
the terms the *source* would use — see §5 for the measurement behind that.

Two ceilings, both in `index.py`: `MAX_HITS = 10` (each hit can be 1,200 characters, so an
unbounded `k` is a context-window problem before it is a latency one) and
`MAX_PATTERN_CHARS = 200` (an arbitrary regex over 6,722 chunks, with no timeout available in
Python's engine — a length cap does not make catastrophic backtracking impossible, it removes the
room to construct one by accident).

## 3. The grounding guardrail is code

The prompt asks for grounded answers. `runtime._validate_grounding` is what makes them grounded. It
runs on every candidate answer and raises `ModelRetry` — handing the model the reason — when:

| rejected | why it matters |
|---|---|
| a `chunk_id` no tool returned this run | a fabricated source: the worst failure this tool has |
| a `snippet` not verbatim in that chunk | a real source with words put in its mouth — *worse*, because it reads as more trustworthy |
| a `[cN]` marker with no matching citation | `ChatResponse` would reject it with a 500 |
| an answer with no citations and `abstained=false` | an assertion with nothing behind it |

The first is mechanical rather than requested: `tools.AnswerDeps.seen_chunks` records every chunk
any tool returns, and nothing outside that set is citable. There is no wording the model can choose
that gets around it.

The last two duplicate checks `ChatResponse._check_provenance` already makes. Doing them one layer
earlier turns a served 500 into a retry the model can act on. `agent.retries` (2, in `config.yaml`)
is that budget; exhausting it raises `UnexpectedModelBehavior`, which is correct — a model that
will not ground its answer must fail loudly rather than serve an ungrounded one.

Snippets are compared **whitespace-normalized**. The corpora wrap mid-sentence, so a byte-exact
test would reject genuine quotations and send the model into a retry loop it cannot win.

**Citations are then rebuilt from the corpus.** The model contributes `chunk_id` and `snippet`,
both checked; title, url, `doc_id` and `source_type` are read off the real `Chunk`. No invented
title can reach the browser.

**`claims` are derived, not asked for.** `AnswerClaim.text` must be a verbatim substring of the
answer, and a model reproducing its own prose character-for-character is a coin flip that fails the
contract validator when it loses. `_claims()` splits on the markers instead, which is exact by
construction.

## 4. Loop safety, from day one

`config.yaml`'s `agent:` block carries `request_limit: 8` and `tool_calls_limit: 12`, applied as
`UsageLimits` on every run. Without them, a model that keeps reformulating a query it will never
satisfy runs until the request times out, and the reader watches a spinner instead of getting an
answer or an honest abstention.

Phase 4 adds cycle detection and a hop ceiling **on top of** these rather than introducing the
idea — cheap now, painful to retrofit.

## 5. What the numbers actually say

Measured against the 30 in-corpus gold questions with `make eval-retrieval` (free, instant, no
model):

| | recall@5 | MRR |
|---|---|---|
| BM25 over `retrieval_text` (shipped) | **0.567** | 0.416 |
| BM25 over bare `text` | 0.500 | 0.371 |

**The context header earns its place.** [chunking.md](chunking.md) §6 asserted that the breadcrumb
(`NCD 30.3 > Acupuncture > Indications...`) is legitimate lexical signal; it is, at every `k1`/`b`
cell swept, and `tests/test_corpus_index.py` pins it so a future "simplify: index `text`" has to
argue with a number.

**`bm25_b` was a false alarm.** chunking.md §5 handed forward the worry that length normalization
would over-favour the 505 NCD chunks under 200 characters. Sweeping `b` from 0.0 to 1.0 moves
recall@5 by at most one question at any `k1`. That question is closed; the short NCD sections are
fine.

**`bm25_k1: 2.0`, but read it as "no evidence 1.2 is better here".** 2.0 won at every `k` on both
metrics, by two questions out of thirty — which a set this size cannot resolve. Do not chase
further decimals on this set.

**The raw-question baseline is mediocre on purpose.** Feeding the verbatim gold question to BM25
scores 0.567, and the misses are vocabulary gaps: *"what exactly is a deductible?"* is dominated by
the rare word *exactly*, not by *deductible*. That is not a bug to fix in the ranking function — it
is the reason the agent gets a toolset and reformulates. Observed on a live run, the agent turns
that question into `search_corpus(query='deductible definition what you pay before plan pays')` and
lands the glossary entry first. It is also the honest baseline Phase 1b's vector search has to
beat.

## 6. Streaming a structured output

`answer_question()` is `stream_answer()` drained to its `done` event. One code path, two shapes.
Phase 0 had a test asserting the two endpoints returned identical objects; a nondeterministic model
makes that unassertable by re-running, so the property moved into the code.

The answer streams even though the output is a structured object rather than text. The model emits
that object as JSON fragments; `_partial_answer()` parses a prefix of the buffer with
`from_json(allow_partial=...)`, reads `answer`, and emits the **diff** against what was already
sent. Which streamed part is the output is identified **by shape** — a buffer that parses to an
object with a string `answer`, and none of the four tools takes an argument by that name. The
alternative was tracking part indices across `FinalResultEvent`, which differs between
tool-output, native-output and prompted-output modes and between providers.

A final flush covers the rest: a provider that sends the output in one piece, or a retry whose
accepted answer shares no prefix with the rejected draft. `done` is authoritative either way, but
the UI shows accumulated tokens until then, so a silent gap reads as a truncated answer.

Trace steps come from `deps.trace`, not from the framework's tool events, because the tools record
the arguments, the real duration, and a summary of what came back — none of which the event stream
carries.

## 7. Two things that will bite

**`Agent("openai:...")` does not see `Secrets`.** PydanticAI reads `OPENAI_API_KEY` from the
process environment and `uv run` does not load `.env`, so the obvious wiring raises `UserError`
even though the settings object holds the key. `_resolve_model` passes
`OpenAIProvider(api_key=...)` explicitly — also the better shape, since the credential flows
through one audited call instead of ambient global state.

**The `gpt-5.6-*` family needs the Responses API, and `_resolve_model` has to track that even
though it constructs the model class by hand.** PydanticAI's own inference of a bare `openai:`
string already resolves to `OpenAIResponsesModel` (only `openai-chat:` gets `OpenAIChatModel`) —
`_resolve_model` exists for the credential, above, not to pick a different endpoint. The first
draft built `OpenAIChatModel` explicitly on an unchecked assumption, and that combination is a hard
400 on the first tool call: *"Function tools with reasoning_effort are not supported ... in
/v1/chat/completions. To use function tools, use /v1/responses"*. An agent with no tools is not
this project, so the explicit construction has to match what inference would have picked.

## 8. Testing it without a provider

`tests/conftest.py` sets `pydantic_ai.models.ALLOW_MODEL_REQUESTS = False` for the whole suite, so
`make check-all` cannot spend money, cannot need a key, and cannot fail because a provider is
having a bad afternoon.

The `agent_kit` fixture supplies a two-chunk corpus and a scripted `FunctionModel`. It provides
**both** a `function` and a `stream_function`, because the runtime only ever streams — and the
streaming half emits arguments in 17-character JSON fragments, deliberately aligned to nothing, so
they split mid-key and mid-string. That is what exercises `_partial_answer`; a single-chunk script
would leave the whole streaming path untested while appearing to pass.

Guardrail tests are written as *what would a model do wrong*, and assert the retry **message** as
well as the rejection: a retry the model cannot act on is a retry wasted.

**One thing that suite structurally cannot check, so `make smoke` does.** `_partial_answer` parses
the output as a **real provider fragments it**, and `FunctionModel` can only approximate that. If
OpenAI changes how `/v1/responses` chops output deltas, every test stays green, the final flush
still delivers a correct answer in one lump, and incremental streaming is silently dead — a long
pause then a wall of text. `make smoke` makes one live call and fails when a run produces a single
token event. It stays out of pytest deliberately: a failing test means this repo is wrong, a failing
smoke check might mean the provider changed, and one command that means either teaches you to shrug
at red. `make smoke-abstain` runs the same checks over an out-of-corpus question, where an invented
citation would be the worst failure this tool has.

## 9. Grading

Three tiers, split by cost, which is why the graders are a list the caller composes rather than a
fixed pipeline:

| | runs | measures |
|---|---|---|
| `make eval-retrieval` | free, instant, no key | retrieval alone — the `bm25_b`/`k1` sweep loop |
| `make eval` | 35 model calls | the whole phase, plus deterministic groundedness |
| `make eval-judge` | +35 model calls | answer correctness, per key fact |

`make smoke` (§8) sits below all of these: one call, and it asks whether the live path works at all
rather than how well it answers.

`groundedness` and `citation_resolution` should be **1.000 on every agent run**. The output
validator rejects either failure before the response is built, so a number below 1.0 is a bug in
that validator, not a score to improve. It is measured anyway, because a guardrail nobody checks is
a guardrail that has already stopped working.

`key_fact_coverage` is lexical overlap and is named *coverage* rather than *correctness* on
purpose: it scores an answer and its exact negation identically. The judge produces the correctness
number, grades one key fact at a time against the gold set's `answer_key_facts`, and runs on
`evals.judge_model` — **a different model from the one it grades**, because a judge marking its own
homework agrees with itself most confidently exactly where both are wrong.

The judge is deliberately unreachable from the UI. A button that spends money on every click is the
wrong affordance.
