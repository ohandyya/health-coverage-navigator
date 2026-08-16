"""The embedding seam — the one place in Phase 1b that reaches a provider.

`Embedder` is a **batch-in, batch-out coroutine**, and both halves of that are deliberate:

*Async*, because it reaches the network. CLAUDE.md's rule is that a seam which might one day be
backed by a network call is declared `Awaitable` now — and this one is not hypothetical, it is a
network call today. `evals/answerers.py` and `evals/grading.py` had to move to async together once
a synchronous seam made an async judge impossible; this seam is born on the right side of that.

*Batched*, because the bulk build embeds 6,722 chunks and the query path embeds one. A
one-string-at-a-time signature would make the build issue 6,722 requests instead of ~53, and a
build-only batch variant would be a second implementation of the same call — two places for the
dimensionality check to disagree. One signature, `embed_one` for the query path's convenience.

**We compute the vectors and hand LanceDB plain floats** (docs/lancedb.md §2, "Option B"). LanceDB
never calls OpenAI on our behalf, so there is no hidden billing and batching stays in our hands.
"""

from collections.abc import Awaitable, Callable, Sequence

#: Text in, vectors out, order preserved. The contract callers rely on: `len(result) == len(texts)`
#: and `result[i]` is the embedding of `texts[i]`.
Embedder = Callable[[Sequence[str]], Awaitable[list[list[float]]]]


class EmbeddingError(RuntimeError):
    """The provider returned something the store cannot use.

    Raised rather than papered over because every alternative is worse: a wrong-width vector fails
    at Arrow-schema depth during a build (an error about a fixed-size list, thousands of rows in),
    and a short batch silently misaligns every vector after it with the wrong chunk — which is not
    an error at all, just a store that quietly returns the wrong passages.
    """


async def embed_one(embedder: Embedder, text: str) -> list[float]:
    """One string, one vector. The query path's convenience over the batch contract."""
    vectors = await embedder([text])
    if len(vectors) != 1:  # pragma: no cover - an Embedder that breaks its own contract
        raise EmbeddingError(f"expected 1 vector for 1 input, got {len(vectors)}")
    return vectors[0]


def openai_embedder(model: str, dimensions: int) -> Embedder:
    """An `Embedder` backed by OpenAI's embeddings endpoint.

    **Call this through the module** (`embedder.openai_embedder(...)`), not through a
    `from ... import openai_embedder`. It is the one function in the repo that spends money without
    going through PydanticAI, so `tests/conftest.py` replaces it suite-wide to keep the
    no-provider invariant — and a name bound at import time in a consumer module would not be
    replaced. `vectors/__main__.py` is the deliberate exception: it is the CLI whose entire job is
    to spend that money, and no test imports it.

    The credential is read through `get_secrets()` at **call** time, not import time, which is what
    keeps this module importable without a key — `make eval-retrieval` and every store test import
    it and never call it. Same reasoning as `agent/runtime._resolve_model`: PydanticAI reads
    `OPENAI_API_KEY` from the process environment and `uv run` does not load `.env`, so the
    credential has to be passed explicitly, and doing that in one audited place beats ambient
    global state.

    Two guards, both from docs/lancedb.md §2's requirements list, and both loud:

    * **Empty input.** The endpoint returns a 400 for an empty string. Chunking already forbids
      blank text (`Chunk._check_shape`), so an empty string here means a caller built the batch
      wrong — worth a clear error rather than a provider's.
    * **Wrong dimensionality.** Checked on every batch against the configured width. A model that
      quietly returns a different size would otherwise surface as an Arrow schema error thousands
      of rows into a paid build.
    """

    async def embed(texts: Sequence[str]) -> list[list[float]]:
        from openai import AsyncOpenAI

        from health_coverage_navigator.config import get_config
        from health_coverage_navigator.settings import get_secrets

        if not texts:
            return []
        blank = [i for i, t in enumerate(texts) if not t.strip()]
        if blank:
            raise EmbeddingError(
                f"cannot embed empty text at position(s) {blank[:5]}; the embeddings endpoint "
                f"rejects an empty string. Filter blank chunks before batching."
            )

        client = AsyncOpenAI(
            api_key=get_secrets().openai_api_key.get_secret_value(),
            max_retries=get_config().agent.request_retries,
        )
        response = await client.embeddings.create(model=model, input=list(texts))

        # Sorted by index rather than trusted in arrival order. The API documents that `data` comes
        # back in input order, but the cost of being wrong is silent misalignment — every vector
        # attached to the wrong chunk — which no test downstream would catch. One sort is cheap
        # insurance against a class of bug that has no symptom.
        items = sorted(response.data, key=lambda item: item.index)
        if len(items) != len(texts):
            raise EmbeddingError(f"asked for {len(texts)} embeddings, got {len(items)}")

        vectors = [list(item.embedding) for item in items]
        wrong = {len(v) for v in vectors} - {dimensions}
        if wrong:
            raise EmbeddingError(
                f"{model} returned {sorted(wrong)}-dimensional vectors, but vectors.dimensions "
                f"is {dimensions}. Documents and queries must share one embedding space — fix "
                f"config.yaml and rebuild with `make embed`."
            )
        return vectors

    return embed
