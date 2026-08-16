"""Non-secret configuration, read from the committed `config.yaml` and never from the environment.

The split against `settings.py` is not "secret vs non-secret" — it is **git-ignored machine-local
credential vs committed reproducible input**, which is the same line the repo already draws between
`chunks.jsonl` and `chunks_meta.json`. That framing is what decides the env-var question:

- A secret must come from the environment, because it cannot be committed.
- Anything that changes an eval result must **not** come from the environment, because an env var
  is invisible to git. A run whose parameters came from an unrecorded `AGENT_MODEL=...` cannot be
  reproduced from the repo, and the score is worth less for it.

So nothing here inherits `BaseSettings`, and that is load-bearing rather than incidental: a plain
`BaseModel` has no environment source to accidentally re-enable. `extra="forbid"` turns a typo'd
YAML key into a startup error instead of a silently-ignored line.

**Fields carry no defaults.** `config.yaml` is committed, so it is always present, and a default in
Python beside a value in YAML is two sources of truth that drift. A missing key is an error naming
the key; that is the better failure.

**This module must not import from `chunking`.** `chunking/__init__.py` imports `pipeline`, which
imports this module, so `from ...chunking.params import ChunkParams` here would be a cycle — the
same shape as the `api/__init__.py` note in docs/progress.md. That is why `ChunkParams` is defined
here rather than imported, and why `chunking/params.py` now holds only `CHUNKER_VERSION`.
"""

import hashlib
import json
from functools import lru_cache
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from health_coverage_navigator.paths import CONFIG_PATH

_FROZEN = ConfigDict(frozen=True, extra="forbid")

#: Which retrieval tools the agent is allowed to see. Phase 1b's eval axis, defined here rather
#: than in `agent/` so `config.py` can type the setting without importing the agent package —
#: `agent/tools.py` imports it back. The navigation tools (`get_chunk`, `list_documents`) are in
#: every configuration; what this selects is the *ranked retrieval* the agent may reach for.
Toolset = Literal["lexical", "vector", "both"]


def _fingerprint(model: BaseModel) -> str:
    """Stable sha256 over a model's values.

    `sort_keys=True` is what makes it stable: dict ordering must never reach a committed artifact,
    the same rule `pipeline.py` states for chunk output.
    """
    return hashlib.sha256(
        json.dumps(model.model_dump(), sort_keys=True).encode("utf-8")
    ).hexdigest()


class AgentConfig(BaseModel):
    """The answering agent, and the ceilings on its loop."""

    model_config = _FROZEN

    #: PydanticAI model string, `provider:model`. See config.yaml for why this one is not pinned
    #: to a dated snapshot and what that costs.
    model: str = Field(min_length=1)

    #: How many times a rejected answer may be re-attempted. This is the grounding guardrail's
    #: budget: `runtime._validate_grounding` raises `ModelRetry` with what went wrong attached.
    retries: int = Field(ge=0, le=5)

    #: Ceiling on model requests in one run, and on tool calls across it. Loop safety goes in with
    #: the agent rather than with Phase 4 (docs/plan.md): cheap now, painful to retrofit.
    request_limit: int = Field(gt=0)
    tool_calls_limit: int = Field(gt=0)

    #: How many times the HTTP client retries a transport-level failure — a 429 or a 5xx. Nothing
    #: to do with `retries` above, which is about the *content* of an answer. Lives in config
    #: rather than in a flag because it decides whether a rate-limited question becomes a delayed
    #: success or a recorded error, and that changes an eval score.
    request_retries: int = Field(ge=0, le=10)

    #: Which retrieval tools the agent may see. Committed rather than flag-only because it decides
    #: what the *shipped* app does; `--toolset` on the eval runner overrides it for one run, and
    #: the run record carries which was used.
    toolset: Toolset


class EvalsConfig(BaseModel):
    """Grading. Separate from `agent:` because the judge must be swappable without touching what
    is being judged — a judge sharing the model it grades would mark its own homework."""

    model_config = _FROZEN

    judge_model: str = Field(min_length=1)


class RetrievalConfig(BaseModel):
    """Phase 1a lexical retrieval. Phase 1b re-backs the same interface and reuses `top_k`."""

    model_config = _FROZEN

    top_k: int = Field(gt=0)
    bm25_k1: float = Field(gt=0)
    bm25_b: float = Field(ge=0, le=1)


