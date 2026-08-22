"""The agent loop: the grounding guardrail, the trace, and the mapping onto the contract.

Every test drives a **scripted model** (`FunctionModel`, from the `agent_kit` fixture) through
`agent.override(model=...)`, so what is under test is our code — the validator, the tool wrappers,
the citation mapping — and never a provider's behaviour. `conftest.py` blocks live requests
suite-wide as a backstop.

The scripts are written as "what would a model do wrong", because that is what a guardrail is for:
citing a chunk it never retrieved, quoting words that are not in the chunk, leaving a marker
pointing at nothing, answering with no sources at all.
"""

import asyncio
import re

import pytest
from pydantic_ai import Agent, UsageLimitExceeded

from health_coverage_navigator.agent.models import AgentAnswer
from health_coverage_navigator.agent.prompt import system_prompt
from health_coverage_navigator.agent.runtime import _claims, answer_question, build_agent
from health_coverage_navigator.agent.tools import needs_vectors, select_tools
from health_coverage_navigator.api.models import MARKER_RE, ChatRequest
from health_coverage_navigator.config import get_config

# ---------------------------------------------------------------- happy path ----------------


def test_a_grounded_answer_becomes_a_chat_response(agent_kit):
    response = agent_kit.run(agent_kit.SEARCH, agent_kit.answer())

    assert response.abstained is False
    assert response.answer.endswith("[c1]")
    assert [c.id for c in response.citations] == ["c1"]
    assert response.usage is not None and response.usage.latency_ms is not None


def test_citation_metadata_comes_from_the_corpus_not_the_model(agent_kit):
    """The model supplies only *which* chunk and *which words*. Everything a reader trusts — the
    title, the document it resolves to, the lane badge — is read off the real chunk."""
    response = agent_kit.run(agent_kit.SEARCH, agent_kit.answer())
    citation = response.citations[0]
    chunk = agent_kit.index.chunk(agent_kit.DEDUCTIBLE_ID)

    assert citation.chunk_id == agent_kit.DEDUCTIBLE_ID
    assert citation.doc_id == "glossary_deductible"
    assert citation.source_type == "reference"
    assert citation.title == chunk.citation_label
    assert citation.snippet in agent_kit.DEDUCTIBLE_TEXT


def test_the_trace_shows_the_tools_the_agent_actually_called(agent_kit):
    response = agent_kit.run(agent_kit.SEARCH, agent_kit.answer())

    kinds = [s.kind for s in response.trace]
    assert kinds[0] == "plan"
    assert kinds[-1] == "synthesis"
    assert set(kinds) == {"plan", "tool_call", "tool_result", "synthesis"}, (
        "the trace panel has an unrendered case"
    )

    call = next(s for s in response.trace if s.kind == "tool_call")
    assert call.tool == "search_corpus"
    assert call.input == {"query": "deductible", "k": 5, "source": None}
    result = next(s for s in response.trace if s.kind == "tool_result")
    assert result.duration_ms is not None


def test_trace_indices_are_contiguous_from_zero(agent_kit):
    """`TraceStep.index` is the panel's ordering key; a gap or a repeat renders as a jumbled
    list."""
    response = agent_kit.run(agent_kit.SEARCH, agent_kit.SEARCH, agent_kit.answer())
    assert [s.index for s in response.trace] == list(range(len(response.trace)))


def test_claims_are_verbatim_substrings_of_the_answer(agent_kit):
    """Derived rather than asked of the model precisely so this can never fail — Phase 4's
    hover-to-highlight does an index lookup, not a fuzzy match."""
    answer = agent_kit.answer(
        answer="A deductible is what you pay first. [c1] Then coinsurance applies. [c2]",
        citations=[
            {
                "id": "c1",
                "chunk_id": agent_kit.DEDUCTIBLE_ID,
                "snippet": "The amount you pay for covered",
            },
            {
                "id": "c2",
                "chunk_id": agent_kit.DEDUCTIBLE_ID,
                "snippet": "copayment or coinsurance",
            },
        ],
    )
    response = agent_kit.run(agent_kit.SEARCH, answer)

    assert len(response.claims) == 2
    for claim in response.claims:
        assert claim.text in response.answer


