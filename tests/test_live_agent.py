"""The openFDA tools *through the agent* — registration, the trace, the budget, the guardrail.

`tests/test_live_clients.py` covers everything below the agent with no PydanticAI in the import
graph. This file is the other half: what happens when a scripted model calls `drug_label` or
`drug_recalls`, cites what came back, and — mostly — tries to cite something that did not.

**The guardrail tests here inherit the relational lane's shape rather than the web lane's**, and
that is the point of §14a: a live-API record *is* a row. It joins `seen_rows`, it is cited with
`row_id` + `cells`, and the byte-exact cell comparison that Phase 1-c built applies to it unchanged.
No fourth citation shape was added, so no fourth set of guardrail tests was needed.

Every client here is a real `OpenFdaClient` over an `httpx.MockTransport` (`conftest`'s
`openfda_client`), so these exercise the actual section resolution and id assignment.
"""

import pytest

from health_coverage_navigator.agent.live_tools import SECTIONS
from health_coverage_navigator.agent.tools import select_tools
from health_coverage_navigator.config import get_config

LABEL = ("drug_label", {"name": "Examplor", "section": "indications"})
RECALLS = ("drug_recalls", {"name": "Examplor"})


# ---------------------------------------------------------------- registration ----------------


def test_the_live_tools_are_registered_only_when_asked() -> None:
    names = {t.__name__ for t in select_tools("both", structured=True, web=True, live=True)}
    assert {"drug_label", "drug_recalls"} <= names
    assert names & {"search_corpus", "vector_search"}, "the reference lane must stay registered"
    assert "query_structured" in names, "the mirror half of this lane must stay registered"
    assert "web_search" in names, "the web lane must stay registered"

    without = {t.__name__ for t in select_tools("both", structured=True, web=True, live=False)}
    assert not ({"drug_label", "drug_recalls"} & without)


def test_the_live_tools_come_last() -> None:
    """Order is what the model sees. These are the narrowest tools registered — each answers one
    specific question about one specific drug — so a model reaching for them first has decided what
    kind of question it faces before looking."""
    names = [
        t.__name__
        for t in select_tools("both", structured=True, web=True, live=True, marketplace=True)
    ]
    assert (
        names[-5:]
        == [
            "drug_label",
            "drug_recalls",
            "lookup_provider",
            "find_drug",
            "check_drug_coverage",
        ]
        or names[-1] == "find_plans"
    )
    assert names.index("web_search") < names.index("drug_label")


def test_the_live_axis_is_a_separate_cached_agent(agent_kit) -> None:
    """The cache-key trap this repo has now taken four times (docs/agent.md §3). A fifth axis is a
    fifth chance at it, and the two agents must not be the same object."""
    from health_coverage_navigator.agent.runtime import build_agent

    with_live = build_agent(structured=False, web=False, live=True)
    without = build_agent(structured=False, web=False, live=False)
    assert with_live is not without


def test_the_prompt_describes_the_lane_only_when_it_exists() -> None:
    """A prompt that names a tool the agent does not have turns an eval into a measurement of how
    well a configuration copes with misleading instructions."""
    from health_coverage_navigator.agent.prompt import system_prompt

    assert "drug_recalls" in system_prompt("both", live=True)
    assert "drug_recalls" not in system_prompt("both", live=False)
    # Whitespace-normalised: the "what you do not have" sentence is wrapped for readability in a
    # diff, so the clause can straddle a line break.
    without = " ".join(system_prompt("both", live=False).split())
    assert "no FDA drug labelling or recall data" in without


# ---------------------------------------------------------------- answers ---------------------


def test_a_label_answer_is_citable_as_a_row(agent_kit) -> None:
    """§14a end to end: the record joins `seen_rows` and is cited with the row shape."""
    response = agent_kit.run(
        LABEL,
        agent_kit.answer(
            answer="It is indicated for the example condition. [c1]",
            citations=[
                {
                    "id": "c1",
                    "row_id": "fda#l1.1",
                    "cells": {"text": agent_kit.FDA_TEXT},
                }
            ],
        ),
        openfda=agent_kit.openfda_client(),
    )

    assert response.abstained is False
    citation = response.citations[0]
    assert citation.source_type == "structured_api", (
        "the live half of an existing lane, not a new one"
    )
    assert citation.chunk_id is None


