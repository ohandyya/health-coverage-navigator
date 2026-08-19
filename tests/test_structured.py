"""Tests for the relational lane's engine — `structured/catalog.py` and `structured/store.py`.

No model, no key, no network: this whole file exercises DuckDB over a fixture mirror built from the
**committed sample slices**, so it runs on a fresh clone where the real (git-ignored) mirror does
not exist. That is also why the fixture is real CMS bytes rather than hand-written rows — the
shapes that make this data awkward (a trailing space inside `'$4,500 '`, a blank `STATE` on a
standalone drug plan, an empty string that is not a null) are exactly the ones a synthetic fixture
would smooth away.

The most important test here is `test_the_sandbox_is_shut`. Everything else checks that a query
behaves; that one checks that a *hostile* query cannot, and it is the file that backs
docs/relational-tool.md §4's claim that the blast radius of model-written SQL is `data/processed/`,
read-only.
"""

import asyncio
import json
from pathlib import Path

import duckdb
import pytest

from health_coverage_navigator.agent.tools import select_tools
from health_coverage_navigator.structured.catalog import (
    StructuredNotBuiltError,
    StructuredStaleError,
    discover,
    partition_year,
    resolve_partition,
)
from health_coverage_navigator.structured.store import QueryRejected, StructuredStore

# --------------------------------------------------------------------------------------------
# The guard
# --------------------------------------------------------------------------------------------


def test_the_sandbox_is_shut(structured_store: StructuredStore) -> None:
    """The escape hatches, proven shut — the point of the three settings in `StructuredStore.open`.

    These are run against the connection directly rather than through `query()`, because `query()`
    would reject most of them at the statement layer and this test is about the *second* layer. If
    it ever starts passing, model-written SQL can read and write the filesystem.
    """
    con = structured_store._con  # noqa: SLF001 - the test is specifically about the connection's state

    for sql in (
        "SELECT * FROM read_csv('/etc/passwd')",
        "COPY (SELECT 1) TO '/tmp/escape.csv'",
        "ATTACH '/tmp/escape.db' AS escaped",
        "INSTALL httpfs",
        "SET memory_limit='8GB'",
        "SET enable_external_access=true",
    ):
        with pytest.raises(duckdb.Error):
            con.execute(sql)


def test_registered_views_still_read_after_the_lock(structured_store: StructuredStore) -> None:
    """The other half of the sandbox: locking must not lock *us* out.

    Views are lazy, so this is the assertion that the ordering in `open()` (views, then the lock)
    is the right way round. Reversed, every query fails with a permission error at read time.
    """
    result = structured_store._con.execute(  # noqa: SLF001
        "SELECT COUNT(*) FROM exchange_puf.plan_attributes_2026"
    ).fetchone()
    assert result is not None and result[0] > 0


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1; SELECT 2",
        "COPY (SELECT 1) TO 'x.csv'",
        "CREATE TABLE t AS SELECT 1",
        "ATTACH 'x.db'",
        "INSTALL httpfs",
        "DELETE FROM exchange_puf.plan_attributes_2026",
        "SELECT (((",
    ],
)
async def test_only_a_single_select_survives_the_statement_guard(
    structured_store: StructuredStore, sql: str
) -> None:
    with pytest.raises(QueryRejected):
        await structured_store.query(sql, plan_year=2026)


async def test_a_cte_and_a_join_are_allowed(structured_store: StructuredStore) -> None:
    """The guard must not be so blunt that it blocks real queries: both of these are `SELECT`s."""
    cte = await structured_store.query(
        "WITH silver AS (SELECT * FROM exchange_puf.plan_attributes_2026 "
        "WHERE MetalLevel = 'Silver') SELECT COUNT(*) AS n FROM silver",
        plan_year=2026,
    )
    assert cte.row_count == 1

    joined = await structured_store.query(
        "SELECT p.PLAN_NAME, f.TIER_LEVEL_VALUE "
        "FROM part_d_spuf.plan_information_2026Q2 p "
        "JOIN part_d_spuf.basic_drugs_formulary_2026Q2 f USING (FORMULARY_ID) LIMIT 3",
        plan_year=2026,
    )
    assert joined.row_count == 3
    # A join is about both tables, and the row id says so.
    assert "basic_drugs_formulary+plan_information" in joined.rows[0].row_id


