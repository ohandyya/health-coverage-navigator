"""The web lane *through the agent* — registration, the trace, the budget, and the guardrail.

`tests/test_web_client.py` covers everything below the agent with no PydanticAI in the import
graph. This file is the other half: what happens when a scripted model calls `web_search`, cites
what came back, and — mostly — tries to cite something that did not.

**The guardrail tests are the point of this file.** They are written as *what would a model do
wrong*, and each asserts the retry **message** as well as the rejection, because a retry the model
cannot act on is a retry wasted. In this lane that matters more than in the other two: a chunk id
and a row id are self-evidently internal, but a URL is guessable, so "cite only what you retrieved"
is doing real work here rather than catching typos.

Every web client here is a real `WebSearchClient` over an `httpx.MockTransport` (`conftest`'s
`web_client`), so these exercise the actual hygiene and id assignment rather than a stand-in's.
"""

import pytest

from health_coverage_navigator.agent.runtime import build_agent
from health_coverage_navigator.agent.tools import select_tools
from health_coverage_navigator.agent.web_tools import TIME_RANGES, TOPICS
from health_coverage_navigator.config import get_config

#: The scripted `web_search` call every test below opens with.
SEARCH = ("web_search", {"query": "2026 open enrollment deadline", "topic": "news"})


def quoted(kit) -> str:
    """A verbatim span of the fixture result, as a model would copy it out."""
    return "November 1, 2025 through January 15, 2026"


# ---------------------------------------------------------------- registration ----------------


def test_the_web_tool_is_registered_only_when_asked() -> None:
    """The third axis, and `select_tools` is where it lives — the flag an eval run turns off."""
    names = {tool.__name__ for tool in select_tools("both", structured=True, web=True)}
    assert "web_search" in names
    assert names & {"search_corpus", "vector_search"}, "the reference lane must stay registered"
    assert "query_structured" in names, "the relational lane must stay registered"

    without = {tool.__name__ for tool in select_tools("both", structured=True, web=False)}
    assert "web_search" not in without


def test_the_web_tool_comes_last() -> None:
    """Order is what the model sees. The most expensive call, whose over-use is this phase's
    characteristic failure, is deliberately not the first thing in the list."""
    names = [tool.__name__ for tool in select_tools("both", structured=True, web=True)]
    assert names[-1] == "web_search"


def test_each_lane_configuration_is_a_separate_cached_agent() -> None:
    """The cache-key trap this repo has now taken three times (docs/agent.md §3).

    `build_agent` caches on `(model, toolset, structured, web)` and `override` applies to the
    instance it is called on — so two configurations sharing one object means a test's override
    silently does not apply and the run goes to the real provider. Phase 1b, Phase 1-c and Phase 2
    each added a defaulted argument, i.e. each added a fresh chance to make exactly this mistake.
    """
    assert build_agent(web=True) is build_agent(web=True)
    assert build_agent(web=True) is not build_agent(web=False)
    # The defaults must resolve *before* the lookup, or `build_agent()` and `build_agent(web=None)`
    # are two different keys for the same configuration.
    assert build_agent(web=None) is build_agent(web=get_config().agent.web_tools)


def test_the_prompt_only_promises_lanes_the_run_actually_has() -> None:
    """Describing a tool the agent does not have makes an eval partly a measurement of how well a
    configuration copes with misleading instructions.

    Phase 2 is the sharper case, and in the more dangerous direction: the stale sentence is an
    *instruction to abstain*. Every pre-Phase-2 prompt listed "anything needing current news" as out
    of reach, which with the web lane registered would decline the questions the lane was added for.
    """
    from health_coverage_navigator.agent.prompt import system_prompt

    with_web = system_prompt("both", structured=True, web=True)
    without = system_prompt("both", structured=True, web=False)

    assert "web_search" in with_web
    assert "web_search" not in without
    # Lowercased because the clause is sentence-capitalised when it leads the "no access" list.
    assert "no web search" in without.lower()
    assert "no web search" not in with_web.lower()
    # The abstention list must not still be telling a web-enabled agent to decline current events.
    assert "anything needing current news" in without
    assert "anything needing current news" not in with_web


