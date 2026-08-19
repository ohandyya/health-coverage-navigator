"""What structured data is on disk, and whether it matches the manifest that describes it.

The mirror is git-ignored (`data/processed/<source>/<partition>/*.parquet`) while the manifest that
records what a complete build looks like — `data/raw/<source>/catalog.json` — is committed. That
split is what makes the three states in docs/relational-tool.md §8 distinguishable:

- **not built** — no partition directory. A fresh clone, and an expected state, not a bug.
- **stale / partial** — a partition exists but a table is missing, or its row count disagrees with
  the manifest. Refused rather than tolerated, on the same argument `VectorsStaleError` makes: a
  missing store is visibly missing, while a half-built one answers every query plausibly and
  wrongly.
- **built** — every expected table present, every row count matching.

**Table names are derived from the manifest, not hardcoded here.** The downloaders own the
slug → filename mapping (`TABLES` in `scripts/download_exchange_puf.py`, `FILES` in
`scripts/download_part_d_spuf.py`), and copying it into `src/` would be a second source of truth
that drifts on the first new table. `catalog.json` records the filename it wrote, so the stem is
read back off that — see `_stem`.

This module knows nothing about DuckDB. It answers "what is on disk and is it whole"; `store.py`
answers "what does it say".
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from health_coverage_navigator.paths import PROCESSED_DIR, RAW_DIR

#: The two vendored structured sources. A closed set, like `CorpusName` — and deliberately not
#: extended by Phase 3: the live APIs answer in the same *lane* but hold no tables here.
StructuredSource = Literal["exchange_puf", "part_d_spuf"]

STRUCTURED_SOURCES: tuple[StructuredSource, ...] = ("exchange_puf", "part_d_spuf")

#: How each source's catalog names a partition, and what the partition means. Exchange PUFs are
#: published per plan **year**; the Part D SPUF is rebuilt every **quarter**.
PARTITION_KEY: dict[StructuredSource, str] = {"exchange_puf": "year", "part_d_spuf": "quarter"}

#: Which catalog field carries the filename the downloader wrote, and therefore the table stem.
FILENAME_KEY: dict[StructuredSource, str] = {
    "exchange_puf": "csv_filename",
    "part_d_spuf": "txt_filename",
}

#: Human-readable source names, for a citation's title. Keyed by plain `str` rather than
#: `StructuredSource`, because the lookups come from a `Row`, whose source is whatever the query
#: touched — including nothing at all, for an aggregate over no vendored table.
SOURCE_LABEL: dict[str, str] = {
    "exchange_puf": "Health Insurance Exchange PUF",
    "part_d_spuf": "Medicare Part D SPUF",
}

#: Where a reader goes to check a cited row against CMS. The landing page rather than the zip URL
#: the downloader fetched: a citation is for a human, and a 12 MB download is not a source they can
#: open. The exact artifact is recorded in `catalog.json`, which is committed.
SOURCE_URL: dict[str, str] = {
    "exchange_puf": "https://www.cms.gov/marketplace/resources/data/public-use-files",
    "part_d_spuf": (
        "https://data.cms.gov/provider-summary-by-type-of-service/medicare-part-d-prescribers/"
        "quarterly-prescription-drug-plan-formulary-pharmacy-network-and-pricing-information"
    ),
}

#: Correctness warnings attached to specific tables, surfaced by `describe_table`. Every one is a
#: documented way for a reasonable-looking query to be wrong; the sources are the per-source data
#: guides, and the reasoning is in docs/relational-tool.md §3 and §14a.
TABLE_NOTES: dict[str, list[str]] = {
    "part_d_spuf.plan_information": [
        "STATE and COUNTY_CODE are populated only for Medicare Advantage rows. Every standalone "
        "PDP is located by PDP_REGION_CODE instead, so filtering this table by STATE silently "
        "drops exactly the plan type a 'what does my drug plan cover' question is usually about.",
        "A plan is identified by CONTRACT_ID + PLAN_ID + SEGMENT_ID together. PLAN_ID alone "
        "repeats across contracts.",
        "Rows with PLAN_SUPPRESSED_YN = 'Y' appear in no other file, so a formulary join returns "
        "nothing for them. That is withheld data, not 'the drug is not covered'.",
    ],
    "part_d_spuf.basic_drugs_formulary": [
        "Keyed by FORMULARY_ID, not by plan — one formulary backs many plans. To answer 'does MY "
        "plan cover X', read FORMULARY_ID from plan_information first.",
        "A drug flagged for prior authorization, step therapy or a quantity limit IS covered; the "
        "flags are conditions, not a refusal. Report them alongside the tier.",
    ],
    "exchange_puf.plan_attributes": [
        "One plan is many rows: each CSR variant of a plan is its own row, with its own "
        "deductibles and MOOP. Filtering by StandardComponentId alone returns all of them, and "
        "they disagree — always report which CSRVariationType a figure came from.",
        "There are 36 max-out-of-pocket columns (MEHB/DEHB/TEHB x network tier x "
        "individual/family). Use describe_table's `contains` argument to find the right one rather "
        "than guessing a name.",
    ],
    "exchange_puf.service_area": [
        "A row with CoverEntireState = 'Yes' lists no county and no ZIP. An empty result for such "
        "a plan means 'sold statewide', which is the opposite of 'sold nowhere' — check that "
        "column before reading zero rows as an absence.",
        "ServiceAreaId is unique only within an issuer and year. Join on BusinessYear + StateCode "
        "+ IssuerId + ServiceAreaId, or you will return another company's counties.",
        "County is a FIPS code, not a name, and ZipCodes is populated only for partial counties. "
        "This table answers county coverage, not ZIP coverage.",
    ],
}


class StructuredNotBuiltError(RuntimeError):
    """No structured mirror on this machine.

    A real, expected state rather than a bug: the Parquet mirrors are git-ignored (both data
    guides' *Where the data lands*), so a fresh clone has none until the downloaders run. Raised
    rather than degraded around — the committed sample slices cover one state and four contracts,
    and answering a national question from them would be wrong in a way nothing in the response
    would reveal.
    """


class StructuredStaleError(RuntimeError):
    """The mirror on disk disagrees with the committed manifest that describes it.

    A partial build, an interrupted download, or a hand-edited Parquet. Refused for the same
    reason `VectorsStaleError` is: it answers everything, plausibly, from data nobody can name.
    """


@dataclass(frozen=True, slots=True)
class TableFile:
    """One table of one partition, on disk and accounted for in the manifest."""

    source: StructuredSource
    table: str
    """Bare stem, e.g. `plan_attributes`."""

    partition: str
    path: Path
    expected_rows: int

    @property
    def qualified(self) -> str:
        """`<source>.<table>` — how a tool argument and a gold question name it."""
        return f"{self.source}.{self.table}"

    @property
    def view(self) -> str:
        """`<source>.<table>_<partition>` — how SQL names it.

        The partition is in the identifier on purpose: a query then *states* which plan year it
        read, so the trace and the row id carry it without anyone having to plumb it separately.
        """
        return f"{self.source}.{self.table}_{self.partition}"


def catalog_path(source: StructuredSource, raw_dir: Path | None = None) -> Path:
    """The committed per-source download manifest."""
    return (RAW_DIR if raw_dir is None else raw_dir) / source / "catalog.json"


def _stem(source: StructuredSource, entry: dict) -> str:
    """The Parquet stem for one catalog entry, read back off the filename the downloader wrote.

    `Benefits_Cost_Sharing_PUF.csv` → `benefits_cost_sharing`; `basic_drugs_formulary.txt` →
    `basic_drugs_formulary`. Derived rather than mapped so a table added to a downloader needs no
    edit here — and an entry whose filename is missing is a manifest bug worth failing on.
    """
    filename = entry.get(FILENAME_KEY[source])
    if not isinstance(filename, str) or not filename:
        raise StructuredStaleError(
            f"{source}: catalog entry {entry.get('table') or entry.get('file')!r} has no "
            f"{FILENAME_KEY[source]}, so the table it produced cannot be identified."
        )
    return filename.rsplit(".", 1)[0].lower().removesuffix("_puf")


def manifest(source: StructuredSource, raw_dir: Path | None = None) -> dict[tuple[str, str], int]:
    """`(partition, table) → expected row count`, from the committed catalog.

    Returns `{}` when the catalog is absent, which is the fresh-clone case rather than an error —
    `discover()` turns the absence of *data* into `StructuredNotBuiltError`, and this function
    stays free of that judgement so a test can drive the two apart.
    """
    path = catalog_path(source, raw_dir)
    if not path.is_file():
        return {}
    entries = json.loads(path.read_text(encoding="utf-8"))
    key = PARTITION_KEY[source]
    return {(str(entry[key]), _stem(source, entry)): int(entry["row_count"]) for entry in entries}


def discover(
    processed_dir: Path | None = None, raw_dir: Path | None = None
) -> dict[StructuredSource, dict[str, list[TableFile]]]:
    """Every vendored table, grouped `source → partition → tables`.

    Raises `StructuredNotBuiltError` when no source has a single partition, and
    `StructuredStaleError` when a partition is present but incomplete — a table the manifest names
    is missing, or a Parquet file the manifest does not name has appeared.

    A partition with *no* manifest entries at all is skipped rather than refused: that is a
    plan year someone downloaded without committing the catalog, and refusing to boot over it would
    make an unrelated year's data unreadable.
    """
    root = PROCESSED_DIR if processed_dir is None else processed_dir
    found: dict[StructuredSource, dict[str, list[TableFile]]] = {}

    for source in STRUCTURED_SOURCES:
        expected = manifest(source, raw_dir)
        source_dir = root / source
        if not source_dir.is_dir():
            continue

        partitions: dict[str, list[TableFile]] = {}
        for partition_dir in sorted(p for p in source_dir.iterdir() if p.is_dir()):
            # `sample/` is the committed slice — one state, four contracts. It is a fixture, not a
            # partition, and serving it as one is the exact failure §2's decision refuses.
            if partition_dir.name == "sample":
                continue
            partition = partition_dir.name
            on_disk = sorted(p.stem for p in partition_dir.glob("*.parquet"))
            named = sorted(table for (part, table) in expected if part == partition)
            if not named:
                continue
            if on_disk != named:
                raise StructuredStaleError(
                    f"{source}/{partition}: the mirror holds {on_disk or 'no tables'} but "
                    f"catalog.json names {named}. Re-run the downloader for this partition."
                )
            partitions[partition] = [
                TableFile(
                    source=source,
                    table=table,
                    partition=partition,
                    path=partition_dir / f"{table}.parquet",
                    expected_rows=expected[(partition, table)],
                )
                for table in named
            ]
        if partitions:
            found[source] = partitions

    if not found:
        raise StructuredNotBuiltError(
            f"no structured plan data under {root}. The Parquet mirrors are git-ignored, so a "
            f"fresh clone has none — run `make puf` to build them (~24 MB over the wire)."
        )
    return found


def source_label(source: str | None) -> str:
    """Human-readable source name for a citation title, tolerant of a row with no single source.

    A query touching no vendored table, or joining across two of them, has no one source to name —
    and a citation that invented one would be exactly the kind of plausible-looking provenance the
    whole lane exists to prevent.
    """
    return SOURCE_LABEL.get(source, "Structured plan data") if source else "Structured query"


def source_url(source: str | None) -> str | None:
    """Where a reader checks a cited row against CMS, or `None` when there is no single source."""
    return SOURCE_URL.get(source) if source else None


def partition_year(partition: str) -> int:
    """The plan year a partition belongs to: `2026` → 2026, `2026Q2` → 2026.

    The two sources partition on different clocks, and this is the one place that knows it. Every
    caller asks the same question — *does this partition answer a question about year N* — so the
    quarter is dropped here rather than at each call site.
    """
    return int(partition[:4])


def resolve_partition(partitions: list[str], plan_year: int | None) -> str | None:
    """Which partition answers a question about `plan_year`, or `None` if none does.

    Newest-first within the year, so `2026` picks `2026Q4` over `2026Q1`. An unset `plan_year`
    takes the newest partition outright — and the tool result says which, so the answer can name
    the year it read rather than implying every year at once.

    Returning `None` rather than falling back to an adjacent year is the whole point: CMS keeps
    many years live, and quietly answering 2026 for a 2025 question is this domain's most common
    correctness bug (docs/plan.md, cross-cutting principles).
    """
    if not partitions:
        return None
    ordered = sorted(partitions, reverse=True)
    if plan_year is None:
        return ordered[0]
    return next((p for p in ordered if partition_year(p) == plan_year), None)