async def test_a_slow_query_is_interrupted(structured_kit, mirror: Path) -> None:
    """DuckDB has no statement timeout, so this proves the `threading.Timer` + `interrupt()` path.

    The query is a generated cross product rather than anything over the mirror: real lookups run
    in milliseconds, and a test needs work that is unambiguously longer than the deadline.
    """
    impatient = structured_kit.open(
        mirror, config=structured_kit.CONFIG.model_copy(update={"query_timeout_s": 0.25})
    )
    try:
        with pytest.raises(QueryRejected, match="cancelled"):
            await impatient.query(
                "SELECT COUNT(DISTINCT md5(i::VARCHAR)) FROM generate_series(1, 200000000) t(i)",
                plan_year=2026,
            )
        # The connection survives its own interrupt — otherwise one bad query poisons the session.
        after = await impatient.query("SELECT 1 AS ok", plan_year=2026)
        assert after.rows[0].cells == {"ok": "1"}
    finally:
        impatient.close()


async def test_the_row_cap_is_applied_and_reported(structured_store: StructuredStore) -> None:
    """The cap is wrapped around the model's statement, so it cannot be forgotten."""
    result = await structured_store.query(
        "SELECT * FROM exchange_puf.plan_attributes_2026", limit=5
    )
    assert result.row_count == 5
    assert result.truncated is True
    assert len(result.rows) == 5

    exact = await structured_store.query("SELECT 1 AS n", limit=5)
    assert exact.truncated is False


# --------------------------------------------------------------------------------------------
# Partitions and the plan year
# --------------------------------------------------------------------------------------------


def test_partition_year_reads_both_shapes() -> None:
    assert partition_year("2026") == 2026
    assert partition_year("2026Q2") == 2026


def test_resolve_partition_prefers_the_newest_and_never_crosses_years() -> None:
    assert resolve_partition(["2026Q1", "2026Q2"], 2026) == "2026Q2"
    assert resolve_partition(["2025", "2026"], None) == "2026"
    # The whole point: no fallback to an adjacent year.
    assert resolve_partition(["2026"], 2025) is None


async def test_an_unvendored_plan_year_is_refused_with_what_is_available(
    structured_store: StructuredStore,
) -> None:
    with pytest.raises(QueryRejected, match="2026"):
        await structured_store.describe("exchange_puf.plan_attributes", plan_year=2019)
    assert structured_store.resolve(2019) == {}


async def test_a_query_against_another_years_table_is_refused(structured_kit, mirror: Path) -> None:
    """`plan_year` is a rule, not a suggestion.

    Two partitions are vendored here; pinning 2026 and naming the 2025 view must be corrected
    before it runs. Without this, the most authoritative-looking number in the answer — a dollar
    figure — can come from a year nobody asked about.
    """
    two_years = structured_kit.build(
        mirror.parent / "two-years", partitions={"exchange_puf": ["2025", "2026"]}
    )
    store = structured_kit.open(two_years)
    try:
        assert store.resolve(2025)["exchange_puf"] == "2025"
        with pytest.raises(QueryRejected, match="plan_attributes_2026"):
            await store.query(
                "SELECT * FROM exchange_puf.plan_attributes_2025 LIMIT 1", plan_year=2026
            )
    finally:
        store.close()


def test_the_overview_reports_only_the_pinned_partition(structured_store: StructuredStore) -> None:
    overview = structured_store.overview(plan_year=2026)
    assert overview.partitions == {"exchange_puf": "2026", "part_d_spuf": "2026Q2"}
    assert {t.table for t in overview.tables} >= {
        "exchange_puf.plan_attributes",
        "part_d_spuf.basic_drugs_formulary",
    }
    assert all(t.view.endswith(t.partition) for t in overview.tables)
    assert overview.available_partitions["exchange_puf"] == ["2026"]


# --------------------------------------------------------------------------------------------
# Values: the mirror is lossless, and the macros do not change that
# --------------------------------------------------------------------------------------------