# ---------------------------------------------------------------- the happy path --------------


def test_a_web_result_becomes_a_web_citation(agent_kit) -> None:
    """The end-to-end shape of a web answer: a search, a result cited by id, and a contract
    citation whose every displayed field was built from the result rather than from the model."""
    web = agent_kit.web_client(agent_kit.web_result(published_date="2025-10-01"))
    answer = agent_kit.answer(
        answer=f"Open enrollment runs {quoted(agent_kit)} in most states. [c1]",
        citations=[
            {"id": "c1", "result_id": agent_kit.WEB_RESULT_ID, "snippet": quoted(agent_kit)}
        ],
    )
    response = agent_kit.run(SEARCH, answer, web=web, message="when is open enrollment?")

    (citation,) = response.citations
    assert citation.source_type == "web"
    assert citation.url == agent_kit.WEB_URL
    assert citation.chunk_id is None and citation.doc_id is None
    assert citation.snippet == quoted(agent_kit)
    # The domain is in the title because the card hides the URL behind a link, and for a health
    # question who is saying something is part of the claim.
    assert "example.org" in citation.title
    assert "2025-10-01" in citation.title, "a dated result should show its date"


def test_the_trace_names_the_domains_the_agent_visited(agent_kit) -> None:
    """The single most useful line in a Phase 2 trace: which lane a question actually reached."""
    web = agent_kit.web_client(agent_kit.web_result())
    answer = agent_kit.answer(
        answer=f"It runs {quoted(agent_kit)}. [c1]",
        citations=[
            {"id": "c1", "result_id": agent_kit.WEB_RESULT_ID, "snippet": quoted(agent_kit)}
        ],
    )
    response = agent_kit.run(SEARCH, answer, web=web)

    calls = [s for s in response.trace if s.tool == "web_search" and s.kind == "tool_call"]
    results = [s for s in response.trace if s.tool == "web_search" and s.kind == "tool_result"]
    assert calls and results
    assert calls[0].input == {
        "query": "2026 open enrollment deadline",
        "topic": "news",
        "time_range": None,
        "max_results": 5,
    }
    assert "example.org" in results[0].summary


def test_a_reference_and_a_web_citation_coexist(agent_kit) -> None:
    """Three lanes share one citation id space, and the model has to be able to mix them."""
    web = agent_kit.web_client(agent_kit.web_result())
    answer = agent_kit.answer(
        answer=(
            "A deductible is what you pay before your plan starts to pay. [c1] "
            f"Open enrollment runs {quoted(agent_kit)}. [c2]"
        ),
        citations=[
            {
                "id": "c1",
                "chunk_id": agent_kit.DEDUCTIBLE_ID,
                "snippet": "The amount you pay for covered health care services",
            },
            {"id": "c2", "result_id": agent_kit.WEB_RESULT_ID, "snippet": quoted(agent_kit)},
        ],
    )
    response = agent_kit.run(agent_kit.SEARCH, SEARCH, answer, web=web)

    assert [c.source_type for c in response.citations] == ["reference", "web"]


# ---------------------------------------------------------------- the guardrail ---------------


def test_citing_a_result_no_search_returned_is_rejected(agent_kit) -> None:
    """`seen_results` is `seen_chunks` one lane over."""
    web = agent_kit.web_client(agent_kit.web_result())
    answer = agent_kit.answer(
        answer="Open enrollment ends January 15. [c1]",
        citations=[{"id": "c1", "result_id": "web#s9.9", "snippet": "whatever"}],
    )
    complaint = agent_kit.expect_rejection(SEARCH, answer, web=web)

    assert "web#s9.9" in complaint
    assert "no web search returned" in complaint
    assert "web#s1.1" in complaint, "the model must be told which ids it actually has"