def test_a_live_citation_carries_its_own_refetchable_url(agent_kit) -> None:
    """§14b — the first structured-lane citation in this repo to link to the exact record rather
    than to the dataset it lives in, and it needed no contract change to do it."""
    response = agent_kit.run(
        LABEL,
        agent_kit.answer(
            answer="Indicated for the example condition. [c1]",
            citations=[{"id": "c1", "row_id": "fda#l1.1", "cells": {"text": agent_kit.FDA_TEXT}}],
        ),
        openfda=agent_kit.openfda_client(),
    )

    url = response.citations[0].url
    assert url is not None and url.startswith("https://api.fda.gov/")
    assert "apikey" not in url.lower()


def test_the_citation_title_names_the_source_and_its_recency(agent_kit) -> None:
    """§14c: provenance a reader can act on. A label is a point-in-time document, so which version
    was read is part of the claim."""
    response = agent_kit.run(
        LABEL,
        agent_kit.answer(
            answer="Indicated for the example condition. [c1]",
            citations=[{"id": "c1", "row_id": "fda#l1.1", "cells": {"text": agent_kit.FDA_TEXT}}],
        ),
        openfda=agent_kit.openfda_client(),
    )

    assert response.citations[0].title == "openFDA label · Examplor · effective 2024-04-15"


def test_the_trace_says_which_upstream_answered(agent_kit) -> None:
    """The phase's whole question is whether the agent reached live where a vendored row would have
    done. A trace that does not name the tool cannot be graded (§16a)."""
    response = agent_kit.run(
        LABEL,
        agent_kit.answer(
            answer="Indicated for the example condition. [c1]",
            citations=[{"id": "c1", "row_id": "fda#l1.1", "cells": {"text": agent_kit.FDA_TEXT}}],
        ),
        openfda=agent_kit.openfda_client(),
    )

    tools = [step.tool for step in response.trace if step.tool]
    assert "drug_label" in tools


# ---------------------------------------------------------------- the inverse rule ------------


def test_no_recalls_reaches_the_model_as_an_answer(agent_kit) -> None:
    """**The rule this phase adds.** An empty recall list with `unavailable` unset is a finding the
    model can state with confidence, not a failed lookup it must hedge — and it must survive the
    whole path to the agent, not just the client."""
    response = agent_kit.run(
        RECALLS,
        agent_kit.answer(
            answer="The FDA's enforcement database holds no recall for this drug. [c1]",
            # The *search* is the citable evidence for a negative finding. Without this the
            # grounding validator would force an abstention on a question the agent did answer —
            # see `_recall_rows` for why the empty result is still a row.
            citations=[{"id": "c1", "row_id": "fda#r1.0", "cells": {"recalls_found": "0"}}],
            abstained=False,
        ),
        openfda=agent_kit.openfda_client(status=404),
    )

    assert response.abstained is False
    assert response.citations[0].title.endswith("no matches")
    summaries = [s.summary for s in response.trace if s.kind == "tool_result"]
    assert any("no FDA recalls" in s for s in summaries), (
        "the trace must say *no recalls* rather than *0 results* — that is the finding"
    )


def test_an_outage_is_distinguishable_from_an_empty_result(agent_kit) -> None:
    """The other half of the same rule, and the reason both are worth a test: a 503 and a 404 must
    not produce the same thing, or the lane can report an absence it never established."""
    response = agent_kit.run(
        RECALLS,
        agent_kit.answer(
            answer="I could not check the FDA's recall data.", citations=[], abstained=True
        ),
        openfda=agent_kit.openfda_client(status=503),
    )

    summaries = [s.summary for s in response.trace if s.kind == "tool_result"]
    assert any("unavailable" in s for s in summaries)


# ---------------------------------------------------------------- guardrail -------------------


