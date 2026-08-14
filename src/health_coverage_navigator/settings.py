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

from pydantic import Field, SecretStr
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


@lru_cache(maxsize=1)
def get_secrets() -> Secrets:
    """The process-wide secrets, read once.

    Cached so that reading a credential in a request handler does not re-read and re-validate
    `.env` per request. Tests that need a different environment should construct `Secrets(...)`
    directly rather than clearing this cache — an explicit object is clearer than global mutation.
    """
    return Secrets()