async def test_cells_are_returned_byte_for_byte(structured_store: StructuredStore) -> None:
    """A cell reaches the model exactly as CMS published it, trailing space and all.

    This is what the grounding validator compares against, so any normalisation here would make a
    citation unverifiable — or, worse, verifiable against something the source does not say.
    """
    result = await structured_store.query(
        "SELECT TEHBDedInnTier1Individual AS d FROM exchange_puf.plan_attributes_2026 "
        "WHERE TEHBDedInnTier1Individual LIKE '$%' LIMIT 1",
        plan_year=2026,
    )
    value = result.rows[0].cells["d"]
    assert value is not None and value.startswith("$")
    assert value != value.strip(), "the mirror's trailing space must survive to the model"


async def test_null_and_empty_string_stay_different(structured_store: StructuredStore) -> None:
    """In this data `''` is a published value and NULL is not. Collapsing them loses the answer."""
    result = await structured_store.query("SELECT '' AS blank, NULL AS missing", plan_year=2026)
    assert result.rows[0].cells == {"blank": "", "missing": None}


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("to_usd('$450 ')", "450.0"),
        ("to_usd('$1,200.50')", "1200.5"),
        ("to_usd('Not Applicable')", None),
        ("to_usd('')", None),
        ("to_pct('70.88%')", "70.88"),
    ],
)
async def test_the_macros_parse_publisher_formatting(
    structured_store: StructuredStore, expression: str, expected: str | None
) -> None:
    result = await structured_store.query(f"SELECT {expression} AS v", plan_year=2026)
    assert result.rows[0].cells["v"] == expected


# --------------------------------------------------------------------------------------------
# describe_table
# --------------------------------------------------------------------------------------------


async def test_describe_reports_values_not_types(structured_store: StructuredStore) -> None:
    described = await structured_store.describe("exchange_puf.plan_attributes", plan_year=2026)
    by_name = {c.name: c for c in described.columns}

    assert described.total_columns > MANY_COLUMNS
    assert described.truncated is True, "a 151-column table must not be dumped whole"
    assert by_name["MetalLevel"].common, "a low-cardinality column carries its value distribution"
    assert any(c.examples for c in described.columns)
    assert described.notes, "plan_attributes carries the CSR-variant warning"


async def test_describe_narrows_by_contains_and_suggests_a_macro(
    structured_store: StructuredStore,
) -> None:
    """The 36-MOOP-column problem, and the answer to it."""
    described = await structured_store.describe(
        "exchange_puf.plan_attributes", plan_year=2026, contains="MOOP"
    )
    assert described.columns and all("MOOP" in c.name for c in described.columns)
    assert described.truncated is False
    assert any(c.macro == "to_usd" for c in described.columns)


async def test_describe_accepts_a_view_name_too(structured_store: StructuredStore) -> None:
    """`list_tables` prints view names, so the model will have one in front of it."""
    described = await structured_store.describe("part_d_spuf.basic_drugs_formulary_2026Q2")
    assert described.table == "part_d_spuf.basic_drugs_formulary"
    assert described.notes


async def test_describe_rejects_an_unknown_table(structured_store: StructuredStore) -> None:
    with pytest.raises(QueryRejected, match="no table named"):
        await structured_store.describe("exchange_puf.premiums")


# --------------------------------------------------------------------------------------------
# Not built, and half built
# --------------------------------------------------------------------------------------------


def test_an_empty_tree_is_not_built(structured_kit, tmp_path: Path) -> None:
    with pytest.raises(StructuredNotBuiltError, match="make puf"):
        discover(tmp_path / "processed", tmp_path / "raw")


def test_a_missing_table_is_stale_not_merely_incomplete(structured_kit, tmp_path: Path) -> None:
    """Half a mirror answers every query, plausibly, from the rows that happened to arrive."""
    broken = structured_kit.build(tmp_path / "broken")
    (broken / "processed" / "exchange_puf" / "2026" / "service_area.parquet").unlink()
    with pytest.raises(StructuredStaleError, match="service_area"):
        discover(broken / "processed", broken / "raw")