def test_a_record_no_tool_returned_cannot_be_cited(agent_kit) -> None:
    """A model can write `fda#l1.1.1` as easily as any other string. Validity is membership in a
    dictionary the tools alone write."""
    message = agent_kit.expect_rejection(
        LABEL,
        agent_kit.answer(
            answer="Invented. [c1]",
            citations=[{"id": "c1", "row_id": "fda#l9.9.9", "cells": {"text": "invented"}}],
        ),
        openfda=agent_kit.openfda_client(),
    )

    assert "fda#l9.9.9" in message
    assert "no query returned" in message


def test_a_cell_value_must_be_copied_exactly(agent_kit) -> None:
    """Phase 1-c's byte-exact rule, inherited unchanged because a live record is a row."""
    message = agent_kit.expect_rejection(
        LABEL,
        agent_kit.answer(
            answer="Paraphrased. [c1]",
            citations=[
                {"id": "c1", "row_id": "fda#l1.1", "cells": {"text": "roughly what it said"}}
            ],
        ),
        openfda=agent_kit.openfda_client(),
    )

    assert "Copy the cell exactly" in message


# ---------------------------------------------------------------- budget ----------------------


def test_a_spent_budget_is_terminal_and_not_a_retry(agent_kit) -> None:
    """A `ModelRetry` here would spend the grounding guardrail's allowance on a refusal that cannot
    change — the allowance does not refill inside a run. Phase 2 asserts the same for the web lane;
    this is the same rule reaching a second lane."""
    limit = get_config().live.max_calls_per_run
    # **Distinct drug names on purpose.** Repeating one would be answered from this run's cache
    # after the first call and would never reach the ceiling — which is the cache working, not the
    # budget failing, and an earlier version of this test could not tell the two apart.
    turns = [("drug_recalls", {"name": f"drug-{i}"}) for i in range(limit + 1)]
    response = agent_kit.run(
        *turns,
        agent_kit.answer(answer="I have used my lookups.", citations=[], abstained=True),
        openfda=agent_kit.openfda_client(),
    )

    summaries = [s.summary for s in response.trace if s.kind == "tool_result"]
    assert any("unavailable" in s for s in summaries), (
        "the ceiling must bite as a result the model reads, not as a raise"
    )


def test_an_unsupported_section_is_a_retry_naming_the_alternatives(agent_kit) -> None:
    """Unlike a spent budget, this *is* recoverable inside the run — the model picks another value
    and the next call works. A retry is only wasted when it cannot change the outcome."""
    bad = ("drug_label", {"name": "Examplor", "section": "ingredients"})
    message = agent_kit.expect_rejection(bad, bad, bad, openfda=agent_kit.openfda_client())

    assert "section must be one of" in message
    assert all(name in message for name in SECTIONS)


@pytest.mark.parametrize("section", SECTIONS)
def test_every_offered_section_is_one_the_client_can_resolve(section: str) -> None:
    """The vocabulary offered to the model and the mapping the client reads must be the same set. A
    value a model may pick that no field backs is a dead end it cannot see."""
    from health_coverage_navigator.live.models import SECTION_FIELDS

    assert SECTION_FIELDS[section], f"{section} maps to no SPL field"


# ------------------------------------------------------ Marketplace and NPPES ----------------


def test_the_marketplace_tools_need_a_credential_and_the_others_do_not() -> None:
    """The asymmetry §6 records: a missing CMS key removes three of six live tools, not the lane.

    This is the difference from the web lane, where a missing key means the app refuses to answer
    at all. Here two of three sources are keyless, so the honest degradation is a narrower agent
    that is *told* what it cannot do.
    """
    keyless = {t.__name__ for t in select_tools("both", live=True, marketplace=False)}
    assert {"drug_label", "drug_recalls", "lookup_provider"} <= keyless
    assert not ({"find_plans", "check_drug_coverage", "find_drug"} & keyless)


def test_the_prompt_claims_the_marketplace_only_when_it_is_registered() -> None:
    """A prompt that offers `find_plans` without the key spends the whole retry budget discovering
    it is not there."""
    from health_coverage_navigator.agent.prompt import system_prompt

    with_key = system_prompt("both", live=True, marketplace=True)
    without = " ".join(system_prompt("both", live=True, marketplace=False).split())
    assert "find_plans" in with_key
    assert "find_plans" not in without
    assert "no live plan prices or formularies" in without


