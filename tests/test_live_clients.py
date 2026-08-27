"""Tests for `live/openfda.py` — the whole FDA lane below the agent, with no network and no key.

**Every test drives the real client over `httpx.MockTransport`**, so request construction, status
handling, the retry transport and every degradation run for real against canned HTTP responses.

**The two most important tests in this file are the ones asserting that an empty result is an
answer.** openFDA returns HTTP 404 when a search matches nothing (docs/structured-api-tools.md
§10b.1), and for *"has drug X been recalled"* that 404 means **no recalls** — the thing the user
asked. A client that mapped it to `unavailable` would abstain on the question the endpoint exists
to answer, and would look entirely correct doing it. `test_no_recalls_is_an_answer_not_an_outage`
is the guard.

**Fixture bodies are hand-built from responses observed on 2026-08-22**, keeping only the fields
the wrappers read. openFDA content is U.S. government public-domain drug labelling, so unlike the
web lane there is no licensing reason to invent it — but a 255,000-character label is not something
to commit either, and the shapes that matter here are small.
"""

import httpx
import pytest
from synthetic import (
    synthetic_npi,
    synthetic_nppes_response,
    synthetic_provider,
    synthetic_taxonomy,
)

from health_coverage_navigator.config import LiveConfig
from health_coverage_navigator.live.marketplace import MarketplaceClient
from health_coverage_navigator.live.models import County
from health_coverage_navigator.live.nppes import NppesClient
from health_coverage_navigator.live.openfda import OpenFdaClient

CONFIG = LiveConfig(
    max_calls_per_run=8,
    timeout_s=5.0,
    retry_after_cap_s=5.0,
    max_section_chars=100,
    max_recalls=3,
    max_plans=3,
)

DISCLAIMER = "Do not rely on openFDA to make decisions regarding medical care."

#: A stand-in for an openFDA key. Spelled so `scripts/scan_sensitive.py` reads it as a placeholder
#: rather than as a leaked credential — its `PLACEHOLDER_RE` anchors on the *start* of the value, so
#: `fda-test-key` looked real and `test-...` does not. The scanner caught the first spelling, which
#: is the guardrail working on its author for the second time this phase.
FAKE_FDA_KEY = "test-openfda-key-not-a-real-credential"


def build(handler) -> OpenFdaClient:
    """Keyless by construction (`api_key=""`), so these tests assert the same thing on a machine
    that holds an openFDA key and one that does not. Omitting it would read `.env`."""
    return OpenFdaClient.open(config=CONFIG, api_key="", transport=httpx.MockTransport(handler))


def label_body(**record) -> dict:
    """An openFDA label envelope. Field values are lists, because openFDA's are (§10b.2)."""
    return {
        "meta": {"disclaimer": DISCLAIMER},
        "results": [
            {
                "effective_time": "20240415",
                "openfda": {
                    "brand_name": ["Examplor"],
                    "generic_name": ["EXAMPLE SUBSTANCE"],
                    "rxcui": ["111111", "222222"],
                },
                **record,
            }
        ],
    }


def responding(payload: dict | None, status: int = 200):
    """A handler returning one fixed response, recording the requests it saw."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if status == 404:
            return httpx.Response(
                404, json={"error": {"code": "NOT_FOUND", "message": "No matches found!"}}
            )
        return httpx.Response(status, json=payload)

    handler.seen = seen  # type: ignore[attr-defined]
    return handler


# ----------------------------------------------------------------- labels --------------------


@pytest.mark.anyio
async def test_a_label_section_comes_back_citable() -> None:
    client = build(responding(label_body(indications_and_usage=["To treat the example."])))
    result = await client.drug_label("Examplor", "indications")

    assert result.unavailable is None
    assert result.label_found is True
    assert [s.text for s in result.sections] == ["To treat the example."]
    assert result.rxcuis == ["111111", "222222"]
    assert result.effective_time == "20240415"
    assert [s.row_id for s in result.sections] == ["fda#l1.1"]
    assert result.disclaimer == DISCLAIMER


@pytest.mark.anyio
async def test_multi_element_sections_are_joined_not_indexed() -> None:
    """§10b.2's trap: `drug_interactions` really did come back as a two-element list.

    A `[0]` here would silently drop half of a drug-interactions section, which in a health tool is
    a wrong answer rather than a formatting nit.
    """
    client = build(responding(label_body(drug_interactions=["First half.", "Second half."])))
    result = await client.drug_label("Examplor", "interactions")

    assert [s.text for s in result.sections] == ["First half.\n\nSecond half."]


@pytest.mark.anyio
async def test_prescription_and_otc_warnings_both_resolve() -> None:
    """The section-name mapping is one-to-many because SPL is (§10b, verified 2026-08-22).

    A prescription label carries `warnings_and_cautions`; an over-the-counter one carries plain
    `warnings`. One field name per section would return a confident nothing for half the catalogue.
    """
    rx = build(responding(label_body(warnings_and_cautions=["Rx warning."])))
    otc = build(responding(label_body(warnings=["OTC warning."])))

    assert [s.field for s in (await rx.drug_label("X", "warnings")).sections] == [
        "warnings_and_cautions"
    ]
    assert [s.field for s in (await otc.drug_label("X", "warnings")).sections] == ["warnings"]


@pytest.mark.anyio
async def test_a_boxed_warning_is_never_dropped_for_a_general_one() -> None:
    """Both are returned, boxed first. Picking one would discard the FDA's most serious class."""
    client = build(
        responding(label_body(boxed_warning=["BOXED."], warnings_and_cautions=["General."]))
    )
    result = await client.drug_label("Examplor", "warnings")

    assert [s.field for s in result.sections] == ["boxed_warning", "warnings_and_cautions"]


