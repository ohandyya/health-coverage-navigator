"""Emit a candidate gold question from a real query against the vendored plan data.

    uv run python -m health_coverage_navigator.evals.structured_gold \\
        --question "What is the individual medical deductible on plan 38344AK1060002 for 2026?" \\
        --sql "SELECT CSRVariationType, TEHBDedInnTier1Individual FROM ..." \\
        --plan-year 2026 --cells TEHBDedInnTier1Individual

Why this exists rather than a paragraph in a contributing guide: **`expected_cells` must never be
typed by hand.** The mirror stores `'$4,500 '` with a trailing space and `'Not Applicable'` beside
an empty string, and a value tidied on its way into YAML asserts something the source does not say
— which would make the eval grade the agent against a fiction, in the one lane whose whole point is
byte-exact evidence.

It prints YAML for a human to read, edit the prose of, and paste into `evals/gold/questions.yaml`.
It does not write the file: the question, its difficulty and its key facts are judgement, and only
the *values* are mechanical.
"""

import argparse
import asyncio
import sys

import yaml

from health_coverage_navigator.structured.catalog import StructuredNotBuiltError
from health_coverage_navigator.structured.store import QueryRejected, StructuredStore


async def _emit(args: argparse.Namespace) -> int:
    try:
        store = StructuredStore.open()
    except StructuredNotBuiltError as exc:
        raise SystemExit(f"{exc}") from exc

    try:
        result = await store.query(args.sql, plan_year=args.plan_year, limit=args.limit)
    except QueryRejected as exc:
        raise SystemExit(f"the query was rejected: {exc}") from exc

    if not result.rows:
        raise SystemExit(
            "that query returned no rows, so there is nothing to assert. A gold question needs a "
            "value the mirror actually holds."
        )

    row = result.rows[0]
    wanted = args.cells or list(row.cells)
    missing = [column for column in wanted if column not in row.cells]
    if missing:
        raise SystemExit(f"the result has no column(s) {missing}; it has {sorted(row.cells)}")

    cells = [row.cells[column] for column in wanted]
    if any(value is None for value in cells):
        raise SystemExit(
            f"one of {wanted} is NULL in that row. A NULL is not a value a citation can carry — "
            f"pick a column the row actually has, or a row that has it."
        )

    question = {
        "id": args.id,
        "question": args.question,
        "difficulty": args.difficulty,
        "expected_source_type": "structured_api",
        "plan_year": args.plan_year,
        "expected_table": row.view.rsplit("/", 1)[-1].split("+")[0]
        if row.source is None
        else f"{row.source}.{row.view.rsplit('/', 1)[-1]}",
        "expected_cells": cells,
        "expected_answer": "TODO — write the answer a person should get",
        "answer_key_facts": ["TODO — one fact per line, in plain language"],
        "notes": f"Generated from: {result.sql}",
    }
    print(f"# {result.row_count} row(s) matched; asserting the first", file=sys.stderr)
    print(yaml.safe_dump([question], sort_keys=False, allow_unicode=True, width=96))
    store.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--question", required=True, help="the question, as a person would ask it")
    parser.add_argument("--sql", required=True, help="a SELECT that returns the answering row")
    parser.add_argument("--plan-year", type=int, required=True, dest="plan_year")
    parser.add_argument("--id", default="str-XX", help="gold question id")
    parser.add_argument(
        "--difficulty", default="medium", choices=("easy", "medium", "hard"), help="phrasing gap"
    )
    parser.add_argument(
        "--cells",
        nargs="*",
        help="which columns of the row a correct answer must cite (default: all of them)",
    )
    parser.add_argument("--limit", type=int, default=5)
    # `asyncio.run` at an entry point, which is the one place CLAUDE.md allows it.
    return asyncio.run(_emit(parser.parse_args()))


if __name__ == "__main__":  # pragma: no cover - CLI
    raise SystemExit(main())
