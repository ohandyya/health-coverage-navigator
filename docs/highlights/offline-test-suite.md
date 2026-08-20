# A test suite that *cannot* spend money — and the guard that had to be repaired to keep it that way

One of the [technical highlights](../technical_highlights.md).

## The problem

This repo runs on a hard invariant: **`make check-all` never reaches a model provider.** It needs no
API key, costs nothing, and cannot fail because a provider is having a bad afternoon. That is what
makes it safe to run constantly, safe to hand to a stranger cloning the repo, and safe to trust when
it is green.

The failure mode being defended against is unusually nasty, and it is worth saying plainly when
presenting this: **a test that accidentally calls a real API does not fail. It passes.** It is
slower, it costs a fraction of a cent, it quietly requires a credential — and every signal you have
says everything is fine. You find out from a bill, or from a CI box that has no key, or never.

## What went wrong, and why it is instructive

Phase 1a enforced the invariant with one line:

```python
monkeypatch.setattr(pydantic_ai.models, "ALLOW_MODEL_REQUESTS", False)
```

Phase 1b added embeddings — and **silently punched a hole straight through it.** That flag is
*PydanticAI's* switch, governing PydanticAI's model requests. The new embedder calls the OpenAI SDK
directly:

```python
client = AsyncOpenAI(api_key=get_secrets().openai_api_key.get_secret_value(), ...)
response = await client.embeddings.create(model=model, input=list(texts))
```

Nothing in that path consults `ALLOW_MODEL_REQUESTS`. The suite would have gone out to the network
on the developer's own key and stayed green.

**The transferable lesson: a safety flag borrowed from a library only covers that library's surface
area.** The invariant was "no test reaches a provider"; the mechanism only ever implemented "no test
reaches a provider *through PydanticAI*". Those were the same sentence right up until they weren't,
and nothing announced the divergence. Any new dependency that can open a socket re-opens this
question.

### The lesson was load-bearing: Phase 2 re-opened it, exactly as predicted

The sentence above ended "any new dependency that can open a socket re-opens this question", and
**Phase 2 added one**: a Tavily search, which leaves through `httpx` and consults neither
`ALLOW_MODEL_REQUESTS` nor the embedder guard. Same hole, third surface. Because the pattern was
already written down it was closed *while the lane was being built* rather than discovered
afterwards — a third autouse fixture refusing the live client factory, patched at our factory for
the same reason as B below.

That is the argument for writing this kind of thing down at all. The Phase 1b hole cost a debugging
session; the Phase 2 one cost a paragraph, because someone had already paid to learn the shape of
it. There are now three guards, and the honest way to state the invariant is: **one guard per
dependency that can open a socket, and adding such a dependency means adding one.**

## The approach: a negative guard plus a positive substitute

**A. The guard — refuse the live factory, suite-wide.**

```python
@pytest.fixture(autouse=True)
def _no_live_embeddings(monkeypatch: pytest.MonkeyPatch) -> None:
    import health_coverage_navigator.vectors.embedder as embedder_module

    def refuse(*_args, **_kwargs):
        raise AssertionError(
            "a test tried to build the live OpenAI embedder. Use the `fake_embedder` fixture; "
            "the suite must not reach a provider."
        )

    monkeypatch.setattr(embedder_module, "openai_embedder", refuse)
```

Patched at **our factory**, not at `AsyncOpenAI`, deliberately: the failure then names the seam the
test should have used, instead of surfacing as an authentication error from somewhere inside a
vendor package. `autouse` so it applies without being asked for, and via `monkeypatch` so it is
restored per test.

**B. The subtlety that makes or breaks it — and that forced a change in production code.**

Patching a module attribute only works if consumers **look the name up at call time**. Two modules
originally did this:

```python
from health_coverage_navigator.vectors.embedder import openai_embedder   # binds at import
```

That copies the reference into the consumer's namespace *before* any fixture runs. The patch would
have replaced a name **nobody used**, the guard would have reported nothing, and the hole would have
stayed open behind a passing suite — a guard that appears to work is worse than no guard. Both
consumers now import the module and resolve through it:

```python
from health_coverage_navigator.vectors import embedder as embedder_module
...
embedder = embedder_module.openai_embedder(config.embedding_model, config.dimensions)
```

`openai_embedder`'s own docstring records the requirement, so the next person does not "tidy" the
import back into the shorter form. `vectors/__main__.py` is a deliberate exception — it is the CLI
whose entire job is to spend that money, and no test imports it.

**C. The substitute — a deterministic fake at the seam we own.**

A guard alone only converts silent spending into loud failure; something has to fill the gap.
`fake_embed` hashes the text into a unit vector:

```python
digest = hashlib.sha256(text.encode("utf-8")).digest()
raw = [(digest[i % len(digest)] / 255.0) - 0.5 for i in range(FAKE_DIM)]
norm = math.sqrt(sum(x * x for x in raw)) or 1.0
out.append([x / norm for x in raw])
```

Its docstring is emphatic about what it is **not**:

> Not semantic, and deliberately not pretending to be. Two paraphrases get unrelated vectors, so
> this can never stand in for a judgement about whether vector search *works*.

That honesty is the point. The fake gives exactly two properties, and they are the only two the
plumbing needs: an exact-text query retrieves its own chunk at similarity 1.0, and ordering is
stable across runs. Retrieval *quality* is a different question, answered by
`make eval-retrieval-vector` against the real model and the real corpus — never by a unit test.

## Where the line between fake and real was drawn

The fake stops at the embedder. **The vector store in the tests is a real LanceDB database**, built
in `tmp_path`, not a stub object.

The reasoning generalises: fake the thing that *costs money or leaves the machine*; keep the thing
you are actually testing. The failure modes worth catching here are LanceDB's own — the fixed-width
Arrow schema, whether `.where()` filters before or after the top-k cut, the name and direction of
the `_distance` column. A hand-written fake `VectorIndex` would assert only that our mock behaves
like our mock, which is a test that can never fail for a real reason.

## Evidence

- **373 tests run with no API key, no network, and no billing**, in about twelve seconds.
- The hole was real and is now closed in all three directions: each guard fails loudly on its live
  factory, and every consumer resolves through the module so the guard actually binds.
- **The web lane took the pattern one step further.** Its guard blocks the live *client factory*,
  but the tests still drive a **real `AsyncTavilyClient`** — over an `httpx.MockTransport`. That was
  the deciding argument for using the vendor SDK rather than hand-rolling the HTTP call: the SDK
  accepts an injected client, so the tests exercise its own status-code-to-exception mapping instead
  of our idea of it. Same principle as the real LanceDB below — fake what leaves the machine, keep
  what you are testing.
- A companion gap surfaced from the same instinct and was fixed alongside it: under the shipped
  configuration the agent reaches for keyword search first and succeeds, so **every default
  `make smoke` run exercised only the lexical path** — `vector_search` could have broken live with
  all nine checks green. `make smoke --toolset vector` now covers it.

## Why it presents well

It is a concrete, slightly uncomfortable story rather than a claim of discipline: *we had an
invariant, we added a feature, and the invariant quietly stopped holding — here is how it was caught
and what now keeps it true.* It carries three ideas worth arguing for:

1. **Safety mechanisms inherit a library's scope, not your intent.** Re-derive them whenever a new
   dependency can reach the network.
2. **Python's import style is a testability decision**, not a formatting preference.
   `from x import y` freezes a reference; `import x` keeps a seam. That difference decided whether
   this guard worked at all.
3. **The most dangerous test failures are the ones that pass.** Anything whose failure mode is a
   green suite deserves a mechanism, not a convention.