class VectorsConfig(BaseModel):
    """Phase 1b semantic retrieval: the embedding model, and how the store is built and queried.

    Separate from `RetrievalConfig` rather than folded into it, because the two are tuned against
    different things and on different budgets — `bm25_k1`/`bm25_b` sweep for free, while changing
    `embedding_model` or `dimensions` is a full re-ingest (docs/lancedb.md §2). Keeping them apart
    means a lexical sweep cannot silently invalidate the vector store.
    """

    model_config = _FROZEN

    #: OpenAI embeddings model. The **same model must embed documents and queries**, which is why
    #: there is one key and not two: two keys is a way to get an incompatible embedding space.
    #: Changing this is not a config tweak — it invalidates every stored vector, and
    #: `VectorIndex.open` refuses a store whose manifest disagrees.
    embedding_model: str = Field(min_length=1)

    #: Native dimensionality of `embedding_model`. Recorded rather than inferred so the Arrow
    #: schema, the manifest and the store cannot disagree about a table that is expensive to
    #: rebuild — a mismatch is a startup error rather than a silently wrong nearest neighbour.
    dimensions: int = Field(gt=0)

    #: How many passages `vector_search` returns by default. Mirrors `retrieval.top_k` today and is
    #: deliberately a separate key: Phase 1b compares lexical against vector, and a shared value
    #: would make "retrieve more" impossible to try on one side alone.
    top_k: int = Field(gt=0)

    #: Inputs per embeddings request during a bulk build. Only the offline build reads this; a
    #: query embeds one string. 128 keeps the 6,722-chunk ingest to ~53 requests without putting a
    #: single failure in charge of a large batch.
    batch_size: int = Field(gt=0)


class ChunkParams(BaseModel):
    """Chunking parameters, and the fingerprint that pins an eval run to them.

    Every value is derived from a measured distribution over the live corpus rather than picked by
    feel; docs/chunking.md carries the measurements and config.yaml carries the short version
    beside each value.

    Sizes are in **characters, not tokens**, with no tokenizer dependency. Phase 1a retrieval is
    lexical, where tokens are irrelevant; Phase 1b's embedding ceiling (~32k chars) is 27x
    `max_chars`, so a real token count would only buy a cost estimate — and one baked into a
    committed artifact would be invalidated by any embedding-model change. Use ~4 chars/token when
    an estimate is needed (`Chunk.approx_tokens`).

    **The field set and their names are frozen by `data/processed/*/chunks_meta.json`.** Renaming,
    adding, or reordering a field changes `model_dump()`, which changes `params_sha256` and every
    `snapshot_id` derived from it — invalidating three committed manifests. Do that only with a
    `make chunk` in the same change.
    """

    model_config = _FROZEN

    max_chars: int = Field(gt=0)
    overlap_chars: int = Field(ge=0)
    min_tail_chars: int = Field(ge=0)
    min_fill_chars: int = Field(ge=0)
    min_doc_chars: int = Field(ge=0)

    def fingerprint(self) -> str:
        """Stable sha256 over the parameter values, for the chunk manifest's `params_sha256`."""
        return _fingerprint(self)


class Config(BaseModel):
    """Everything in `config.yaml`."""

    model_config = _FROZEN

    agent: AgentConfig
    retrieval: RetrievalConfig
    vectors: VectorsConfig
    evals: EvalsConfig
    chunking: ChunkParams

    def fingerprint(self) -> str:
        """Stable sha256 over the whole configuration.

        Distinct from `chunking.fingerprint()`, which covers only the chunk params and is pinned by
        the committed manifests. This one is for an eval run record: it is the answer to "what was
        this score measured under", which matters most when the agent model is a floating alias
        that can move underneath a rerun.
        """
        return _fingerprint(self)


def load_config(path: Any = None) -> Config:
    """Read and validate `config.yaml`. A missing or malformed file is a hard error.

    `path` is a parameter so tests can point at a `tmp_path` — the same real testing need that made
    `create_app(dist_dir=...)` injectable, and the reason it is resolved here at call time rather
    than bound as a default (docs/progress.md records that exact bug in `evals/runner.py`).
    """
    config_path = CONFIG_PATH if path is None else path
    if not config_path.is_file():
        raise FileNotFoundError(
            f"{config_path} is missing. It is committed to the repo — restore it from git rather "
            f"than recreating it, so the values match what the manifests were built under."
        )
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{config_path} must contain a YAML mapping, got {type(data).__name__}")
    return Config.model_validate(data)


@lru_cache(maxsize=1)
def get_config() -> Config:
    """The process-wide configuration, read once.

    Deliberately separate from `settings.get_secrets()` rather than composed into one object: a
    chunking run or a retrieval test needs `top_k` and no credentials, and composing the two would
    make every such caller fail when `OPENAI_API_KEY` is unset.
    """
    return load_config()