def test_a_plausible_url_is_not_a_source(agent_kit) -> None:
    """**The failure this lane exists to prevent.**

    A model can write `https://www.cms.gov/...` that is well-formed, plausible, and never
    retrieved — and unlike an invented chunk id, neither a reader nor a grader could tell by
    looking. Requiring a `result_id` only a search could have assigned is what makes that
    impossible rather than merely discouraged.
    """
    web = agent_kit.web_client(agent_kit.web_result())
    answer = agent_kit.answer(
        answer="CMS announced the dates. [c1]",
        citations=[
            {
                "id": "c1",
                "result_id": "https://www.cms.gov/newsroom/press-releases/2026-open-enrollment",
                "snippet": quoted(agent_kit),
            }
        ],
    )
    complaint = agent_kit.expect_rejection(SEARCH, answer, web=web)
    assert "never a URL you did not get back" in complaint


def test_a_paraphrased_web_snippet_is_rejected(agent_kit) -> None:
    """A real source with words put in its mouth — which reads as *more* trustworthy, and is
    therefore worse than an unresolvable one."""
    web = agent_kit.web_client(agent_kit.web_result())
    answer = agent_kit.answer(
        answer="Enrollment closes in mid-January. [c1]",
        citations=[
            {
                "id": "c1",
                "result_id": agent_kit.WEB_RESULT_ID,
                "snippet": "enrollment closes in mid-January",
            }
        ],
    )
    complaint = agent_kit.expect_rejection(SEARCH, answer, web=web)

    assert "is not present in the content" in complaint
    assert agent_kit.WEB_URL in complaint, "naming the page makes the complaint actionable"


def test_a_verbatim_snippet_survives_rewrapping(agent_kit) -> None:
    """Whitespace-normalized, matching the chunk path and *not* the row path.

    Extracted web text wraps and re-wraps arbitrarily, so a byte-exact test would reject genuinely
    verbatim quotations and send the model into a retry loop it cannot win. A table cell has no
    wrapping to survive and its whitespace is data, which is why that lane is stricter. The two
    rules disagree on purpose.
    """
    web = agent_kit.web_client(agent_kit.web_result())
    rewrapped = "November 1, 2025\n   through January  15, 2026"
    answer = agent_kit.answer(
        answer="It runs from November through January. [c1]",
        citations=[{"id": "c1", "result_id": agent_kit.WEB_RESULT_ID, "snippet": rewrapped}],
    )
    response = agent_kit.run(SEARCH, answer, web=web)
    assert response.citations[0].source_type == "web"


def test_a_web_citation_with_no_snippet_is_caught_as_a_shape_error(agent_kit) -> None:
    """Caught by `AgentCitation._check_shape`, one layer before the grounding validator, so the
    model is told which half is missing rather than getting a less specific complaint."""
    from pydantic import ValidationError

    from health_coverage_navigator.agent.models import AgentCitation

    with pytest.raises(ValidationError) as excinfo:
        AgentCitation(id="c1", result_id="web#s1.1")
    assert "needs snippet" in str(excinfo.value)


def test_a_citation_mixing_a_web_result_and_a_passage_is_rejected() -> None:
    from pydantic import ValidationError

    from health_coverage_navigator.agent.models import AgentCitation

    with pytest.raises(ValidationError) as excinfo:
        AgentCitation(id="c1", result_id="web#s1.1", chunk_id="x", snippet="y")
    assert "mixes" in str(excinfo.value)


def test_a_passage_citation_still_validates_after_the_shape_refactor() -> None:
    """The regression this refactor exists to prevent.

    The original two-shape check detected a passage as `chunk_id is not None or snippet is not
    None`. A web citation also carries a `snippet`, so adding the third shape naively would have
    made every web citation read as a malformed passage — and the fix (discriminating on the id
    field alone) must not have broken the two shapes that already worked.
    """
    from health_coverage_navigator.agent.models import AgentCitation

    assert AgentCitation(id="c1", chunk_id="x", snippet="y").is_row is False
    assert AgentCitation(id="c1", row_id="r", cells={"a": "b"}).is_row is True
    assert AgentCitation(id="c1", result_id="web#s1.1", snippet="y").is_web is True