@pytest.mark.anyio
async def test_a_missing_section_is_not_a_missing_label() -> None:
    """`label_found=True` with no sections: the label exists and does not carry this one."""
    # Imported inside the test: this file keeps PydanticAI out of its import graph, and
    # `agent.live_tools` pulls it in.
    from health_coverage_navigator.agent.live_tools import rows_for

    client = build(responding(label_body(indications_and_usage=["Something."])))
    result = await client.drug_label("Examplor", "interactions")

    assert result.label_found is True
    assert result.sections == []
    assert result.unavailable is None
    # And it is citable: "the label exists and does not carry this section" is a finding, and the
    # model is told to report it. Without a row the grounding validator forces an abstention on it.
    rows = rows_for(result)
    assert [r.row_id for r in rows] == [result.row_id] != [""]
    assert rows[0].cells["sections_found"] == "0"


@pytest.mark.anyio
async def test_a_brand_miss_retries_against_the_generic_name() -> None:
    """Users type generic names. Searching only the brand field would answer "no such drug"."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        search = request.url.params.get("search", "")
        calls.append(search)
        if "brand_name" in search:
            return httpx.Response(404, json={"error": {"code": "NOT_FOUND"}})
        return httpx.Response(200, json=label_body(indications_and_usage=["Found generically."]))

    result = await build(handler).drug_label("example substance", "indications")

    assert len(calls) == 2
    assert "brand_name" in calls[0] and "generic_name" in calls[1]
    assert result.label_found is True


@pytest.mark.anyio
async def test_no_label_at_all_is_an_answer_not_an_outage() -> None:
    # Imported inside the test: this file keeps PydanticAI out of its import graph, and
    # `agent.live_tools` pulls it in.
    from health_coverage_navigator.agent.live_tools import rows_for

    result = await build(responding(None, status=404)).drug_label("nosuchdrug", "indications")

    assert result.unavailable is None, "a 404 from openFDA means no match, never an outage"
    assert result.label_found is False
    # Two searches — brand, then generic — found nothing, and that finding is citable.
    rows = rows_for(result)
    assert [r.row_id for r in rows] == [result.row_id] != [""]
    assert rows[0].cells["query"] == "nosuchdrug"


@pytest.mark.anyio
async def test_an_oversized_section_is_marked_truncated_not_silently_cut() -> None:
    client = build(responding(label_body(warnings=["W" * (CONFIG.max_section_chars + 50)])))
    section = (await client.drug_label("Examplor", "warnings")).sections[0]

    assert section.truncated is True
    assert len(section.text) == CONFIG.max_section_chars


# ----------------------------------------------------------------- recalls -------------------


def recall_body(*records: dict, total: int | None = None) -> dict:
    return {
        "meta": {"disclaimer": DISCLAIMER, "results": {"total": total or len(records)}},
        "results": list(records),
    }


RECALL = {
    "recall_number": "D-070-2013",
    "classification": "Class II",
    "status": "Terminated",
    "reason_for_recall": "Presence of particulate matter.",
    "product_description": "Example Tablets, 10 mg",
    "recalling_firm": "Example Pharma Inc.",
    "distribution_pattern": "Nationwide",
    "recall_initiation_date": "20121109",
}


@pytest.mark.anyio
async def test_no_recalls_is_an_answer_not_an_outage() -> None:
    """**The most important test in this file.**

    openFDA answers "nothing matched" with a 404. For *"has drug X been recalled"* that is the
    answer the user wanted — no recalls — and mapping it to `unavailable` would abstain on the one
    question this endpoint exists to answer, while looking perfectly correct.
    """
    result = await build(responding(None, status=404)).drug_recalls("Examplor")

    assert result.unavailable is None
    assert result.recalls == []
    assert result.total_matching == 0


@pytest.mark.anyio
async def test_recalls_carry_the_three_fields_that_change_the_answer() -> None:
    """Class, status and product description. Omitting any of them misleads — see the tool's own
    description for why each one does."""
    result = await build(responding(recall_body(RECALL, total=44))).drug_recalls("Examplor")

    recall = result.recalls[0]
    assert recall.classification == "Class II"
    assert recall.status == "Terminated"
    assert recall.product_description == "Example Tablets, 10 mg"
    assert recall.recall_initiation_date == "20121109"
    assert result.total_matching == 44, "the count of all matches, not just those returned"


@pytest.mark.anyio
async def test_recall_ids_are_numbered_within_a_call() -> None:
    result = await build(responding(recall_body(RECALL, RECALL))).drug_recalls("X", sequence=2)

    assert [r.row_id for r in result.recalls] == ["fda#r2.1", "fda#r2.2"]


@pytest.mark.anyio
async def test_the_recall_cap_reaches_the_request() -> None:
    handler = responding(recall_body(RECALL))
    await build(handler).drug_recalls("Examplor")

    params = handler.seen[0].url.params  # type: ignore[attr-defined]
    assert params.get("limit") == str(CONFIG.max_recalls)
    assert params.get("sort") == "recall_initiation_date:desc"


# ----------------------------------------------------------------- failures ------------------


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, "credentials"),
        (429, "rate-limiting"),
        (500, "temporarily unavailable"),
        (503, "temporarily unavailable"),
    ],
)
async def test_every_http_failure_degrades_honestly(status: int, expected: str) -> None:
    """One test per row of §13a's failure table. None of these raises, and none of them is a 404."""
    result = await build(responding({}, status=status)).drug_recalls("Examplor")

    assert result.unavailable is not None
    assert expected in result.unavailable
    assert result.recalls == []


