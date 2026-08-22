"""Tests for `config.py` and the committed `config.yaml`.

Two jobs, and they are different. Most tests here validate the *mechanism* against a `tmp_path`
file. A few validate the **real committed `config.yaml`**, on the same argument
`tests/test_gold_set.py` makes for the live corpus: a typo in a committed input should fail
`make check-all`, not the next person's boot.
"""

import json
import re

import pytest
import yaml
from pydantic import ValidationError

from health_coverage_navigator.config import ChunkParams, Config, get_config, load_config
from health_coverage_navigator.corpus import CORPUS_NAMES, chunks_meta_path
from health_coverage_navigator.paths import CONFIG_PATH

#: Model families OpenAI publishes with no dated snapshot, so a floating alias is the only form
#: available. Every entry costs eval reproducibility — a rerun can differ from its baseline with
#: nothing in the record to say why — so this is a set of accepted losses, not a convenience.
ALLOWED_FLOATING_MODELS = frozenset({"gpt-5.6-luna", "gpt-5.6-sol", "gpt-5.6-terra"})


def _write(tmp_path, data: dict):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


@pytest.fixture
def valid() -> dict:
    """A minimal valid config, independent of whatever the committed one currently says."""
    return {
        "agent": {
            "model": "openai:gpt-5.4-mini-2026-03-17",
            "retries": 2,
            "request_limit": 12,
            "tool_calls_limit": 16,
            "request_retries": 5,
            "toolset": "both",
            "structured_tools": True,
            "web_tools": True,
            "live_tools": True,
        },
        "retrieval": {"top_k": 5, "bm25_k1": 1.2, "bm25_b": 0.75},
        "vectors": {
            "embedding_model": "text-embedding-3-small",
            "dimensions": 1536,
            "top_k": 5,
            "batch_size": 128,
        },
        "structured": {
            "max_rows": 200,
            "query_timeout_s": 5.0,
            "memory_limit": "512MB",
            "threads": 2,
        },
        "web": {
            "search_depth": "basic",
            "max_results": 5,
            "chunks_per_source": 3,
            "max_results_per_domain": 2,
            "max_searches_per_run": 3,
            "timeout_s": 20.0,
            "retry_after_cap_s": 5.0,
            "exclude_domains": [],
        },
        "live": {
            "max_calls_per_run": 8,
            "timeout_s": 15.0,
            "retry_after_cap_s": 5.0,
            "max_section_chars": 20000,
            "max_recalls": 5,
            "max_plans": 10,
        },
        "evals": {"judge_model": "openai:gpt-5.4-mini-2026-03-17"},
        "chunking": {
            "max_chars": 1200,
            "overlap_chars": 320,
            "min_tail_chars": 300,
            "min_fill_chars": 480,
            "min_doc_chars": 40,
        },
    }


# --------------------------------------------------------------------------------------------
# The mechanism
# --------------------------------------------------------------------------------------------


def test_loads_a_valid_file(tmp_path, valid: dict) -> None:
    config = load_config(_write(tmp_path, valid))
    assert config.agent.model == "openai:gpt-5.4-mini-2026-03-17"
    assert config.retrieval.top_k == 5
    assert config.chunking.max_chars == 1200


def test_missing_file_is_a_hard_error(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "does-not-exist.yaml")


def test_unknown_key_is_rejected(tmp_path, valid: dict) -> None:
    """`extra="forbid"` is why a typo'd key fails loudly instead of being silently ignored."""
    valid["retrieval"]["top_kk"] = 5
    with pytest.raises(ValidationError):
        load_config(_write(tmp_path, valid))


def test_missing_key_is_rejected(tmp_path, valid: dict) -> None:
    """No field carries a default, so an omitted key names itself in the error."""
    del valid["retrieval"]["bm25_b"]
    with pytest.raises(ValidationError):
        load_config(_write(tmp_path, valid))


def test_out_of_range_value_is_rejected(tmp_path, valid: dict) -> None:
    valid["retrieval"]["bm25_b"] = 1.5  # normalisation is a 0..1 weight
    with pytest.raises(ValidationError):
        load_config(_write(tmp_path, valid))


