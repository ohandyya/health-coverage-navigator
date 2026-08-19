"""`StructuredStore` — DuckDB over the vendored Parquet mirrors, and the guard on model-written SQL.

The read side of the relational lane. `agent/structured_tools.py` adds the trace and the run-scoped
bookkeeping; the querying itself is here, and this module imports nothing from `agent/` so that
every test below runs with no model, no key, and no network.

**Nothing is loaded.** The Parquet files are registered as views and read where they lie — 22 ms to
open a connection and register all ten tables, 3–16 ms for a representative lookup. There is no
copy of the data in memory, no server, and no build step beyond the downloaders.

**The mirrors stay lossless.** Every column is `VARCHAR` and is returned as it was stored,
including `'$450 '`'s trailing space. Two macros (`to_usd`, `to_pct`) are registered for *filtering
and ordering*; they never rewrite what is stored, and the tool descriptions tell the model to quote
the raw cell when reporting a value.

## The guard, in two independent layers

Model-written SQL reaches a real query engine, so neither layer trusts the other
(docs/relational-tool.md §4, where each behaviour below is a measurement rather than a reading of
the docs):

1. **Statement shape.** `duckdb.extract_statements` must yield exactly one statement, of type
   `SELECT`. That rejects `COPY`, `ATTACH`, `INSTALL`, `CREATE`, and anything hiding behind a
   second statement, before DuckDB executes a character.
2. **The connection sandbox.** After the views exist, `allowed_directories` is pinned to the
   processed-data directory, external access is turned off, and the configuration is locked. The
   order matters and is load-bearing: views are lazy, so they must be created first; and
   `allowed_directories` does nothing while external access is still enabled. Verified after
   locking — `read_csv('/etc/passwd')`, `COPY … TO`, `ATTACH`, `INSTALL` and further `SET`s all
   raise, while the registered views still read.

Plus two ceilings that make a bad query cheap rather than impossible: every statement is wrapped in
a row cap it cannot forget to write, and each one runs on its own cursor with a `threading.Timer`
armed on `interrupt()`, because DuckDB has no statement timeout.

Every rejection is a `QueryRejected` carrying DuckDB's own message. That is deliberate: DuckDB's
binder error already names the near-miss columns (*"Candidate bindings: TEHBDedInnTier1Individual,
…"*), which is better than anything written here, and the tool turns it into a `ModelRetry`.
"""

import asyncio
import re
import threading
import time
from pathlib import Path

import duckdb

from health_coverage_navigator.config import StructuredConfig
from health_coverage_navigator.paths import PROCESSED_DIR
from health_coverage_navigator.structured.catalog import (
    TABLE_NOTES,
    StructuredStaleError,
    TableFile,
    discover,
    resolve_partition,
)
from health_coverage_navigator.structured.models import (
    ColumnInfo,
    QueryResult,
    Row,
    StructuredOverview,
    TableDescription,
    TableInfo,
)

#: Ceiling on how many columns `describe_table` reports in one call. `plan_attributes` has 151, and
#: describing all of them costs more context than the query that follows. The reply says it
#: truncated and names the `contains` filter, which is the intended way through a wide table.
MAX_DESCRIBED_COLUMNS = 60

#: A column with at most this many distinct values gets its full value distribution. Above it, a
#: histogram is noise rather than orientation.
LOW_CARDINALITY = 12

#: How many rows `describe_table` reads to draw example values from. Parquet order, so the same
#: rows every time — a describe that changed between calls would make a trace unreadable.
EXAMPLE_SCAN_ROWS = 20

#: Values that look like money or a percentage, for suggesting a macro. Deliberately loose: the
#: suggestion is a hint in a description, and a false positive costs the model one look.
_MONEY_RE = re.compile(r"^\s*\$[\d,]")
_PERCENT_RE = re.compile(r"^\s*[\d.]+\s*%\s*$")

#: The macros registered on every connection. `TRY_CAST` rather than `CAST` so an unparseable value
#: becomes NULL and drops out of a comparison, instead of failing a query that is 99% right — at
#: the cost, stated in the tool description, that `'Not Applicable'` and `''` both become NULL.
MACROS = (
    "CREATE MACRO to_usd(x) AS "
    "TRY_CAST(NULLIF(regexp_replace(trim(CAST(x AS VARCHAR)), '[$,]', '', 'g'), '') AS DOUBLE)",
    "CREATE MACRO to_pct(x) AS "
    "TRY_CAST(NULLIF(replace(trim(CAST(x AS VARCHAR)), '%', ''), '') AS DOUBLE)",
)