@pytest.mark.anyio
async def test_a_timeout_degrades_rather_than_raising() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    result = await build(handler).drug_label("Examplor", "indications")

    assert result.unavailable is not None
    assert "did not respond in time" in result.unavailable


@pytest.mark.anyio
async def test_a_connection_failure_degrades_rather_than_raising() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    result = await build(handler).drug_recalls("Examplor")

    assert result.unavailable is not None
    assert "could not be reached" in result.unavailable


@pytest.mark.anyio
async def test_an_unreadable_body_degrades_rather_than_raising() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not json</html>")

    result = await build(handler).drug_label("Examplor", "indications")

    assert result.unavailable is not None
    assert "could not read" in result.unavailable


@pytest.mark.anyio
async def test_an_unavailable_result_never_claims_an_absence() -> None:
    """The wording matters as much as the flag: this text lands in the model's context, and the
    failure it guards against is a reader hearing "no recalls" when nothing was looked up."""
    result = await build(responding({}, status=503)).drug_recalls("Examplor")

    assert result.unavailable is not None
    assert "do not report an absence" in result.unavailable


@pytest.mark.anyio
async def test_the_citation_url_is_public_and_refetchable() -> None:
    """§14b. openFDA takes no credential, so unlike the Marketplace this URL carries nothing that
    has to be stripped before a reader or an eval run file sees it."""
    client = build(responding(label_body(indications_and_usage=["Text."])))
    result = await client.drug_label("Examplor", "indications")

    assert result.source_url.startswith("https://api.fda.gov/drug/label.json?")
    assert "apikey" not in result.source_url.lower()


# ------------------------------------------------------------- Marketplace -------------------


def marketplace(handler) -> MarketplaceClient:
    return MarketplaceClient.open(
        config=CONFIG, api_key="test-key", transport=httpx.MockTransport(handler)
    )


def json_handler(payload, status: int = 200):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=payload)

    handler.seen = seen  # type: ignore[attr-defined]
    return handler