def test_an_ambiguous_zip_answers_and_says_which_county(agent_kit) -> None:
    """**Answer, then qualify.** The first version raised a `ModelRetry` asking the model to pick,
    and measured behaviour was that it asked the reader instead and abstained on a question it could
    answer. Prices plus "these are for Davidson County" beats a clarifying question."""
    from health_coverage_navigator.agent.live_tools import _choose_county
    from health_coverage_navigator.live.models import County

    counties = [
        County(fips="37057", name="Davidson County", state="NC"),
        County(fips="37151", name="Randolph County", state="NC"),
    ]
    chosen, others = _choose_county(counties, None, "27360")

    assert chosen.fips == "37057"
    assert [c.name for c in others] == ["Randolph County"], (
        "the un-priced counties must travel with the answer, not vanish"
    )


def test_an_explicit_county_still_wins(agent_kit) -> None:
    from health_coverage_navigator.agent.live_tools import _choose_county
    from health_coverage_navigator.live.models import County

    counties = [
        County(fips="37057", name="Davidson County", state="NC"),
        County(fips="37151", name="Randolph County", state="NC"),
    ]
    chosen, others = _choose_county(counties, "37151", "27360")

    assert chosen.fips == "37151"
    assert [c.fips for c in others] == ["37057"]


def test_a_county_that_does_not_belong_to_the_zip_is_still_a_retry(agent_kit) -> None:
    """Still a mistake worth correcting — unlike ambiguity, which is a fact to report."""
    import pytest as _pytest
    from pydantic_ai import ModelRetry

    from health_coverage_navigator.agent.live_tools import _choose_county
    from health_coverage_navigator.live.models import County

    counties = [County(fips="37057", name="Davidson County", state="NC")]
    with _pytest.raises(ModelRetry) as excinfo:
        _choose_county(counties, "99999", "27360")
    assert "not one of the counties" in str(excinfo.value)


def test_one_county_is_chosen_without_ceremony(agent_kit) -> None:
    from health_coverage_navigator.agent.live_tools import _choose_county
    from health_coverage_navigator.live.models import County

    only = County(fips="37057", name="Davidson County", state="NC")
    chosen, others = _choose_county([only], None, "27360")
    assert chosen is only and others == []


def test_a_repeated_lookup_is_answered_from_the_run_cache(agent_kit) -> None:
    """§13c. A compound question that asks about one drug three ways should pay for it once — and
    the trace must still show it asked, because "looked it up twice" and "looked it up once and
    reused it" are different behaviours."""
    response = agent_kit.run(
        RECALLS,
        RECALLS,
        agent_kit.answer(
            answer="No recalls on record. [c1]",
            citations=[{"id": "c1", "row_id": "fda#r1.0", "cells": {"recalls_found": "0"}}],
            abstained=False,
        ),
        openfda=agent_kit.openfda_client(status=404),
    )

    summaries = [s.summary for s in response.trace if s.kind == "tool_result"]
    recall_steps = [s for s in summaries if "recall" in s]
    assert len(recall_steps) == 2, "both calls are traced"
    assert sum("(cached)" in s for s in recall_steps) == 1, "the second is served from cache"


def test_the_reconciliation_rule_reaches_the_model_only_with_both_halves() -> None:
    """§15, and plan.md's "which source is authoritative" checklist item. The rule was written down
    a phase before anything carried it to the model — a capability, not a paragraph."""
    from health_coverage_navigator.agent.prompt import system_prompt

    both = system_prompt("both", structured=True, web=True, live=True, marketplace=True)
    assert "choose by what is being asked" in both
    assert "cite both" in both
    # Meaningless with only one half registered, and a prompt that describes a choice the agent
    # cannot make is the stale-instruction failure this module exists to avoid.
    assert "choose by what is being asked" not in system_prompt("both", structured=True, live=False)
    assert "choose by what is being asked" not in system_prompt("both", structured=False, live=True)


