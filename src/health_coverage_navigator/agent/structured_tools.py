"""The relational lane's three tools: orient, inspect, query.

`structured/store.py` does the querying. This module does what only makes sense *inside an agent
run* — the same two jobs `tools.py` does for the reference lane:

**The trace.** Every call appends a `tool_call` / `tool_result` pair to `deps.trace`, and for
`query_structured` the arguments include the SQL the model wrote. That is the most legible step
this project produces: the query is the agent's reasoning, written down.

**What the agent has actually seen.** Every row a query returns is recorded in `deps.seen_rows`,
and the output validator refuses to accept a citation of anything else — or of a *cell value* the
row does not carry. That is the relational analogue of the verbatim-snippet rule: in a lane whose
evidence is a table cell rather than a sentence, "quote it exactly" means "the value, byte for
byte".

Three tools rather than a typed function per question (docs/relational-tool.md §3): the data is
relational and the questions are not knowable in advance, so the general tool is SQL and the
guard is code. `QueryRejected` — a bad statement, an unknown column, a timeout, a query against a
plan year this question did not pin — comes back as a `ModelRetry` carrying DuckDB's own message,
because DuckDB's binder error already names the near-miss columns better than we could.
"""

import time

from pydantic_ai import ModelRetry, RunContext

from health_coverage_navigator.agent.deps import AnswerDeps
from health_coverage_navigator.structured.models import (
    QueryResult,
    StructuredOverview,
    TableDescription,
)
from health_coverage_navigator.structured.store import QueryRejected

#: What the tools say when the lane is configured but the mirror is not on disk. Unreachable in the
#: app — `routes/chat.py` returns a 503 before the agent runs — but reachable in a test or a script
#: that builds `AnswerDeps` by hand, and a `None` dereference there would read as a bug in the
#: agent rather than a missing download.
NO_STORE = (
    "The structured plan data is not available in this session. Answer from the reference "
    "corpus if it covers the question, or say that plan-specific data is not available."
)


def _store(ctx: RunContext[AnswerDeps]):
    store = ctx.deps.structured
    if store is None:  # pragma: no cover - `select_tools` makes this unreachable in the app
        raise ModelRetry(NO_STORE)
    return store


async def list_tables(ctx: RunContext[AnswerDeps]) -> StructuredOverview:
    """List the vendored plan-data tables you can query, and which plan year they cover.

    Start here for anything about a **specific** plan, drug, premium, deductible or service area.
    This is data published by CMS as tables — one row per plan, per benefit, per drug — not prose,
    so it answers "what is the deductible on plan X" in a way the reference corpus never can.

    The reply names each table twice: `table` is its short name and **`view` is what you write in
    SQL**. The view carries the plan year (`exchange_puf.plan_attributes_2026`), so a query states
    which year it read.

    It also reports `available_partitions` — every plan year and quarter on this machine. If the
    question asks about a year that is not listed, say so and abstain rather than answering from a
    different year: these figures change every year, and a number from the wrong one looks exactly
    as authoritative as the right one.

    Two sources are vendored:

    - **exchange_puf** — the ACA marketplace. Per-plan attributes (deductibles, out-of-pocket
      maximums, metal level, HSA eligibility), per-benefit cost sharing, and the counties a plan
      is sold in.
    - **part_d_spuf** — Medicare Part D. Which drug is on which formulary, at what tier, with
      which utilization-management flags, plus per-plan premiums and deductibles.
    """
    started = time.perf_counter()
    overview = _store(ctx).overview(ctx.deps.plan_year)
    ctx.deps._record(
        "list_tables",
        {"plan_year": ctx.deps.plan_year},
        f"{len(overview.tables)} tables across {len(overview.partitions)} sources "
        f"({', '.join(f'{k} {v}' for k, v in sorted(overview.partitions.items())) or 'none'})",
        int((time.perf_counter() - started) * 1000),
    )
    return overview