class QueryRejected(ValueError):
    """A query the store refused, or that DuckDB refused, with the reason attached.

    Not an error condition: in a lane where the model writes SQL, a rejected query is a normal
    event and the message is the correction. `agent/structured_tools.py` re-raises it as a
    `ModelRetry`, the same way `grep_corpus` handles a bad regular expression.
    """


def _quote(identifier: str) -> str:
    """Quote an identifier for interpolation. Used only on names read off DuckDB's own catalog."""
    escaped = identifier.replace('"', '""')
    return f'"{escaped}"'


def _sql_literal(path: Path) -> str:
    """Single-quote a path for a `read_parquet` literal, escaping any embedded quote.

    Same guard the downloaders apply to the same interpolation: these paths come from a directory
    listing, not from a user, but a path is still text going into SQL.
    """
    return str(path).replace("'", "''")


def _render(value: object) -> str | None:
    """One result cell, as the model and the grounding validator both see it.

    `None` stays `None` — it is SQL NULL, which in this data is *not* the same as the empty string
    a source column can legitimately hold. Everything else becomes text without reformatting, so a
    `VARCHAR` survives byte-for-byte and a computed number renders once, here, rather than
    differently in each caller.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value)


class StructuredStore:
    """An open DuckDB connection with every vendored table registered as a view."""

    __slots__ = ("_by_view", "_con", "_config", "_partitions", "_tables")

    def __init__(
        self,
        con: duckdb.DuckDBPyConnection,
        tables: list[TableFile],
        config: StructuredConfig,
    ) -> None:
        self._con = con
        self._config = config
        self._tables = tables
        self._by_view = {t.view: t for t in tables}
        partitions: dict[str, list[str]] = {}
        for table in tables:
            seen = partitions.setdefault(table.source, [])
            if table.partition not in seen:
                seen.append(table.partition)
        self._partitions = {source: sorted(parts) for source, parts in partitions.items()}

    # ---------------------------------------------------------------- opening ----------------

    @classmethod
    def open(
        cls,
        *,
        processed_dir: Path | None = None,
        raw_dir: Path | None = None,
        config: StructuredConfig | None = None,
    ) -> "StructuredStore":
        """Open the mirror, refusing one that disagrees with its committed manifest.

        `processed_dir` and `raw_dir` are parameters rather than module constants so a test can
        point at a fixture built from the committed sample slices — the same injectable-path
        precedent as `create_app(dist_dir=...)`, and the reason nothing here binds a default at
        import time.
        """
        if config is None:
            from health_coverage_navigator.config import get_config

            config = get_config().structured

        root = PROCESSED_DIR if processed_dir is None else processed_dir
        found = discover(root, raw_dir)
        tables = [
            table
            for partitions in found.values()
            for files in partitions.values()
            for table in files
        ]

        con = duckdb.connect(
            config={"memory_limit": config.memory_limit, "threads": config.threads}
        )
        for source in sorted({t.source for t in tables}):
            con.execute(f"CREATE SCHEMA IF NOT EXISTS {_quote(source)}")
        for table in tables:
            con.execute(
                f"CREATE VIEW {table.view} AS "
                f"SELECT * FROM read_parquet('{_sql_literal(table.path)}')"
            )
        for macro in MACROS:
            con.execute(macro)

        cls._verify(con, tables)

        # Views first, then the sandbox — in this order, for the reasons in the module docstring.
        # `allowed_directories` is still set even though external access is about to be disabled:
        # it is what keeps the registered views readable, and it is the allow-list that would
        # matter again if external access were ever re-enabled deliberately.
        con.execute(f"SET allowed_directories=['{_sql_literal(root.resolve())}']")
        con.execute("SET enable_external_access=false")
        con.execute("SET lock_configuration=true")

        return cls(con, tables, config)

    @staticmethod
    def _verify(con: duckdb.DuckDBPyConnection, tables: list[TableFile]) -> None:
        """Every table's row count must match the committed catalog.

        Cheap — Parquet keeps the count in its footer, so ten `COUNT(*)`s are a few milliseconds —
        and it is the difference between a half-downloaded mirror failing at startup and it
        answering questions plausibly from the rows that happened to arrive.
        """
        for table in tables:
            (actual,) = con.execute(f"SELECT COUNT(*) FROM {table.view}").fetchone() or (None,)
            if actual != table.expected_rows:
                raise StructuredStaleError(
                    f"{table.source}/{table.partition}/{table.table}: the mirror holds "
                    f"{actual:,} rows but catalog.json records {table.expected_rows:,}. "
                    f"Re-run the downloader for this partition rather than answering from it."
                )

    def close(self) -> None:
        self._con.close()

    # ---------------------------------------------------------------- what exists ------------

    @property
    def available_partitions(self) -> dict[str, list[str]]:
        return {source: list(parts) for source, parts in self._partitions.items()}

    def resolve(self, plan_year: int | None) -> dict[str, str]:
        """Source → the partition that answers a question about `plan_year`.

        A source with no partition for that year is **absent from the result**, not defaulted to
        another year. Callers read the absence as "this source cannot answer for that year", which
        is what turns an unvendored plan year into an abstention.
        """
        resolved: dict[str, str] = {}
        for source, parts in self._partitions.items():
            partition = resolve_partition(parts, plan_year)
            if partition is not None:
                resolved[source] = partition
        return resolved

    def overview(self, plan_year: int | None) -> StructuredOverview:
        """What `list_tables` reports."""
        partitions = self.resolve(plan_year)
        tables = [
            TableInfo(
                table=t.qualified,
                view=t.view,
                partition=t.partition,
                rows=t.expected_rows,
                columns=len(self._columns(t.view)),
            )
            for t in self._tables
            if partitions.get(t.source) == t.partition
        ]
        return StructuredOverview(
            plan_year=plan_year,
            partitions=partitions,
            available_partitions=self.available_partitions,
            tables=sorted(tables, key=lambda t: t.table),
        )

    def _resolve_table(self, table: str, plan_year: int | None) -> TableFile:
        """Find one table by `<source>.<table>`, in the partition `plan_year` selects.

        Accepts a view name (`exchange_puf.plan_attributes_2026`) too, since that is what
        `list_tables` prints and what the model will have in front of it.
        """
        partitions = self.resolve(plan_year)
        for candidate in self._tables:
            if candidate.view == table:
                return candidate
        matches = [
            candidate
            for candidate in self._tables
            if candidate.qualified == table
            and partitions.get(candidate.source) == candidate.partition
        ]
        if matches:
            return matches[0]

        known = sorted({t.qualified for t in self._tables})
        if any(t.qualified == table for t in self._tables):
            source = table.split(".", 1)[0]
            raise QueryRejected(
                f"{table} exists but not for plan year {plan_year}. Vendored partitions for "
                f"{source}: {self._partitions.get(source, [])}. Answer for one of those years or "
                f"say the data is not available for {plan_year}."
            )
        raise QueryRejected(f"no table named {table!r}. Tables in the mirror: {known}.")

    # ---------------------------------------------------------------- describing ------------

    def _columns(self, view: str) -> list[str]:
        rows = self._con.execute(f"DESCRIBE {view}").fetchall()
        return [str(row[0]) for row in rows]

    async def describe(
        self,
        table: str,
        *,
        plan_year: int | None = None,
        contains: str | None = None,
        limit: int = MAX_DESCRIBED_COLUMNS,
    ) -> TableDescription:
        """Describe one table by its **values**: examples, cardinality, and the notes that apply.

        Three queries, ~70 ms measured on the widest table: example rows, an approximate distinct
        count per column, and a value distribution for the low-cardinality ones. All of it is our
        own SQL over names read off DuckDB's catalog, so none of it goes through the guard — but it
        does go through the same timeout, because a describe that hangs is a request that hangs.
        """
        return await asyncio.to_thread(self._describe, table, plan_year, contains, limit)

    def _describe(
        self, table: str, plan_year: int | None, contains: str | None, limit: int
    ) -> TableDescription:
        target = self._resolve_table(table, plan_year)
        columns = self._columns(target.view)
        selected = (
            [c for c in columns if contains.lower() in c.lower()] if contains else list(columns)
        )
        truncated = len(selected) > max(limit, 1)
        selected = selected[: max(limit, 1)]

        infos: list[ColumnInfo] = []
        if selected:
            quoted = [_quote(c) for c in selected]
            sample = self._execute(
                f"SELECT {', '.join(quoted)} FROM {target.view} LIMIT {EXAMPLE_SCAN_ROWS}"
            )[1]
            distinct_row = self._execute(
                f"SELECT {', '.join(f'approx_count_distinct({q})' for q in quoted)} "
                f"FROM {target.view}"
            )[1]
            # `approx_count_distinct` returns a BIGINT, but DuckDB's Python types widen to
            # `object`; `_as_int` is where that becomes a number without pyright taking it on faith.
            distincts = [_as_int(v) for v in (distinct_row[0] if distinct_row else ())]

            low = [
                (position, column)
                for position, (column, count) in enumerate(zip(selected, distincts, strict=False))
                if count <= LOW_CARDINALITY
            ]
            histograms: dict[str, dict[str, int]] = {}
            if low:
                hist_row = self._execute(
                    f"SELECT {', '.join(f'histogram({_quote(c)})' for _, c in low)} "
                    f"FROM {target.view}"
                )[1]
                if hist_row:
                    for (_, column), value in zip(low, hist_row[0], strict=False):
                        if isinstance(value, dict):
                            histograms[column] = {
                                str(k): int(v)
                                for k, v in value.items()  # pyright: ignore[reportUnknownArgumentType]
                            }

            for position, column in enumerate(selected):
                values = [_render(row[position]) for row in sample]
                examples: list[str] = []
                for value in values:
                    if value is not None and value.strip() and value not in examples:
                        examples.append(value)
                    if len(examples) == 3:
                        break
                infos.append(
                    ColumnInfo(
                        name=column,
                        examples=examples,
                        distinct=distincts[position] if position < len(distincts) else None,
                        common=histograms.get(column),
                        macro=_macro_for(examples),
                    )
                )

        return TableDescription(
            table=target.qualified,
            view=target.view,
            partition=target.partition,
            rows=target.expected_rows,
            columns=infos,
            total_columns=len(columns),
            truncated=truncated,
            notes=TABLE_NOTES.get(target.qualified, []),
        )

    # ---------------------------------------------------------------- querying --------------

    async def query(
        self,
        sql: str,
        *,
        plan_year: int | None = None,
        limit: int | None = None,
        sequence: int = 1,
    ) -> QueryResult:
        """Run one guarded `SELECT`, and record every row it returned as citable.

        `sequence` numbers the query within an agent run, so a row id says which call produced it.
        """
        partitions = self.resolve(plan_year)
        statement = self._check_statement(sql)
        views = self._check_partitions(statement, partitions, plan_year)
        cap = self._config.max_rows if limit is None else max(1, min(limit, self._config.max_rows))

        started = time.perf_counter()
        columns, rows = await asyncio.to_thread(
            self._execute, f"SELECT * FROM (\n{statement}\n) LIMIT {cap + 1}"
        )
        duration_ms = int((time.perf_counter() - started) * 1000)

        truncated = len(rows) > cap
        subject, source, partition = _subject(views)
        return QueryResult(
            sql=statement,
            columns=columns,
            rows=[
                Row(
                    row_id=f"{subject}#q{sequence}.{position}",
                    view=subject,
                    source=source,
                    partition=partition,
                    cells={
                        column: _render(value) for column, value in zip(columns, row, strict=False)
                    },
                )
                for position, row in enumerate(rows[:cap], start=1)
            ],
            row_count=min(len(rows), cap),
            truncated=truncated,
            partitions=partitions,
            duration_ms=duration_ms,
        )

    def _check_statement(self, sql: str) -> str:
        """Layer 1: exactly one statement, and it must be a `SELECT`.

        Returns the statement text with its trailing semicolon removed, because the row cap wraps
        it in a subquery and `(SELECT 1;)` is not valid SQL.
        """
        try:
            statements = duckdb.extract_statements(sql)
        except Exception as exc:  # noqa: BLE001 - any parse failure is the model's to fix
            raise QueryRejected(f"that is not valid SQL: {exc}") from exc

        if len(statements) != 1:
            raise QueryRejected(
                f"send exactly one statement; that was {len(statements)}. Run them one at a time "
                f"so each result can be cited separately."
            )
        if statements[0].type != duckdb.StatementType.SELECT:
            raise QueryRejected(
                f"only SELECT is allowed here, and that is a "
                f"{statements[0].type.name} statement. This lane reads the vendored data; nothing "
                f"can write to it."
            )
        return statements[0].query.strip().rstrip(";").strip()

    def _check_partitions(
        self, sql: str, partitions: dict[str, str], plan_year: int | None
    ) -> list[TableFile]:
        """Refuse a query that reads a plan year this run did not pin.

        Every view name carries its partition, so this is a set-membership test over known strings
        rather than SQL parsing. It is what makes `plan_year` a rule rather than a suggestion: the
        request pins a year, and a query against another year's table is corrected before it runs
        instead of producing a number from the wrong year that reads exactly as authoritative.
        """
        mentioned = [table for view, table in self._by_view.items() if _mentions(sql, view)]
        wrong = [t for t in mentioned if partitions.get(t.source) != t.partition]
        if wrong:
            table = wrong[0]
            right = partitions.get(table.source)
            correction = (
                f"use {table.source}.{table.table}_{right}"
                if right
                else f"{table.source} has no data for plan year {plan_year}"
            )
            raise QueryRejected(
                f"{table.view} holds {table.source} data for {table.partition}, but this question "
                f"is pinned to plan year {plan_year}. {correction}."
            )
        return mentioned

    def _execute(self, sql: str) -> tuple[list[str], list[tuple[object, ...]]]:
        """Run one statement on its own cursor, under the timeout. Synchronous by design.

        A cursor per query rather than the shared connection is what makes concurrent questions
        safe, and it is also what the timeout needs: `interrupt()` cancels the cursor that is
        running, leaving every other query and the connection itself usable. DuckDB has no
        statement timeout, which is why this is a timer and not a setting.
        """
        cursor = self._con.cursor()
        timer = threading.Timer(self._config.query_timeout_s, cursor.interrupt)
        timer.start()
        try:
            cursor.execute(sql)
            columns = [str(d[0]) for d in cursor.description or []]
            return columns, cursor.fetchall()
        except duckdb.InterruptException as exc:
            raise QueryRejected(
                f"the query was still running after {self._config.query_timeout_s:g}s and was "
                f"cancelled. Add a filter on an indexed identifier, or aggregate rather than "
                f"scanning the whole table."
            ) from exc
        except duckdb.Error as exc:
            raise QueryRejected(str(exc)) from exc
        finally:
            timer.cancel()
            cursor.close()


def _as_int(value: object) -> int:
    """A count DuckDB returned, as an int. Anything unexpected reads as zero rather than raising.

    A describe is orientation, not evidence: a cardinality that cannot be read is worth degrading
    over, unlike a *cell*, which `_render` never guesses at.
    """
    return int(value) if isinstance(value, int | float | str) else 0


def _macro_for(examples: list[str]) -> str | None:
    if any(_MONEY_RE.match(value) for value in examples):
        return "to_usd"
    if any(_PERCENT_RE.match(value) for value in examples):
        return "to_pct"
    return None


def _mentions(sql: str, view: str) -> bool:
    """Whether `sql` names this view, ignoring case as DuckDB does for unquoted identifiers."""
    return re.search(rf"(?<![\w.]){re.escape(view)}(?![\w])", sql, re.IGNORECASE) is not None


def _subject(views: list[TableFile]) -> tuple[str, str | None, str | None]:
    """The `<source>/<partition>/<table>` prefix of a row id, plus the provenance behind it.

    Names what the row is *about*, which for a join is both tables. A query touching no vendored
    table at all (`SELECT 1`) still gets a stable prefix rather than an empty one — it is citable,
    it is just not about any table, and the citation it produces says so instead of naming a source
    it did not read.
    """
    if not views:
        return "structured/expression", None, None
    sources = {t.source for t in views}
    partitions = {t.partition for t in views}
    if len(sources) == 1 and len(partitions) == 1:
        table = views[0]
        prefix = f"{table.source}/{table.partition}/{'+'.join(sorted(t.table for t in views))}"
        return prefix, table.source, table.partition
    return "+".join(sorted(t.view for t in views)), None, None