def test_a_row_count_that_disagrees_with_the_catalog_is_stale(
    structured_kit, tmp_path: Path
) -> None:
    doctored = structured_kit.build(tmp_path / "doctored")
    catalog = doctored / "raw" / "exchange_puf" / "catalog.json"
    entries = json.loads(catalog.read_text(encoding="utf-8"))
    entries[0]["row_count"] = int(entries[0]["row_count"]) + 1
    catalog.write_text(json.dumps(entries), encoding="utf-8")

    with pytest.raises(StructuredStaleError, match="catalog.json"):
        structured_kit.open(doctored)


#: `plan_attributes` has 151 columns; the describe cap is 60. Written as a constant so the
#: assertion says what it means rather than repeating a magic number.
MANY_COLUMNS = 60


# --------------------------------------------------------------------------------------------
# Through the agent: the tools, and the grounding guardrail over rows
# --------------------------------------------------------------------------------------------
#
# These drive the whole loop with a scripted model (no provider, no key) to check the parts that
# only exist *inside a run*: that a query makes its rows citable, and that the validator rejects a
# citation the rows do not support. The store is the same sample-built fixture as above.

#: A query the sample mirror answers, and the row it returns. Alaska's plan attributes are in the
#: committed slice, so this is a real CMS row rather than a fabricated one.
DEDUCTIBLE_SQL = (
    "SELECT StandardComponentId, CSRVariationType, TEHBDedInnTier1Individual "
    "FROM exchange_puf.plan_attributes_2026 "
    "WHERE TEHBDedInnTier1Individual LIKE '$%' LIMIT 1"
)


def _first_row(store: StructuredStore) -> tuple[str, dict[str, str | None]]:
    """The row the scripted query will return, fetched here so the test can name its cells.

    `asyncio.run` at a **test boundary** is the sanctioned use (CLAUDE.md's async rules): these
    tests are synchronous because `AgentKit.run` is, and it is synchronous because the agent's own
    entry point is where the loop belongs.
    """
    result = asyncio.run(store.query(DEDUCTIBLE_SQL, plan_year=2026, sequence=1))
    return result.rows[0].row_id, result.rows[0].cells


def _query_turn(sql: str = DEDUCTIBLE_SQL) -> tuple[str, dict[str, object]]:
    return ("query_structured", {"sql": sql, "limit": None})


def test_the_structured_tools_are_registered_only_when_asked() -> None:
    """The lane is a separate axis from the retrieval toolset, and `select_tools` is where that
    lives — the flag an eval run turns off to measure what the lane is worth."""
    names = {tool.__name__ for tool in select_tools("both", structured=True)}
    assert {"list_tables", "describe_table", "query_structured"} <= names
    assert names & {"search_corpus", "vector_search"}, "the reference lane must stay registered"

    without = {tool.__name__ for tool in select_tools("both", structured=False)}
    assert not without & {"list_tables", "describe_table", "query_structured"}


def test_a_queried_row_becomes_a_structured_citation(agent_kit, structured_store) -> None:
    """The end-to-end shape of a relational answer: a query, a row cited by id, and a contract
    citation whose every displayed field was built from the row rather than from the model."""
    row_id, cells = _first_row(structured_store)
    answer = agent_kit.answer(
        answer="That plan's individual medical deductible is $4,500. [c1]",
        citations=[
            {
                "id": "c1",
                "row_id": row_id,
                "cells": {"TEHBDedInnTier1Individual": cells["TEHBDedInnTier1Individual"]},
            }
        ],
    )
    response = agent_kit.run(_query_turn(), answer, structured=structured_store, plan_year=2026)

    (citation,) = response.citations
    assert citation.source_type == "structured_api"
    assert citation.chunk_id is None and citation.doc_id is None
    assert citation.snippet.startswith("TEHBDedInnTier1Individual: ")
    assert "plan_attributes" in citation.title and "2026" in citation.title
    assert citation.url and "cms.gov" in citation.url
    # The SQL the model wrote is the most legible step in the trace, so it has to be in it.
    assert any(step.tool == "query_structured" for step in response.trace)
    assert any(DEDUCTIBLE_SQL in str(step.input) for step in response.trace if step.input)