# ---------------------------------------------------------------- budget and degradation ------


def test_the_search_budget_is_enforced_and_is_not_a_retry(agent_kit, monkeypatch) -> None:
    """Past the budget the tool returns a *result the model reads*, not a `ModelRetry`.

    A retry could only produce the same refusal — the budget does not refill inside a run — while
    spending the grounding guardrail's allowance on a network-shaped condition. Same reasoning as
    the outage path.
    """
    # `Config` is frozen — deliberately, since configuration is not state — so the budget is
    # lowered by swapping what `web_tools` reads rather than by mutating a value in place.
    config = get_config()
    tightened = config.model_copy(
        update={"web": config.web.model_copy(update={"max_searches_per_run": 1})}
    )
    monkeypatch.setattr("health_coverage_navigator.agent.web_tools.get_config", lambda: tightened)

    web = agent_kit.web_client(agent_kit.web_result())
    answer = agent_kit.answer(
        answer="I could not confirm this. [c1]",
        citations=[
            {"id": "c1", "result_id": agent_kit.WEB_RESULT_ID, "snippet": quoted(agent_kit)}
        ],
    )
    response = agent_kit.run(SEARCH, SEARCH, answer, web=web)

    results = [s for s in response.trace if s.tool == "web_search" and s.kind == "tool_result"]
    assert len(results) == 2
    assert "budget" in results[1].summary or "unavailable" in results[1].summary


def test_an_outage_reaches_the_model_as_an_instruction(agent_kit) -> None:
    """An outage must never be reportable to a reader as "the web does not cover this"."""
    web = agent_kit.web_client(unavailable_status=429)
    answer = agent_kit.answer(
        answer="I could not check the web for this.",
        abstained=True,
        citations=[],
    )
    response = agent_kit.run(SEARCH, answer, web=web)

    assert response.abstained
    results = [s for s in response.trace if s.tool == "web_search" and s.kind == "tool_result"]
    assert "unavailable" in results[0].summary


def test_an_abstention_needs_no_web_citation(agent_kit) -> None:
    """Declining after an outage is a correct answer, and must not be blocked by the
    citations-required rule."""
    web = agent_kit.web_client(unavailable_status=500)
    answer = agent_kit.answer(answer="Not something I could check.", abstained=True, citations=[])
    assert agent_kit.run(SEARCH, answer, web=web).abstained


# ---------------------------------------------------------------- argument validation ---------


@pytest.mark.parametrize("bad", ["finance", "sports", ""])
def test_an_unsupported_topic_is_a_retry(agent_kit, bad: str) -> None:
    """`finance` is a real Tavily topic and is deliberately not exposed — every value offered to a
    model is a value it can choose wrongly."""
    web = agent_kit.web_client(agent_kit.web_result())
    answer = agent_kit.answer(
        answer=f"It runs {quoted(agent_kit)}. [c1]",
        citations=[
            {"id": "c1", "result_id": agent_kit.WEB_RESULT_ID, "snippet": quoted(agent_kit)}
        ],
    )
    response = agent_kit.run(
        ("web_search", {"query": "q", "topic": bad}),
        SEARCH,
        answer,
        web=web,
    )
    assert response.citations, "the model should recover from a bad argument, not fail the run"
    assert set(TOPICS) == {"general", "news"}


def test_an_unsupported_time_range_is_a_retry(agent_kit) -> None:
    web = agent_kit.web_client(agent_kit.web_result())
    answer = agent_kit.answer(
        answer=f"It runs {quoted(agent_kit)}. [c1]",
        citations=[
            {"id": "c1", "result_id": agent_kit.WEB_RESULT_ID, "snippet": quoted(agent_kit)}
        ],
    )
    response = agent_kit.run(
        ("web_search", {"query": "q", "time_range": "fortnight"}),
        SEARCH,
        answer,
        web=web,
    )
    assert response.citations
    assert "fortnight" not in TIME_RANGES