@pytest.mark.anyio
async def test_drugs_autocomplete_parses_a_bare_array() -> None:
    """§7a: the published spec declares `{drugs: [...]}` and the server sends a bare array. A model
    generated from the spec would fail on the first real call."""
    # Imported inside the test: this file keeps PydanticAI out of its import graph, and
    # `agent.live_tools` pulls it in.
    from health_coverage_navigator.agent.live_tools import rows_for

    client = marketplace(
        json_handler([{"rxcui": "617318", "name": "LIPITOR", "strength": "20 mg"}])
    )
    result = await client.find_drug("lipitor")

    assert [m.rxcui for m in result.matches] == ["617318"]
    assert result.unavailable is None
    # A resolved drug is citable too: "I checked the 20 mg tablet" is a claim about this result.
    assert [r.row_id for r in rows_for(result)] == [m.row_id for m in result.matches] != [""]


@pytest.mark.anyio
async def test_an_unrecognised_drug_name_is_a_citable_finding() -> None:
    """An empty autocomplete means the Marketplace does not know that name — usually a misspelling,
    and the tool tells the model to say so. A real answer needs a row behind it."""
    # Imported inside the test: this file keeps PydanticAI out of its import graph, and
    # `agent.live_tools` pulls it in.
    from health_coverage_navigator.agent.live_tools import rows_for

    result = await marketplace(json_handler([])).find_drug("lipitorr")

    assert result.matches == []
    rows = rows_for(result)
    assert [r.row_id for r in rows] == [result.row_id] != [""]
    assert rows[0].cells["query"] == "lipitorr"


@pytest.mark.anyio
async def test_a_plan_search_that_matched_nothing_is_a_citable_finding() -> None:
    """The fourth state `PlanMatches` documents. Its `state_not_served` sibling has been citable
    since §14a-bis; this branch was the same hole one `if` away."""
    # Imported inside the test: this file keeps PydanticAI out of its import graph, and
    # `agent.live_tools` pulls it in.
    from health_coverage_navigator.agent.live_tools import rows_for

    result = await marketplace(json_handler({"plans": [], "total": 0})).find_plans(
        county=County(fips="37057", name="Davidson County", state="NC"),
        zipcode="27360",
        ages=[40],
        income=50000,
        year=2026,
        limit=3,
    )

    assert result.plans == []
    assert result.state_not_served is None
    rows = rows_for(result)
    assert [r.row_id for r in rows] == [result.row_id] != [""]
    assert rows[0].cells["zipcode"] == "27360"


@pytest.mark.anyio
async def test_an_empty_coverage_envelope_is_a_citable_finding() -> None:
    """`DataNotProvided` arrives as a row and is citable already. This is the residue: the
    Marketplace returned nothing at all for these drugs and plans."""
    # Imported inside the test: this file keeps PydanticAI out of its import graph, and
    # `agent.live_tools` pulls it in.
    from health_coverage_navigator.agent.live_tools import rows_for

    result = await marketplace(json_handler({"coverage": []})).check_drug_coverage(
        ["617318"], ["77264NC0010049"], 2026
    )

    assert result.coverage == []
    assert [r.row_id for r in rows_for(result)] == [result.row_id] != [""]


@pytest.mark.anyio
async def test_generic_covered_is_not_not_covered() -> None:
    """**The most consequential correctness detail in the phase** (§7b). `generic_rxcui` appears
    exactly when the plan covers the generic and not the brand — reporting that as "not covered"
    sends a reader away from a drug they can actually get."""
    client = marketplace(
        json_handler(
            {
                "coverage": [
                    {
                        "rxcui": "262095",
                        "plan_id": "77264NC0010049",
                        "coverage": "GenericCovered",
                        "generic_rxcui": "259255",
                    }
                ]
            }
        )
    )
    result = await client.check_drug_coverage(["262095"], ["77264NC0010049"], 2026)

    item = result.coverage[0]
    assert item.coverage == "GenericCovered"
    assert item.generic_rxcui == "259255"


@pytest.mark.anyio
async def test_a_plain_covered_row_carries_no_generic() -> None:
    """The other half of the conditional: absent, not null-and-usually-present."""
    client = marketplace(
        json_handler({"coverage": [{"rxcui": "1", "plan_id": "p", "coverage": "Covered"}]})
    )
    result = await client.check_drug_coverage(["1"], ["p"], 2026)

    assert result.coverage[0].generic_rxcui is None