def test_a_prose_cell_is_quoted_from_not_reproduced(agent_kit) -> None:
    """A label section is a passage, not a value. Demanding a byte-exact copy of 2,199 characters
    asks the model to reproduce a page to cite a sentence — the failure that consumed the whole
    retry budget on `live-01` in the first Phase 3 sweep."""
    long_text = "ALPHA. " + ("filler sentence. " * 60) + "OMEGA."
    response = agent_kit.run(
        LABEL,
        agent_kit.answer(
            answer="It is indicated for the example condition. [c1]",
            citations=[{"id": "c1", "row_id": "fda#l1.1", "cells": {"text": "OMEGA."}}],
        ),
        openfda=agent_kit.openfda_client(sections={"indications_and_usage": [long_text]}),
    )

    assert response.citations[0].snippet.strip().endswith("OMEGA.")


def test_a_quote_that_is_not_in_the_prose_cell_is_still_refused(agent_kit) -> None:
    """The relaxation is about *how much* must match, never about whether the words are real."""
    long_text = "ALPHA. " + ("filler sentence. " * 60) + "OMEGA."
    message = agent_kit.expect_rejection(
        LABEL,
        agent_kit.answer(
            answer="Invented. [c1]",
            citations=[
                {"id": "c1", "row_id": "fda#l1.1", "cells": {"text": "words never printed"}}
            ],
        ),
        openfda=agent_kit.openfda_client(sections={"indications_and_usage": [long_text]}),
    )

    assert "does not appear in that cell" in message


def test_a_short_cell_still_demands_an_exact_copy(agent_kit) -> None:
    """The mirror lane's guarantee is untouched: `'$4,500 '` keeps its trailing space."""
    message = agent_kit.expect_rejection(
        RECALLS,
        agent_kit.answer(
            answer="Close enough. [c1]",
            citations=[{"id": "c1", "row_id": "fda#r1.0", "cells": {"recalls_found": "zero"}}],
        ),
        openfda=agent_kit.openfda_client(status=404),
    )

    assert "Copy the cell exactly" in message


def test_the_prompt_says_the_corpus_can_be_stale_about_current_facts() -> None:
    """§15's second failure mode — trusting a static source where only a live one is current.

    Measured: asked which exchange serves ZIP 30076, the agent answered from the reference corpus
    that "Georgia uses HealthCare.gov", which stopped being true, and never called `find_plans`.
    The reconciliation step covered tables-vs-live and said nothing about the corpus.
    """
    from health_coverage_navigator.agent.prompt import system_prompt

    text = " ".join(
        system_prompt("both", structured=True, web=True, live=True, marketplace=True).split()
    )
    assert "describe how things worked when they were written" in text
    assert "is not evidence that nothing has moved since" in text


