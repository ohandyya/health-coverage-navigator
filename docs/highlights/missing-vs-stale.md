# "Missing" and "wrong" are different failures, and get opposite treatment

One of the [technical highlights](../technical_highlights.md).

## The problem

The vector store is a **derived artifact**: 40 MB of float32 computed from the committed corpus by a
paid API call, and therefore git-ignored. Every derived artifact can fail in two ways, and
collapsing them into one error type is a mistake that costs you exactly when it matters:

| | **Missing** | **Stale** |
|---|---|---|
| What happened | never built here | built against *different* chunks or a different model |
| How it presents | nothing works, loudly | **everything works, plausibly** |
| Is it expected? | yes — fresh clone, and building costs money | no |
| Cost of getting it wrong | an obvious error message | citations pointing at passages that no longer exist |

The second row is the whole argument. A missing store is *visibly* unbuilt — you cannot fail to
notice. A stale store answers every question with confident prose and real-looking citations that
resolve to the wrong text, or to nothing.

**The severity of a failure is not how broken the system is. It is how detectable the wrongness
is.** A system that is obviously down is safer than one that is quietly wrong, and for a
health-coverage tool a quietly wrong citation is the failure the entire
[grounding design](grounded-citations.md) exists to prevent — so it must not be reintroduced one
layer lower, in the storage.

## Why staleness is even possible

Retrieval returns `chunk_id`s, and **a chunk id is only meaningful against the chunk set that
produced it.** Re-chunking the corpus — a parameter tweak, a new heading rule — renumbers
everything. A store built before that change hands back ids the in-memory corpus cannot resolve.

Left undetected, the damage lands two layers away and wearing a disguise: `remember()` skips ids it
cannot look up, so the grounding validator then rejects every citation, burns the retry budget, and
the run dies as `UnexpectedModelBehavior` — which reads like a *model* problem. You would debug the
prompt for an hour before suspecting the store.

## The approach

**A. Two exception types, because there are two failures.**

```python
class VectorsNotBuiltError(RuntimeError): ...   # ordinary, expected
class VectorsStaleError(RuntimeError): ...      # never ordinary
```

`VectorsNotBuiltError` deliberately mirrors Phase 1a's `ChunksNotBuiltError`, so the two
git-ignored artifacts fail the same way and both name the command that fixes them.

**B. A committed manifest is what makes staleness detectable at all.**

The store is ignored; `data/processed/vectors_meta.json` is committed, and records the chunk
snapshot ids, the embedding model, the dimensionality and the distance metric. Without it, "is this
store current?" is simply not a question anyone could ask. Same bargain the chunker already made:
**the artifact is ignored, the manifest is committed, and reproducibility is *checked* rather than
asserted.**

`VectorIndex.open` compares before answering anything, and the message names the fix:

```python
if manifest.chunker_snapshot_id != expected:
    raise VectorsStaleError(
        f"the vector store was built against chunks {manifest.chunker_snapshot_id} but the "
        f"corpus is now {expected}. Every stored chunk id refers to the old chunking, so "
        f"citations would resolve against passages that no longer exist. Run `make embed`."
    )
```

**C. The asymmetry, at the point of loading.**

```python
try:
    return await VectorIndex.open(embedder)
except VectorsNotBuiltError as exc:
    logger.warning("semantic search unavailable: %s", exc)
    return None
```

One is caught; the other is conspicuously **not**. `VectorsStaleError` propagates out of the
lifespan and **stops the server booting.** Refusing to start is a strange thing to want, until you
price the alternative: a server that starts and serves wrong citations. Failing at boot with a
message naming `make embed` is the cheap version of that discovery.

## The same two errors, a different policy per context

This is the part worth presenting, because it shows the rule is *derived* rather than copied around.
Three call sites, three policies, each following from what wrongness would cost **there** (the
fourth resource, Phase 2's search client, is the section below — it has only half of this table's
problem, and the half it lacks is instructive):

| Context | Missing | Stale | Why |
|---|---|---|---|
| **API startup** | log, degrade to `None` | **refuse to boot** | the app must never serve a wrong citation |
| **Answering a request** | 503 naming `make embed` | (never reached — boot failed) | no stub fallback, and no silent downgrade to lexical |
| **Eval runner** | **`SystemExit`** | **`SystemExit`** | *both* are fatal here |

The eval runner is the interesting row. It treats a *missing* store as fatal where the API treats it
as ordinary — because silently falling back to lexical would produce a plausible score for a
configuration nobody asked for, while the run record would name the toolset that was *requested*.
That is the one failure capable of corrupting the measurement the whole phase exists to make.

The request path applies the same reasoning to a subtler temptation: `_unavailable()` refuses to
quietly degrade `both` to lexical-only. The answer would be perfectly real — just measured against a
retrieval setup nobody chose, with nothing in the response saying so.

**`make embed-check`** is the offline half: it compares the snapshot id and row count without
re-embedding, so drift is caught in the same breath as `make chunk-check` rather than at boot.

### The web lane has only one of the two errors, and the absence is the point

Phase 2 added a fourth resource to this rule — the Tavily client — and it exercises exactly **half**
of it, which is a useful check that the rule is about *consequences* rather than about resources.

A search API has no local artifact, so there is nothing that can go **stale**: every call is fresh by
construction, and the "refuse to boot" branch has nothing to guard. What remains is *missing*, and
it differs from the other three in one visible way: no build step fixes it. `chunks.jsonl`,
the vector store and the Parquet mirrors are all cured by a `make` target; a missing
`TAVILY_API_KEY` is cured by editing `.env`. So the 503 names the file rather than a command —
degrade at startup, refuse at request time, and say the right thing about *why*.

There is a second half of the same rule inside the lane, one layer down. A Tavily **outage
mid-request** is neither missing nor stale; it is a resource that was there at boot and is not there
now. It cannot refuse to boot and it must not fail the whole answer, so it degrades — but into a
result carrying `unavailable`, never an empty list. That distinction is the rule's core applied to a
third state: an empty result means *the web does not appear to cover this*, and an outage means
*nothing was asked*. Collapsing them would let a rate limit be served to a reader as a finding.

## Evidence

- Three tests drive the refusal directly — different chunks, different model, and manifest-without-store
  (the realistic fresh-clone shape, which must report *not built* rather than *stale*).
- The snapshot id is pinned against **every** input that could change a vector — model,
  dimensionality, distance metric, chunk snapshots — plus a test that it does not depend on dict
  ordering, since it reaches a committed file.
- It fired in real use during development: `make embed-check` and a 503 naming `make embed` are what
  the app reported before the store was built, rather than anything mysterious.

## Why it presents well

It is a small amount of code carrying an opinion most systems get wrong by default: **degrade when
the system is visibly reduced, refuse when it would be invisibly wrong.** Most error handling
collapses toward one policy — usually "log it and carry on", which is precisely backwards for the
dangerous case.

Two ideas transfer beyond this repo:

1. **Classify failures by detectability, not severity.** "Loudly broken" is a *better* state than
   "quietly wrong", and error types should encode that judgement rather than leave it to each caller.
2. **A derived artifact needs a committed description of what it was derived from.** Otherwise
   staleness is not a bug you can catch — it is a bug you cannot even express.