@pytest.mark.anyio
async def test_the_api_key_never_reaches_a_citation_url() -> None:
    """**§14b's leak guard — the one test here whose failure is a security finding.** The key rides
    in the query string, and these URLs are rendered in a browser and written to eval run files."""
    client = marketplace(
        json_handler({"coverage": [{"rxcui": "1", "plan_id": "p", "coverage": "Covered"}]})
    )
    result = await client.check_drug_coverage(["1"], ["p"], 2026)

    url = result.coverage[0].source_url
    assert "test-key" not in url
    assert "apikey" not in url.lower()


@pytest.mark.anyio
async def test_a_state_that_runs_its_own_exchange_is_an_answer() -> None:
    """§10d. CMS serves only the states that use HealthCare.gov; the rest come back 400. That is
    not an outage and not "no plans available" — the reader is in the wrong place."""
    client = marketplace(
        json_handler({"message": "state is not a valid marketplace state"}, status=400)
    )
    result = await client.find_plans(
        county=County(fips="13121", name="Fulton County", state="GA"),
        zipcode="30076",
        ages=[40],
        income=50000,
        year=2026,
        limit=3,
    )

    assert result.state_not_served == "GA"
    assert result.unavailable is None, "a state-based exchange is not a failure of anything"


@pytest.mark.anyio
async def test_a_rejected_key_says_the_key_and_not_the_api() -> None:
    """§3's 60-day expiry makes this the likely Marketplace failure eventually, and it is the one
    an operator can fix in a minute — but only if the message points at the credential."""
    client = marketplace(
        json_handler({"message": "Invalid authentication credentials"}, status=401)
    )
    result = await client.find_drug("lipitor")

    assert result.unavailable is not None
    assert "key" in result.unavailable and "expired" in result.unavailable


@pytest.mark.anyio
async def test_a_zip_spanning_two_counties_returns_both() -> None:
    """Premiums are set per county, so which one is not a wrapper's choice to make silently."""
    client = marketplace(
        json_handler(
            {
                "counties": [
                    {"fips": "13089", "name": "DeKalb County", "state": "GA"},
                    {"fips": "13121", "name": "Fulton County", "state": "GA"},
                ]
            }
        )
    )
    counties, unavailable = await client.counties_for_zip("30341")

    assert unavailable == ""
    assert [c.name for c in counties] == ["DeKalb County", "Fulton County"]


# ----------------------------------------------------------------- NPPES ---------------------


def nppes(handler) -> NppesClient:
    return NppesClient.open(config=CONFIG, transport=httpx.MockTransport(handler))


@pytest.mark.anyio
async def test_a_provider_lookup_reports_the_primary_specialty_first() -> None:
    """§10a.2's trap. A provider may hold several taxonomies with exactly one primary, and
    `taxonomies[0]` from upstream is not reliably it."""
    record = synthetic_provider(
        1,
        taxonomies=[
            synthetic_taxonomy("Internal Medicine", primary=False),
            synthetic_taxonomy("Cardiology", primary=True),
        ],
    )
    client = nppes(json_handler(synthetic_nppes_response(record)))
    result = await client.lookup_npi(record["number"])

    assert result.found is True
    assert result.provider is not None
    assert result.provider.taxonomies[0].description == "Cardiology"
    assert result.provider.taxonomies[0].primary is True


@pytest.mark.anyio
async def test_no_such_npi_is_an_answer_not_an_outage() -> None:
    """NPPES says it with `result_count: 0` and an HTTP **200**, where openFDA uses a 404. Two
    conventions in one phase, which is why neither client shares an "is this empty" helper."""
    client = nppes(json_handler(synthetic_nppes_response()))
    result = await client.lookup_npi(synthetic_npi(9))

    assert result.unavailable is None
    assert result.found is False


@pytest.mark.anyio
async def test_a_malformed_npi_is_reported_as_a_bad_input() -> None:
    """NPPES reports it inside a 200. Telling the reader their number is wrong is more useful than
    reporting an outage — and it is a different state from "no such provider"."""
    client = nppes(
        json_handler({"Errors": [{"description": "The NPI must be 10 digits"}], "result_count": 0})
    )
    result = await client.lookup_npi("123")

    assert result.invalid is not None
    assert result.found is False
    assert result.unavailable is None


@pytest.mark.anyio
async def test_an_organization_gets_a_name_too() -> None:
    """An `NPI-2` carries `organization_name` and no personal name. Reading only the personal fields
    would return an empty name for every hospital in the registry."""
    record = synthetic_provider(3, enumeration_type="NPI-2")
    record["basic"] = {"organization_name": "EXAMPLE HEALTH SYSTEM", "status": "A"}
    client = nppes(json_handler(synthetic_nppes_response(record)))
    result = await client.lookup_npi(record["number"])

    assert result.provider is not None
    assert result.provider.name == "EXAMPLE HEALTH SYSTEM"