@pytest.mark.anyio
async def test_live_row_cells_use_names_the_model_was_shown() -> None:
    """**The general guard for a bug this phase hit three times.**

    A citation names a cell by key, and the only names the model has are the field names on the
    result it was handed. When the two drift the model is rejected for naming a column that does not
    exist — while having done the right thing. It happened with `record_id` vs `row_id`, with
    a result-level id that was not the citable one, and with `field` vs `section`.

    So: every cell key a live tool emits must be a field the model can actually see on the
    corresponding result model, or an obvious synonym listed here. Checked structurally rather than
    per-tool, because the next instance will be in whichever tool nobody thought to re-check.
    """
    from health_coverage_navigator.agent.live_tools import _label_rows, _recall_rows
    from health_coverage_navigator.live.models import DrugLabelResult, LabelSection

    label = DrugLabelResult(
        query="x",
        label_found=True,
        requested_section="indications",
        sections=[LabelSection(row_id="fda#l1.1", field="indications_and_usage", text="t")],
        brand_names=["X"],
        effective_time="20240415",
    )
    visible = set(LabelSection.model_fields) | set(DrugLabelResult.model_fields) | {"drug"}
    for row in _label_rows(label):
        unknown = set(row.cells) - visible
        assert not unknown, f"cell key(s) the model never saw: {sorted(unknown)}"

    from health_coverage_navigator.live.models import DrugRecall, DrugRecallResult

    recalls = DrugRecallResult(
        query="x",
        row_id="",
        recalls=[DrugRecall(row_id="fda#r1.1", recall_number="D-1", reason="r")],
    )
    visible_recall = set(DrugRecall.model_fields) | {
        "recall_initiation_date",
        "recalls_found",
        "searched",
        "drug",
    }
    for row in _recall_rows(recalls):
        unknown = set(row.cells) - visible_recall
        assert not unknown, f"cell key(s) the model never saw: {sorted(unknown)}"

    # The other half of the same invariant, and the half that was missing when this test was first
    # written: every row a tool records must have its `row_id` **visible on the result**, or the
    # agent holds an answer it is not allowed to point at. That is what made `find_plans` abstain on
    # a state it had correctly identified as unserved.
    from health_coverage_navigator.agent.live_tools import _plan_rows, _provider_rows

    # Plan rows: every cell key must be a field on `PlanSummary` or on the result that carried it.
    from health_coverage_navigator.live.models import PlanMatches, PlanSummary, ProviderResult

    priced = PlanMatches(
        zipcode="27360",
        year=2026,
        plans=[
            PlanSummary(
                row_id="mkt#p1.1",
                plan_id="77264NC0010049",
                name="A Plan",
                issuer="An Issuer",
                premium=344.5,
                deductible=8450.0,
            )
        ],
    )
    visible_plan = set(PlanSummary.model_fields) | set(PlanMatches.model_fields)
    for row in _plan_rows(priced):
        unknown = set(row.cells) - visible_plan
        assert not unknown, f"cell key(s) the model never saw: {sorted(unknown)}"
    # And the *values* must be spelled as the model saw them, not reformatted.
    assert _plan_rows(priced)[0].cells["premium"] == "344.5"
    assert _plan_rows(priced)[0].cells["deductible"] == "8450.0"

    not_served = PlanMatches(state_not_served="GA", row_id="mkt#s1.GA")
    assert [r.row_id for r in _plan_rows(not_served)] == [not_served.row_id]

    missing = ProviderResult(npi="1000000000", found=False, row_id="npi#1000000000.0")
    assert [r.row_id for r in _provider_rows(missing)] == [missing.row_id]

    for result, rows in ((not_served, _plan_rows(not_served)), (missing, _provider_rows(missing))):
        for row in rows:
            assert row.row_id, "a recorded row with no id is uncitable by construction"
            visible_ids = {
                value
                for name in type(result).model_fields
                if isinstance(value := getattr(result, name, None), str)
            }
            assert row.row_id in visible_ids, (
                "the row's id must be a value the model can read off the result"
            )


# ---------------------------------------------------------------- the invariant ---------------


def _negative_results() -> list[tuple[str, object]]:
    """Every shape a live lookup can come back in having **reached its upstream and found nothing**.

    Written out rather than generated, because the point of the list is that a person had to think
    about each branch — the four that were fixed case by case, and the five that were not.
    """
    from health_coverage_navigator.live.models import (
        CoverageResult,
        DrugLabelResult,
        DrugMatches,
        DrugRecallResult,
        PlanMatches,
        ProviderResult,
    )

    return [
        # The four that were fixed while building Phase 3 (§18c family 1).
        ("no recalls", DrugRecallResult(query="Examplor", row_id="fda#r1.0")),
        ("no such npi", ProviderResult(npi="1000000000", found=False, row_id="npi#1000000000.0")),
        ("malformed npi", ProviderResult(npi="123", invalid="too short", row_id="npi#123.x")),
        ("state not served", PlanMatches(state_not_served="GA", row_id="mkt#s1.GA")),
        # The five this test was written for (docs/negative-finding-gaps.md).
        (
            "no label",
            DrugLabelResult(
                query="nosuchdrug",
                requested_section="indications",
                label_found=False,
                row_id="fda#l1.0",
                source_url="https://api.fda.gov/drug/label.json?search=x",
            ),
        ),
        (
            "label without that section",
            DrugLabelResult(
                query="Examplor",
                requested_section="interactions",
                label_found=True,
                brand_names=["Examplor"],
                row_id="fda#l1.0",
                source_url="https://api.fda.gov/drug/label.json?search=x",
            ),
        ),
        ("no plans matched", PlanMatches(zipcode="27360", year=2026, row_id="mkt#p1.0")),
        ("drug not recognised", DrugMatches(query="lipitorr", row_id="mkt#d1.0")),
        ("no coverage returned", CoverageResult(year=2026, row_id="mkt#c1.0")),
    ]