def test_non_mapping_file_is_rejected(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("- just\n- a list\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(path)


def test_config_is_frozen(tmp_path, valid: dict) -> None:
    config = load_config(_write(tmp_path, valid))
    with pytest.raises(ValidationError):
        config.retrieval.top_k = 99


def test_environment_cannot_reach_config(monkeypatch: pytest.MonkeyPatch, tmp_path, valid) -> None:
    """The whole point of the split: these are plain `BaseModel`s, with no environment source.

    `AGENT_MODEL` used to populate `Settings.agent_model`. It must now do nothing at all.
    """
    monkeypatch.setenv("AGENT_MODEL", "openai:evil")
    monkeypatch.setenv("TOP_K", "999")
    monkeypatch.setenv("MAX_CHARS", "999999")
    config = load_config(_write(tmp_path, valid))
    assert config.agent.model == "openai:gpt-5.4-mini-2026-03-17"
    assert config.retrieval.top_k == 5
    assert config.chunking.max_chars == 1200


def test_fingerprint_is_stable_and_order_independent(tmp_path, valid: dict) -> None:
    """Key order in the YAML must not change the fingerprint, or it cannot pin anything."""
    a = load_config(_write(tmp_path, valid))
    shuffled = {k: valid[k] for k in reversed(list(valid))}
    b = load_config(_write(tmp_path, shuffled))
    assert a.fingerprint() == b.fingerprint()


def test_fingerprint_changes_with_a_value(tmp_path, valid: dict) -> None:
    before = load_config(_write(tmp_path, valid)).fingerprint()
    valid["retrieval"]["top_k"] = 7
    assert load_config(_write(tmp_path, valid)).fingerprint() != before


# --------------------------------------------------------------------------------------------
# The committed file
# --------------------------------------------------------------------------------------------


def test_committed_config_is_valid() -> None:
    """A typo in `config.yaml` should fail `make check-all`, not someone's next boot."""
    assert CONFIG_PATH.is_file()
    assert isinstance(get_config(), Config)


def test_committed_model_is_pinned_or_a_known_exception() -> None:
    """Guards the reason for the pin, not the specific model.

    Bumping the model is fine. Silently dropping the date on a family that *has* snapshots is not
    — that is how a rerun starts differing from its baseline unattributably.
    """
    provider, _, model = get_config().agent.model.partition(":")
    assert provider == "openai"
    if model in ALLOWED_FLOATING_MODELS:
        return
    assert re.search(r"-\d{4}-\d{2}-\d{2}$", model), (
        f"{model!r} is neither pinned to a dated snapshot nor a listed floating-alias exception"
    )


def test_committed_judge_model_is_pinned_or_a_known_exception() -> None:
    """Same rule as the answering model: a judge on a floating alias makes a correctness score
    unattributable in exactly the way the answering model would."""
    provider, _, model = get_config().evals.judge_model.partition(":")
    assert provider == "openai"
    assert model in ALLOWED_FLOATING_MODELS or re.search(r"-\d{4}-\d{2}-\d{2}$", model)


def test_the_judge_is_not_the_model_it_grades() -> None:
    """A judge sharing the model it grades marks its own homework, and the failure is invisible —
    it agrees with itself most confidently exactly where both are wrong."""
    config = get_config()
    assert config.evals.judge_model != config.agent.model


def test_the_agent_loop_has_ceilings() -> None:
    """Loop safety goes in with the agent, not with Phase 4 (docs/plan.md §1a). A missing ceiling
    is a spinner until the request times out rather than an answer or an abstention."""
    agent = get_config().agent
    assert agent.request_limit > 0
    assert agent.tool_calls_limit >= agent.request_limit, (
        "a tool-call ceiling below the request ceiling makes the request ceiling unreachable"
    )
    assert agent.retries >= 1, "the grounding guardrail needs at least one retry to be useful"


def test_committed_chunking_matches_the_manifests() -> None:
    """`config.yaml` and `data/processed/*/chunks_meta.json` must not drift apart.

    `params_sha256` and every `snapshot_id` derive from these five values. If someone edits them
    without re-running `make chunk`, the committed manifests describe chunks nobody can rebuild —
    and `make chunk-check` would catch it only if it happened to be run.
    """
    chunking = get_config().chunking
    for source in CORPUS_NAMES:
        meta_path = chunks_meta_path(source)
        if not meta_path.is_file():
            continue
        manifest = json.loads(meta_path.read_text(encoding="utf-8"))
        assert manifest["params"] == chunking.model_dump(), (
            f"{source}: config.yaml chunking differs from the committed manifest"
        )
        assert manifest["params_sha256"] == chunking.fingerprint(), (
            f"{source}: params_sha256 is stale — run `make chunk`"
        )


def test_chunk_params_field_set_is_frozen_by_the_manifests() -> None:
    """Renaming or adding a field changes `model_dump()`, which invalidates three manifests."""
    assert list(ChunkParams.model_fields) == [
        "max_chars",
        "overlap_chars",
        "min_tail_chars",
        "min_fill_chars",
        "min_doc_chars",
    ]