def test_an_uncited_tail_carries_no_claim(agent_kit):
    """A claim is a span *plus what backs it*. Text after the last marker has nothing behind it, so
    inventing a claim for it would attach provenance that does not exist."""
    answer = agent_kit.answer(
        answer="A deductible is what you pay first. [c1] Check your own plan documents."
    )
    response = agent_kit.run(agent_kit.SEARCH, answer)
    assert [c.text for c in response.claims] == ["A deductible is what you pay first. [c1]"]


# ---------------------------------------------------------------- abstention ----------------


def test_abstention_is_a_first_class_boolean(agent_kit):
    answer = AgentAnswer(
        abstained=True,
        answer="I don't have that in my reference material.",
        citations=[],
    )
    response = agent_kit.run(agent_kit.SEARCH, answer, message="which dermatologists take Aetna?")

    assert response.abstained is True
    assert response.citations == []
    assert response.claims == []
    # The trace is exactly what you would read to work out *why* it abstained, so it must survive.
    assert response.trace


# ---------------------------------------------------------------- the guardrail -------------


def test_citing_a_chunk_no_tool_returned_is_rejected(agent_kit):
    """The grounding rule made mechanical. The chunk is real and in the corpus — it was simply
    never retrieved this run, so it is not citable."""
    bad = agent_kit.answer(
        citations=[{"id": "c1", "chunk_id": agent_kit.PREMIUM_ID, "snippet": "every month"}]
    )
    reason = agent_kit.expect_rejection(agent_kit.SEARCH, bad)
    assert agent_kit.PREMIUM_ID in reason
    assert "no tool returned" in reason


def test_a_paraphrased_snippet_is_rejected(agent_kit):
    """A real source with words put in its mouth. Worse than a fabricated one, because it reads as
    more trustworthy."""
    bad = agent_kit.answer(
        citations=[
            {
                "id": "c1",
                "chunk_id": agent_kit.DEDUCTIBLE_ID,
                "snippet": "the sum you must spend before coverage kicks in",
            }
        ]
    )
    assert "not present in chunk" in agent_kit.expect_rejection(agent_kit.SEARCH, bad)


def test_a_whitespace_reflowed_snippet_is_accepted(agent_kit):
    """The corpora wrap mid-sentence, so a byte-exact test would reject genuine quotations and send
    the model into a retry loop it cannot win."""
    answer = agent_kit.answer(
        citations=[
            {
                "id": "c1",
                "chunk_id": agent_kit.DEDUCTIBLE_ID,
                "snippet": "before   your insurance\nplan starts to pay",
            }
        ]
    )
    response = agent_kit.run(agent_kit.SEARCH, answer)
    assert response.citations[0].snippet.startswith("before")


def test_a_dangling_marker_is_rejected(agent_kit):
    bad = agent_kit.answer(answer="A deductible is what you pay. [c1] Coinsurance follows. [c9]")
    assert "c9" in agent_kit.expect_rejection(agent_kit.SEARCH, bad)


def test_an_answer_with_no_citations_is_rejected(agent_kit):
    bad = AgentAnswer(abstained=False, answer="A deductible is what you pay first.", citations=[])
    assert "abstained=true" in agent_kit.expect_rejection(agent_kit.SEARCH, bad)


def test_the_guardrail_lets_a_corrected_answer_through(agent_kit):
    """The point of `ModelRetry` over a hard failure: the model is told what was wrong and fixes it.
    A guardrail that only ever fails would make `retries` pointless."""
    bad = agent_kit.answer(
        citations=[{"id": "c1", "chunk_id": agent_kit.PREMIUM_ID, "snippet": "every month"}]
    )
    response = agent_kit.run(agent_kit.SEARCH, bad, agent_kit.answer())
    assert response.citations[0].chunk_id == agent_kit.DEDUCTIBLE_ID


def test_every_served_marker_resolves(agent_kit):
    """The contract validator would 500 on a dangling marker. This asserts the guardrail keeps it
    from ever getting that far."""
    response = agent_kit.run(agent_kit.SEARCH, agent_kit.answer())
    known = {c.id for c in response.citations}
    assert set(MARKER_RE.findall(response.answer)) <= known


# ---------------------------------------------------------------- loop safety ---------------


