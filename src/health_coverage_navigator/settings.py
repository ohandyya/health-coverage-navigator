"""Secrets: the process environment, and the `.env` file behind it.

**Secrets only.** Non-secret configuration lives in `config.py`, read from a committed
`config.yaml`, and is deliberately *not* readable from the environment — `config.py`'s docstring
argues the split. The short version: a secret must come from the environment because it cannot be
committed; anything that changes an eval result must not, because an env var is invisible to git.

A field on this class is reachable by any environment variable of the same name. That is the right
behaviour for a credential and the wrong behaviour for a tunable, which is why nothing but
credentials may be added here.

A leaf module in the same spirit as `paths.py`. It imports nothing from this package except
`paths`, and in particular **imports no web framework** — the same argument `api/models.py` makes
for the contract models: `evals/` and `tests/` need configuration without dragging FastAPI into
their import graph.

The `.env` path is absolute, resolved from `REPO_ROOT`, so configuration does not depend on the
directory a command happens to be run from. `uv run` does not load `.env` on its own, so without
this module the file is inert.
"""

from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from health_coverage_navigator.paths import REPO_ROOT


class Secrets(BaseSettings):
    """Credentials, validated once.

    Frozen because configuration is not state: a module that mutated this would be changing the
    meaning of an eval run halfway through it.
    """

    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        # `.env` grows keys ahead of the phase that reads them — `CMS_MARKETPLACE_API_KEY` is
        # already in `.env.example`, commented, for Phase 3. An unknown key must not crash boot.
        extra="ignore",
        frozen=True,
    )

    #: `min_length=1` is load-bearing, not decoration. `.env.example` ships this key **blank**, so
    #: a `cp .env.example .env` with no edit produces an empty string — which is a perfectly valid
    #: `str` and would sail through a bare annotation, boot cleanly, and fail with a 401 on the
    #: first user question. This turns that into a startup error, which is the entire reason
    #: `pydantic-settings` was chosen over `python-dotenv`.
    #:
    #: `default=...` means "required" to pydantic while reading as "has a default" to pyright's
    #: `dataclass_transform` synthesis of `__init__`. Without it, every `Secrets()` call — which is
    #: the *only* correct way to call it, since the values come from the environment — is a
    #: `reportCallIssue` for a missing argument. The alternative was a `# pyright: ignore` at each
    #: call site, which suppresses the real errors along with the spurious one.
    openai_api_key: SecretStr = Field(
        default=...,
        min_length=1,
        description="OpenAI API key. Server-side only — never expose via a VITE_* var.",
    )

    #: **Optional, unlike `openai_api_key`** — and the asymmetry is deliberate. There is no agent
    #: without a model, so a missing OpenAI key is a startup error. There *is* an agent without web
    #: search: a clone that only wants the reference and relational lanes must still boot, exactly
    #: as one with no vector store or no Parquet mirrors does. Whether `None` is fatal is decided by
    #: `agent.web_tools` in `config.yaml`, and only `api/routes/chat.py` asks that question.
    #:
    #: `_blank_is_none` below is the same trap `min_length=1` catches above, arriving through a
    #: different door. `.env.example` ships `TAVILY_API_KEY=` **blank**, so a `cp .env.example .env`
    #: with no edit yields `""` — a valid `str` that would read as "configured", boot cleanly, and
    #: fail with a 401 on the first web question. `min_length=1` cannot be used here because the
    #: field is genuinely optional; mapping `""` to `None` collapses "no key" and "empty key" into
    #: one well-handled state instead of two, one of which is a live credential error.
    tavily_api_key: SecretStr | None = Field(
        default=None,
        description=(
            "Tavily API key (Phase 2 web lane). Server-side only — never expose via a VITE_* var."
        ),
    )

    #: **Optional, like `tavily_api_key` and for the same reason** — but with one difference worth
    #: recording: a missing Tavily key removes the *whole* web lane, while a missing Marketplace key
    #: removes only part of the live lane. openFDA and NPPES are keyless
    #: (docs/structured-api-tools.md §4, §5), so `agent.live_tools: true` with no key here is a
    #: perfectly coherent deployment — two of three sources work, and the agent is told which
    #: questions it therefore cannot answer rather than the lane refusing to start.
    #:
    #: Carries the same blank-is-none treatment as Tavily's, for the same `cp .env.example .env`
    #: trap. And one hazard neither of the others has: **CMS keys expire every 60 days**, so this
    #: value goes stale on a schedule rather than only when someone changes it. A rotation needs
    #: `.env` edited *and* the process restarted, since this class is frozen behind an `lru_cache`.
    cms_marketplace_api_key: SecretStr | None = Field(
        default=None,
        description=(
            "CMS Marketplace API key (Phase 3 live lane). Server-side only — never expose via a "
            "VITE_* var. Expires every 60 days; CMS emails a replacement."
        ),
    )

    #: **Optional, and the reason it exists is a reversal worth recording.** §4 of
    #: docs/structured-api-tools.md decided against requesting an openFDA key: the keyless ceiling
    #: is 1,000 requests/day per *IP*, and per-drug lookups do not approach it. That reasoning still
    #: holds for a hand-run question; what it did not survive is the eval sweep, whose worst case
    #: was measured at 78% of a single day's allowance — the exact tripwire §4 wrote down.
    #:
    #: So a key is now configured, and the field is optional because the decision it reverses is
    #: still half-true: **without this the FDA tools work perfectly well**, just at the lower
    #: ceiling. That is the difference from `cms_marketplace_api_key`, whose absence removes tools
    #: entirely.
    openfda_api_key: SecretStr | None = Field(
        default=None,
        description=(
            "openFDA API key. Optional — the FDA tools work without it at 1,000 requests/day per "
            "IP; with it, 120,000/day per key. Server-side only."
        ),
    )

    @field_validator("tavily_api_key", "cms_marketplace_api_key", "openfda_api_key", mode="before")
    @classmethod
    def _blank_is_none(cls, value: object) -> object:
        """An unset key and a whitespace-only key are the same thing: no key."""
        return None if isinstance(value, str) and not value.strip() else value


@lru_cache(maxsize=1)
def get_secrets() -> Secrets:
    """The process-wide secrets, read once.

    Cached so that reading a credential in a request handler does not re-read and re-validate
    `.env` per request. Tests that need a different environment should construct `Secrets(...)`
    directly rather than clearing this cache — an explicit object is clearer than global mutation.
    """
    return Secrets()
