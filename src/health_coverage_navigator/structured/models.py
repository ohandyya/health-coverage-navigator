"""What the relational tools return — the shapes the *language model* reads.

These sit here rather than in `agent/models.py`, which holds the reference lane's equivalents, for
one structural reason: `structured/` must not import from `agent/` (docs/relational-tool.md §9), and
`store.py` builds every one of these. The prompt-surface rule still applies — every field name and
docstring below is text a model reads, so it is written for that reader.

Two things carried deliberately rather than smoothed away, both from the mirror's own design
(`docs/exchange_puf_data.md`):

**A cell is `str | None`, and `None` is SQL `NULL`, not an empty cell.** The mirrors store every
column as `VARCHAR` with DuckDB's null handling disabled, so a source column is never `NULL` —
`''` and `'Not Applicable'` are distinct, real values. A `None` therefore only ever comes from a
query's own doing (an outer join that missed, a `to_usd()` that could not parse), and collapsing it
onto `''` would erase that distinction in the one lane where the difference is the answer.

**Values are strings, never coerced.** A `COUNT(*)` and a `to_usd()` are rendered to text at the
boundary, so `Row.cells` is one type and the grounding validator has one comparison to make.
"""

from pydantic import BaseModel, Field


class TableInfo(BaseModel):
    """One vendored table, in one partition."""

    table: str
    """Qualified name, `<source>.<table>` — e.g. `exchange_puf.plan_attributes`."""

    view: str
    """**The name to write in SQL.** It carries the partition (`exchange_puf.plan_attributes_2026`),
    so a query names the plan year it reads and the trace shows it."""

    partition: str
    """`2026` for a plan year, `2026Q2` for a Part D quarter."""

    rows: int
    columns: int


class StructuredOverview(BaseModel):
    """What `list_tables` reports: which plan data exists, for which years, and what to call it."""

    plan_year: int | None
    """The year this run pinned, or `None` when the question did not name one."""

    partitions: dict[str, str]
    """Source → the partition these tables come from, after resolving `plan_year`."""

    available_partitions: dict[str, list[str]]
    """Source → every partition vendored on this machine. The honest answer to *"what years do you
    have?"*, and what makes an unvendored year an abstention rather than a guess."""

    tables: list[TableInfo]


class ColumnInfo(BaseModel):
    """One column, described by its **values** rather than its type.

    Every column in the mirror is `VARCHAR`, so a type listing says nothing. What a query needs to
    know is what the values look like: `'$450 '` carries a dollar sign and a trailing space,
    `'Not Applicable'` sits beside `''` and means something different, and a yes/no flag is `'Yes'`
    or `'No'`, not a boolean.
    """

    name: str

    examples: list[str] = Field(default_factory=list)
    """Up to three real values from the first rows of the table."""

    distinct: int | None = None
    """Approximate number of distinct values, for judging whether a filter will be selective."""

    common: dict[str, int] | None = None
    """For a low-cardinality column, every value with its row count — so an equality filter can be
    written against what is actually there rather than against a guess."""

    macro: str | None = None
    """`to_usd` or `to_pct` when the values look like money or a percentage. Use it to **filter and
    order**; quote the raw cell when reporting a value."""


class TableDescription(BaseModel):
    """What `describe_table` reports."""

    table: str
    view: str
    partition: str
    rows: int

    columns: list[ColumnInfo]

    total_columns: int
    """How many columns the table has, which is not always how many are listed — see `truncated`."""

    truncated: bool
    """True when `columns` is a slice. Narrow with the `contains` argument rather than reading a
    wide table in full."""

    notes: list[str] = Field(default_factory=list)
    """Correctness warnings that apply to this specific table — a blank `STATE` on standalone drug
    plans, a service area that lists no counties because it covers a whole state. Read them: each
    one is a documented way for a reasonable-looking query to be wrong."""


class Row(BaseModel):
    """One row a query returned, and the only thing an answer may cite."""

    row_id: str
    """Cite this exact string. `<source>/<partition>/<table>#q<n>.<row>` — it names the query that
    produced the row, because a projection or an aggregate has no key of its own."""

    view: str
    """What this row is about: `<source>/<partition>/<table>`, or both tables of a join."""

    source: str | None = None
    partition: str | None = None
    """Where the row came from. Carried rather than parsed back out of `row_id`, because
    `runtime.py` builds the citation's title and URL from these and a citation must never depend on
    string-splitting an identifier."""

    cells: dict[str, str | None]
    """Column → value, exactly as the query returned it. `null` is SQL NULL, which is **not** the
    same as an empty string in this data."""

    url: str | None = None
    """Where a reader can re-fetch **this exact record**, when such an address exists.

    `None` for a mirror row, and that is not an omission: there is no public address for row 41,922
    of a Parquet file, so a citation to one links to the source dataset instead. A Phase 3 live-API
    record *does* have one — the query URL that produced it — and this is where it travels
    (docs/structured-api-tools.md §14b). Additive, and the mirror path is untouched by it."""

    title: str | None = None
    """A display label for the citation, when `view` and `partition` do not produce a good one.

    `None` for a mirror row, whose `source/partition/table` triple already reads well. A live record
    has no partition, so without this every live citation would render as the generic "Structured
    query result" — losing exactly the provenance §14c asks to be carried, such as which FDA label
    version a warning was read from."""


class QueryResult(BaseModel):
    """What `query_structured` reports."""

    sql: str
    """The statement that ran, as it ran."""

    columns: list[str]
    rows: list[Row]

    row_count: int
    """How many rows came back. **Zero is a finding, not a failure**: it means this table holds no
    such row, which is different from "not covered" and different from a value of
    `'Not Applicable'`. Say which one you mean."""

    truncated: bool
    """True when the row cap cut the result. Narrow the query rather than reasoning from a slice."""

    partitions: dict[str, str]
    """The partitions this query was allowed to read — the plan year, made explicit."""

    duration_ms: int