async def describe_table(
    ctx: RunContext[AnswerDeps],
    table: str,
    contains: str | None = None,
) -> TableDescription:
    """Show a table's columns, with **real values** from them — read this before writing SQL.

    Every column in this data is text, including the ones that look numeric. A deductible is stored
    as `'$4,500 '` — dollar sign, comma, trailing space — and an empty string sits beside
    `'Not Applicable'` meaning something different from it. So a filter written from a guess about
    the values will silently match nothing, and a comparison like `< 1000` will compare strings.

    The reply gives, per column: example values, roughly how many distinct values there are, the
    full value distribution when there are few, and the name of a macro (`to_usd`, `to_pct`) when
    the values look like money or a percentage.

    It also carries `notes` — correctness warnings specific to that table. **Read them.** Each one
    is a documented way for a reasonable-looking query to return a confident wrong answer.

    Args:
        table: A `table` or `view` name from `list_tables`, e.g. `exchange_puf.plan_attributes`.
        contains: Show only columns whose name contains this text, case-insensitively. Use it on
            wide tables — `plan_attributes` has 151 columns, 36 of them different flavours of
            out-of-pocket maximum, and `contains="MOOP"` is how you find the right one instead of
            guessing a name.
    """
    started = time.perf_counter()
    try:
        described = await _store(ctx).describe(
            table, plan_year=ctx.deps.plan_year, contains=contains
        )
    except QueryRejected as exc:
        raise ModelRetry(str(exc)) from exc

    ctx.deps._record(
        "describe_table",
        {"table": table, "contains": contains},
        f"{len(described.columns)} of {described.total_columns} columns in {described.view}"
        + (f", {len(described.notes)} correctness note(s)" if described.notes else ""),
        int((time.perf_counter() - started) * 1000),
    )
    return described


async def query_structured(
    ctx: RunContext[AnswerDeps],
    sql: str,
    limit: int | None = None,
) -> QueryResult:
    """Run one read-only `SELECT` against the vendored plan data, and get back citable rows.

    Write ordinary DuckDB SQL against the `view` names `list_tables` gives you. Joins, `GROUP BY`,
    CTEs and aggregates all work. Only `SELECT` is allowed, one statement at a time.

    **Look at the columns first.** `describe_table` costs one call and prevents the two failures
    that waste several: a column name that does not exist, and a filter written against a value
    that is formatted differently than you assumed.

    Two macros are available for the publisher-formatted numbers: `to_usd(col)` turns `'$4,500 '`
    into `4500.0` and `to_pct(col)` turns `'70.88%'` into `70.88`. Use them to **filter and order**
    — `WHERE to_usd(TEHBDedInnTier1Individual) < 1000`. Do **not** use them to report a value: they
    map both `''` and `'Not Applicable'` to NULL, and those mean different things. Quote the raw
    cell in your answer.

    **Citing a row.** Each row comes back with a `row_id` and its `cells`. To use one, cite the
    `row_id` and copy the exact cell values you relied on — including a trailing space if the value
    has one. You may only cite rows a query returned in this conversation.

    **Zero rows is a finding, not a failure.** It means this table holds no such row — which is
    different from "the plan does not cover it" and different from a cell reading
    `'Not Applicable'`. Say which one you mean, and never present an empty result as a coverage
    decision.

    Args:
        sql: One `SELECT` statement. Name tables by their `view` (with the year in the name).
        limit: How many rows to return, up to the server's cap. The cap is applied either way, and
            the reply says when it truncated.
    """
    deps = ctx.deps
    store = _store(ctx)
    started = time.perf_counter()
    try:
        result = await store.query(
            sql, plan_year=deps.plan_year, limit=limit, sequence=deps.next_query()
        )
    except QueryRejected as exc:
        deps._record(
            "query_structured",
            {"sql": sql, "limit": limit},
            f"rejected: {exc}",
            int((time.perf_counter() - started) * 1000),
        )
        raise ModelRetry(str(exc)) from exc

    deps.remember_rows(result.rows)
    deps._record(
        "query_structured",
        {"sql": result.sql, "limit": limit},
        f"{result.row_count} row(s)"
        + (" (truncated)" if result.truncated else "")
        + f", {len(result.columns)} column(s)",
        result.duration_ms,
    )
    return result


#: Phase 1-c's relational lane. Registered alongside the reference-lane tools, never instead of
#: them: the whole measurement of this phase is whether the agent picks the right *kind* of source,
#: which it cannot do if it only has one.
STRUCTURED_TOOLS = (list_tables, describe_table, query_structured)

__all__ = ["STRUCTURED_TOOLS", "describe_table", "list_tables", "query_structured"]