def test_a_runaway_tool_loop_hits_the_usage_limit(agent_kit):
    """Without a ceiling this is a spinner until the request times out, rather than an answer or an
    honest abstention. Phase 4 adds cycle detection on top of this, not instead of it.

    A one-turn script never advances, so the model reformulates the same search forever — the exact
    shape of the failure the limits exist for.
    """
    with (
        build_agent(structured=False, web=False, live=False).override(
            model=agent_kit.script(agent_kit.SEARCH)
        ),
        pytest.raises(UsageLimitExceeded),
    ):
        asyncio.run(answer_question(ChatRequest(message="q"), agent_kit.index))


def test_the_limits_come_from_config():
    """A ceiling nobody can see is a ceiling nobody tunes. `config.yaml` carries all three, so a
    run that tripped one can be explained from the repo."""
    limits = get_config().agent
    assert limits.request_limit > 0
    assert limits.tool_calls_limit > 0
    assert limits.retries >= 1, "the grounding guardrail needs at least one retry to be useful"


# ---------------------------------------------------------------- tools ---------------------


def test_a_malformed_regex_is_a_retry_not_a_crash(agent_kit):
    """A bad pattern is the model's mistake to fix, so it comes back as a message rather than a
    500 the user sees."""
    response = agent_kit.run(
        ("grep_corpus", {"pattern": "(unclosed"}),
        agent_kit.SEARCH,
        agent_kit.answer(),
    )
    assert response.citations[0].chunk_id == agent_kit.DEDUCTIBLE_ID


def test_every_tool_widens_the_citable_set(agent_kit):
    """`get_chunk` and `grep_corpus` make a chunk citable exactly as `search_corpus` does — the
    guardrail is about what was *seen*, not about which tool saw it."""
    answer = agent_kit.answer(
        citations=[{"id": "c1", "chunk_id": agent_kit.PREMIUM_ID, "snippet": "every month"}]
    )
    response = agent_kit.run(("get_chunk", {"chunk_id": agent_kit.PREMIUM_ID}), answer)
    assert response.citations[0].doc_id == "glossary_premium"


def test_a_vector_hit_is_citable_like_any_other(agent_kit):
    """The Phase 1b tool goes through the same `remember` -> `seen_chunks` -> validator path as the
    lexical ones, so a semantic hit is citable on exactly the same terms. That is only true because
    `vector_search` resolves its ids through the same `CorpusIndex`; if it returned rows straight
    out of LanceDB, this citation would be rejected as ungrounded."""
    response = agent_kit.run(agent_kit.VECTOR_SEARCH, agent_kit.answer())

    assert response.abstained is False
    assert response.citations[0].chunk_id == agent_kit.DEDUCTIBLE_ID
    assert response.citations[0].source_type == "reference", (
        "1a and 1b search one lane two ways; the lane vocabulary must not grow a fourth value"
    )


def test_the_trace_names_which_search_found_the_passage(agent_kit):
    """docs/plan.md §1b's user-facing line: 'the trace shows which kind of search produced each
    citation'. `TraceStep.tool` already carries it, which is why no `Citation` field was added."""
    response = agent_kit.run(agent_kit.VECTOR_SEARCH, agent_kit.answer())
    tools = [step.tool for step in response.trace if step.tool]
    assert "vector_search" in tools
    assert "search_corpus" not in tools


# ---------------------------------------------------------------- toolset composition -------
#
# Phase 1b's eval axis. The comparison it feeds is only meaningful if the three configurations
# differ in *exactly* one thing — which tools the agent may see — so these tests are about that
# "exactly", not about which configuration retrieves better.


@pytest.mark.parametrize(
    ("toolset", "expected"),
    [
        ("lexical", {"search_corpus", "grep_corpus", "get_chunk", "list_documents"}),
        ("vector", {"vector_search", "get_chunk", "list_documents"}),
        ("both", {"search_corpus", "grep_corpus", "vector_search", "get_chunk", "list_documents"}),
    ],
)
def test_each_toolset_registers_exactly_its_tools(toolset, expected):
    assert {tool.__name__ for tool in select_tools(toolset)} == expected


