"""Tests for `settings.py`.

Every test builds `IsolatedSecrets`, never `Secrets`. Without that isolation these read the
developer's real `.env`, so the "missing key is rejected" cases would pass on CI and fail on the
machine of anyone who has actually configured the project — the worst direction for a guard to
fail in.

A subclass rather than the `_env_file=None` keyword pydantic-settings documents: that keyword is
invisible to pyright's synthesised `__init__`, so passing it is a `reportCallIssue` on every line.
Overriding `model_config` says the same thing in a way the type checker can see, and pydantic
merges it over the parent's config, so only `env_file` changes.
"""

import pytest
from pydantic import SecretStr, ValidationError
from pydantic_settings import SettingsConfigDict

from health_coverage_navigator.settings import Secrets

#: Must *begin* with a token `scan_sensitive.py`'s `PLACEHOLDER_RE` recognises (`placeholder`,
#: `dummy`, `fake`, `test`, ...). A value that merely reads as fake to a human — `unit-test-value`
#: — is a blocking `cred:assignment` hit, because the detector anchors at the start of the value.
SECRET = "placeholder-not-a-real-key"


class IsolatedSecrets(Secrets):
    """`Secrets` with the `.env` file detached, so tests see only what they set."""

    model_config = SettingsConfigDict(env_file=None)


def test_key_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    assert IsolatedSecrets().openai_api_key.get_secret_value() == SECRET


def test_missing_key_is_a_startup_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValidationError):
        IsolatedSecrets()


def test_empty_key_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """`.env.example` ships the key blank, so this is the `cp`-without-editing path.

    An empty string is a valid `str`; only `min_length=1` makes this fail at boot instead of as a
    401 on the first user question.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "")
    with pytest.raises(ValidationError):
        IsolatedSecrets()


def test_key_is_masked_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    """A traceback or a logged settings object must not print the key."""
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    secrets = IsolatedSecrets()
    assert SECRET not in repr(secrets)
    assert SECRET not in str(secrets.openai_api_key)


def test_secrets_are_frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    secrets = IsolatedSecrets()
    with pytest.raises(ValidationError):
        secrets.openai_api_key = SecretStr("other")


def test_secrets_holds_only_credentials() -> None:
    """The guardrail for the whole split.

    A tunable added here would become environment-overridable, and an env var is invisible to git —
    so an eval run configured by one could not be reproduced from the repo. Non-secrets belong in
    `config.yaml`. `agent_model` used to live here; that is exactly the mistake this catches.
    """
    assert set(Secrets.model_fields) == {"openai_api_key", "tavily_api_key"}


# ---------------------------------------------------------------- the web key ----------------
#
# Optional where the OpenAI key is required, because there is an agent without web search and
# there is no agent without a model. The blank-string cases are the ones that matter: `.env.example`
# ships `TAVILY_API_KEY=` empty, and an empty key that read as "configured" would boot cleanly and
# 401 on the first web question — the exact failure `min_length=1` prevents for OpenAI, arriving
# through the one door `min_length` cannot guard on an optional field.


def test_tavily_key_is_optional(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    assert IsolatedSecrets().tavily_api_key is None


def test_tavily_key_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    monkeypatch.setenv("TAVILY_API_KEY", SECRET)
    key = IsolatedSecrets().tavily_api_key
    assert key is not None
    assert key.get_secret_value() == SECRET


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_blank_tavily_key_reads_as_absent(monkeypatch: pytest.MonkeyPatch, blank: str) -> None:
    """The `cp .env.example .env` path. `""` must mean "no key", never "a key that is empty"."""
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    monkeypatch.setenv("TAVILY_API_KEY", blank)
    assert IsolatedSecrets().tavily_api_key is None


def test_tavily_key_is_masked_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    monkeypatch.setenv("TAVILY_API_KEY", SECRET)
    assert SECRET not in repr(IsolatedSecrets())