def test_citing_a_row_no_query_returned_is_rejected(agent_kit, structured_store) -> None:
    """`seen_rows` is `seen_chunks` one lane over: cite what a tool returned, or be asked again."""
    answer = agent_kit.answer(
        answer="The deductible is $4,500. [c1]",
        citations=[
            {"id": "c1", "row_id": "exchange_puf/2026/plan_attributes#q9.1", "cells": {"x": "1"}}
        ],
    )
    complaint = agent_kit.expect_rejection(
        _query_turn(), answer, structured=structured_store, plan_year=2026
    )
    assert "no query returned" in complaint


def test_a_tidied_cell_value_is_rejected(agent_kit, structured_store) -> None:
    """The strictest comparison in the repo, and deliberately stricter than the chunk path.

    A chunk quotation is normalised for whitespace because the corpora wrap mid-sentence. A cell
    has no wrapping to survive, and its trailing space is *data* — so a model that "helpfully"
    writes `$4,500` where the source says `$4,500 ` has edited the evidence, and is told so.
    """
    row_id, cells = _first_row(structured_store)
    stored = cells["TEHBDedInnTier1Individual"]
    assert stored is not None and stored != stored.strip(), (
        "the fixture must carry the real spacing"
    )

    answer = agent_kit.answer(
        answer="The deductible is $4,500. [c1]",
        citations=[
            {"id": "c1", "row_id": row_id, "cells": {"TEHBDedInnTier1Individual": stored.strip()}}
        ],
    )
    complaint = agent_kit.expect_rejection(
        _query_turn(), answer, structured=structured_store, plan_year=2026
    )
    assert "Copy the cell exactly" in complaint


def test_citing_a_column_the_row_does_not_have_is_rejected(agent_kit, structured_store) -> None:
    row_id, _ = _first_row(structured_store)
    answer = agent_kit.answer(
        answer="The premium is $300. [c1]",
        citations=[{"id": "c1", "row_id": row_id, "cells": {"Premium": "$300"}}],
    )
    complaint = agent_kit.expect_rejection(
        _query_turn(), answer, structured=structured_store, plan_year=2026
    )
    assert "does not have" in complaint


def test_a_rejected_query_is_a_retry_not_a_crash(agent_kit, structured_store) -> None:
    """A bad query is an ordinary event in this lane, not a failed run.

    The model gets DuckDB's own message — which names the near-miss columns better than anything
    written here would — recovers, and the attempt stays in the trace. A rejected query that
    vanished from the trace would hide the most interesting thing the agent did.
    """
    response = agent_kit.run(
        _query_turn("SELECT Nonexistent FROM exchange_puf.plan_attributes_2026"),
        agent_kit.answer(
            abstained=True,
            answer="I could not find that in the vendored plan data.",
            citations=[],
        ),
        structured=structured_store,
        plan_year=2026,
    )
    assert response.abstained
    rejected = [
        s for s in response.trace if s.tool == "query_structured" and "rejected" in s.summary
    ]
    assert rejected, "a rejected query must still appear in the trace"
    assert "Nonexistent" in rejected[0].summary


def test_a_reference_and_a_row_citation_coexist(agent_kit, structured_store) -> None:
    """One id space, two lanes. This is the shape Phase 4's per-claim provenance builds on, and the
    reason `AgentCitation` is one class with two shapes rather than two lists."""
    row_id, cells = _first_row(structured_store)
    answer = agent_kit.answer(
        answer="A deductible is what you pay first. [c1] On this plan it is $4,500. [c2]",
        citations=[
            {
                "id": "c1",
                "chunk_id": agent_kit.DEDUCTIBLE_ID,
                "snippet": "before your insurance plan starts to pay",
            },
            {
                "id": "c2",
                "row_id": row_id,
                "cells": {"TEHBDedInnTier1Individual": cells["TEHBDedInnTier1Individual"]},
            },
        ],
    )
    response = agent_kit.run(
        agent_kit.SEARCH, _query_turn(), answer, structured=structured_store, plan_year=2026
    )
    assert [c.source_type for c in response.citations] == ["reference", "structured_api"]