@pytest.mark.parametrize("toolset", ["lexical", "vector", "both"])
def test_the_navigation_tools_are_in_every_configuration(toolset):
    """`get_chunk` and `list_documents` widen and orient; they do not rank. Dropping them from the
    vector-only run would fold "lost the ability to widen a hit" into the lexical-vs-vector number,
    and nothing downstream could separate the two effects again."""
    names = {tool.__name__ for tool in select_tools(toolset)}
    assert {"get_chunk", "list_documents"} <= names


@pytest.mark.parametrize("toolset", ["lexical", "vector", "both"])
def test_the_general_purpose_search_comes_first(toolset):
    """Order is what the model sees, and the recovery moves belong last."""
    names = [tool.__name__ for tool in select_tools(toolset)]
    assert names[-2:] == ["get_chunk", "list_documents"]


@pytest.mark.parametrize(
    ("toolset", "expected"), [("lexical", False), ("vector", True), ("both", True)]
)
def test_needs_vectors_matches_what_is_registered(toolset, expected):
    """The API's 503 and the eval runner's store-opening both branch on this, so it must not drift
    from `select_tools` — a `False` here with `vector_search` registered is a `ModelRetry` on every
    call, and a `True` with it absent is a pointless startup failure."""
    assert needs_vectors(toolset) is expected
    assert ("vector_search" in {t.__name__ for t in select_tools(toolset)}) is expected


@pytest.mark.parametrize("toolset", ["lexical", "vector", "both"])
def test_the_prompt_describes_only_the_tools_that_exist(toolset):
    """A prompt naming a tool the agent does not have is not a cosmetic flaw in an eval — it would
    make the comparison partly a measurement of how well each configuration copes with misleading
    instructions."""
    prompt = system_prompt(toolset)
    registered = {tool.__name__ for tool in select_tools(toolset)}
    for name in ("search_corpus", "grep_corpus", "vector_search", "get_chunk"):
        if name not in registered:
            assert name not in prompt, f"{toolset} prompt names {name}, which it cannot call"


def test_the_prompts_differ_between_toolsets():
    """Cheap guard against the composition silently collapsing back to one constant."""
    prompts = {system_prompt(t) for t in ("lexical", "vector", "both")}
    assert len(prompts) == 3


def test_a_toolset_gets_its_own_cached_agent():
    """`_build_agent`'s `lru_cache` keys on the toolset as well as the model. Sharing one `Agent`
    across two toolsets would silently give a 'lexical-only' run the vector tool — docs/progress.md
    records the same class of bug when `build_agent()` and `build_agent(None)` were two keys."""
    assert build_agent(toolset="lexical") is build_agent(toolset="lexical")
    assert build_agent(toolset="lexical") is not build_agent(toolset="vector")
    assert build_agent(toolset=None) is build_agent(toolset=get_config().agent.toolset)


# ---------------------------------------------------------------- unit ----------------------


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("One. [c1] Two. [c2]", ["One. [c1]", "Two. [c2]"]),
        ("Only prose, no markers.", []),
        ("Leading [c1] mid-sentence text.", ["Leading [c1]"]),
        ("Unknown marker [c7] is skipped.", []),
    ],
)
def test_claims_are_split_on_markers(answer: str, expected: list[str]):
    assert [c.text for c in _claims(answer, {"c1", "c2"})] == expected


def test_the_agent_is_one_instance_reused():
    """docs/plan.md's cross-cutting principle: one agent, more tools. Two instances would also mean
    `agent.override()` in a test silently missing the one the runtime uses — which happened."""
    assert build_agent() is build_agent()
    assert build_agent() is build_agent(get_config().agent.model)
    assert isinstance(build_agent(), Agent)


@pytest.mark.parametrize("toolset", ["lexical", "vector", "both"])
def test_the_prompt_names_the_three_corpora(toolset):
    """True for every toolset: the corpus description and the abstention rule are the shared half
    of the prompt, and only the search guidance varies."""
    from health_coverage_navigator.agent.prompt import system_prompt

    prompt = system_prompt(toolset)
    for corpus in ("healthcare_gov", "medicare_pubs", "medicare_ncd"):
        assert corpus in prompt
    assert re.search(r"abstain", prompt, re.IGNORECASE)