@pytest.mark.parametrize(
    "label,result", _negative_results(), ids=lambda v: v if isinstance(v, str) else ""
)
def test_a_reached_lookup_is_always_citable(label: str, result: object) -> None:
    """**The invariant the point fixes never became.**

    A lookup that reached its upstream must leave something an answer can cite — whatever it found,
    including nothing. Otherwise the grounding validator forces the model to abstain on a question
    it did answer, or to go and cite a worse source for a fact it already holds authoritatively.
    Both were measured; see `_search_row`.

    Nine shapes, four of which were fixed one at a time before anyone wrote the rule down. This is
    the check that would have caught all nine at once, which is the whole argument for having it:
    *the next instance will be in whichever tool nobody thought to re-check.*
    """
    from health_coverage_navigator.agent.live_tools import rows_for

    rows = rows_for(result)
    assert rows, f"{label}: a finding with nothing to cite forces a false abstention"
    for row in rows:
        assert row.row_id, f"{label}: a recorded row with no id is uncitable by construction"
        # Family 2's half of the same rule: the id must be readable off the result the model was
        # handed, or it holds an answer it is not allowed to point at.
        visible = {
            value
            for name in type(result).model_fields  # type: ignore[attr-defined]
            if isinstance(value := getattr(result, name, None), str)
        }
        assert row.row_id in visible, f"{label}: the model cannot see the id it must cite"


#: Stand-ins by annotation, for building a result with nothing on it but what pydantic insists on.
#: Deliberately not a `defaultdict` — an unmapped type must fail by *name*, telling the next person
#: which field to account for, rather than as a `ValidationError` from three frames down.
_PLACEHOLDERS: dict[object, object] = {str: "x", int: 1, float: 1.0, bool: False}


def _only_required(shape, **overrides):
    """A minimal instance of a live result model: required fields filled, nothing else set.

    Written against `model_fields` rather than against a hand-kept list of constructor arguments,
    which is what the first version did — it named `query` and `npi` because those were required
    *that day*, so a new required field would have failed as an unreadable `ValidationError` rather
    than as the thing that actually changed.
    """
    kwargs: dict[str, object] = {}
    for name, field in shape.model_fields.items():
        if not field.is_required():
            continue
        value = _PLACEHOLDERS.get(field.annotation)
        assert value is not None, (
            f"{shape.__name__}.{name} is required and its type "
            f"({field.annotation}) has no placeholder — add one to _PLACEHOLDERS."
        )
        kwargs[name] = value
    return shape(**{**kwargs, **overrides})


def test_an_outage_stays_uncitable() -> None:
    """The inverse, and the half a well-meaning fix breaks: **an outage is not a finding.**

    Nothing was looked up, so there is nothing to cite. A row here would let the model cite the fact
    that it failed, which is the same defect pointing the other way and a worse one — it turns "I
    could not check" into a sourced claim.
    """
    from health_coverage_navigator.agent.live_tools import _ROW_BUILDERS, rows_for

    for shape in _ROW_BUILDERS:
        assert "unavailable" in shape.model_fields, (
            f"{shape.__name__} has no `unavailable` field, so an outage and an empty result are "
            f"indistinguishable on it — the inverse of the rule this test guards"
        )
        assert rows_for(_only_required(shape, unavailable="upstream unreachable")) == [], (
            f"{shape.__name__} cited an outage"
        )


def test_every_live_tool_has_a_row_builder() -> None:
    """What makes the invariant survive the next tool someone adds.

    The two tests above check the shapes that exist today; this one checks that a *new* shape cannot
    slip in without one. A tool whose result type has no builder cannot record a row at all, so
    everything it establishes would be uncitable — the defect, in its most general form.
    """
    from typing import get_type_hints

    from health_coverage_navigator.agent.live_tools import _ROW_BUILDERS, LIVE_TOOLS

    for tool in LIVE_TOOLS:
        returns = get_type_hints(tool)["return"]
        assert returns in _ROW_BUILDERS, (
            f"{tool.__name__} returns {returns.__name__}, which has no row builder — nothing it "
            f"finds could be cited. Register one in _ROW_BUILDERS."
        )