@pytest.mark.anyio
async def test_the_openfda_key_reaches_the_request_when_configured() -> None:
    """openFDA spells it `api_key`; the Marketplace spells it `apikey`. Two adjacent tools in one
    lane, two spellings — a shared auth helper guessing from the hostname would get one wrong."""
    handler = responding(label_body(indications_and_usage=["Text."]))
    client = OpenFdaClient.open(
        config=CONFIG, api_key=FAKE_FDA_KEY, transport=httpx.MockTransport(handler)
    )
    await client.drug_label("Examplor", "indications")

    params = handler.seen[0].url.params  # type: ignore[attr-defined]
    assert params.get("api_key") == FAKE_FDA_KEY
    assert "apikey" not in params


@pytest.mark.anyio
async def test_keyless_stays_a_supported_configuration() -> None:
    """A missing openFDA key costs headroom, not capability — unlike the Marketplace key, whose
    absence removes tools. So there is no `OpenFdaNotConfiguredError` and no request parameter."""
    handler = responding(label_body(indications_and_usage=["Text."]))
    # `""`, not omitted: omitting it reads the environment, and this must assert the keyless path
    # on a machine that happens to hold a key just as reliably as on one that does not.
    client = OpenFdaClient.open(config=CONFIG, api_key="", transport=httpx.MockTransport(handler))
    result = await client.drug_label("Examplor", "indications")

    assert result.label_found is True
    assert "api_key" not in handler.seen[0].url.params  # type: ignore[attr-defined]


@pytest.mark.anyio
async def test_the_openfda_key_never_reaches_a_citation_url() -> None:
    """§14b's leak guard, now that openFDA has a credential too. The key is added inside `_fetch`
    and never enters the dict `_url` reads, so there is nothing to remember to strip."""
    handler = responding(label_body(indications_and_usage=["Text."]))
    client = OpenFdaClient.open(
        config=CONFIG, api_key=FAKE_FDA_KEY, transport=httpx.MockTransport(handler)
    )
    result = await client.drug_label("Examplor", "indications")

    assert FAKE_FDA_KEY not in result.source_url
    assert "api_key" not in result.source_url


@pytest.mark.anyio
async def test_the_recall_query_is_a_valid_disjunction() -> None:
    """**The test that would have caught the worst bug of this phase.**

    `drug_recalls` searched `brand_name:"x"+OR+generic_name:"x"` with a literal `+`. openFDA writes
    disjunction as `+OR+` where `+` *is* the encoding of a space, so the literal got encoded again
    to `%2B`, the term became unparseable, openFDA matched nothing, and the client reported **"no
    recalls" for a drug with 44 of them** — a false negative on a safety question, from the one
    endpoint whose whole purpose is that an empty result can be trusted.

    Every other test in this file was blind to it: a `MockTransport` returns its canned body however
    nonsensical the query, so parsing tests pass against a request no real server would honour. This
    asserts the *outgoing* request instead, which is the only thing a mock cannot fake.
    """
    handler = responding(recall_body(RECALL))
    await build(handler).drug_recalls("atorvastatin")

    raw = str(handler.seen[0].url)  # type: ignore[attr-defined]
    assert "%2BOR%2B" not in raw, "a literal '+' double-encodes and matches nothing"
    assert "+OR+" in raw, "openFDA spells disjunction with spaces around OR"
    search = handler.seen[0].url.params["search"]  # type: ignore[attr-defined]
    assert search == 'openfda.brand_name:"atorvastatin" OR openfda.generic_name:"atorvastatin"'


@pytest.mark.anyio
async def test_a_not_served_state_is_a_citable_finding() -> None:
    """The third negative-finding hole in one phase. Without a row behind it the agent had the
    authoritative answer from CMS and cited a web page for it instead."""
    from health_coverage_navigator.agent.live_tools import _plan_rows

    client = marketplace(
        json_handler({"message": "state is not a valid marketplace state"}, status=400)
    )
    result = await client.find_plans(
        county=County(fips="13121", name="Fulton County", state="GA"),
        zipcode="30076",
        ages=[40],
        income=50000,
        year=2026,
        limit=3,
    )
    rows = _plan_rows(result)

    assert len(rows) == 1
    assert rows[0].cells["state_not_served"] == "GA"
    assert rows[0].cells["zipcode"] == "30076"
