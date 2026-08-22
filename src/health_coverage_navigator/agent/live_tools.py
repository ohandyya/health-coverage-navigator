"""The live half of the structured lane — the tools, and what only makes sense *inside a run*.

`live/` does the fetching. This module does the same two jobs `tools.py` does for the reference
lane, `structured_tools.py` for the mirror half of this one, and `web_tools.py` for the web:

**The trace.** Every call appends a `tool_call` / `tool_result` pair to `deps.trace`. What it
carries here that the mirror tools cannot is *which upstream answered* — the phase's whole question
is whether the agent reached for a live endpoint when a vendored row would have done, and a trace
that does not say cannot be graded (docs/structured-api-tools.md §16a).

**What the agent has actually seen.** Every live record is recorded in `deps.seen_rows` — the same
dictionary the mirror tools write to, not a fourth one — and the output validator refuses a citation
of anything else. §14a argues why a live record *is* a row rather than a new citation shape: the
claim it makes is identical in kind. What distinguishes it is the id namespace (`fda#…`) and the
fact that it carries its own re-fetchable URL, which a mirror row cannot.

**The budget is terminal, not a retry.** A spent lookup allowance comes back as a result the model
reads and acts on, never as `ModelRetry` — the allowance does not refill inside a run, so a retry
could only produce the same refusal while spending the grounding guardrail's budget on it. Phase 2
has a test asserting exactly this distinction for the web lane; the same rule holds here.
"""

import time
from typing import get_args

from pydantic_ai import ModelRetry, RunContext

from health_coverage_navigator.agent.deps import AnswerDeps
from health_coverage_navigator.config import get_config
from health_coverage_navigator.live.models import (
    County,
    CoverageResult,
    DrugLabelResult,
    DrugMatches,
    DrugRecallResult,
    LabelSectionName,
    PlanMatches,
    ProviderResult,
)
from health_coverage_navigator.structured.models import Row

#: What a tool says when the lane is configured but no client was built. Unreachable in the app,
#: reachable in a test or a script that builds `AnswerDeps` by hand — where a `None` dereference
#: would read as a bug in the agent rather than as a missing dependency.
NO_CLIENT = (
    "The FDA drug database is not available in this session. Answer from the reference corpus or "
    "the plan data if they cover the question, and otherwise say you could not check it."
)

NO_MARKETPLACE = (
    "Marketplace plan data is not available in this session (no CMS API key is configured). You "
    "cannot look up plans, prices, or which plans cover a drug. Say so plainly if the question "
    "needs one of those; the vendored plan tables may still answer a question about a filed plan "
    "year."
)

NO_NPPES = (
    "The NPI registry is not available in this session. You cannot look up a provider. Say so "
    "plainly if the question needs one."
)

BUDGET_SPENT = (
    "This run's live-lookup budget is spent ({limit} lookups). Looking up again will not help — "
    "answer from what you have already retrieved, or say what you could not check."
)

#: Derived from the type rather than restated, so the runtime check and the signature cannot drift.
SECTIONS = get_args(LabelSectionName)


async def drug_label(
    ctx: RunContext[AnswerDeps],
    name: str,
    section: str = "indications",
) -> DrugLabelResult:
    """Read one section of a drug's **FDA-approved label** — the official prescribing information.

    Use this for what a drug *is for*, what it *warns about*, how it *interacts*, or what *side
    effects* are documented. This is the FDA's own labelling record, so it is authoritative for what
    the label says — and it is a labelling document, **not** clinical advice and **not** anything
    about cost or coverage.

    **It cannot tell you whether a plan covers the drug.** That is `check_drug_coverage`, and the
    two are easy to confuse because both are "about a drug". Coverage is a property of a plan; a
    label is a property of the medicine.

    **Ask for one section at a time.** A whole label runs to a quarter of a million characters, so
    this returns only the section you name. If you need warnings *and* interactions, call twice.

    **Read `label_found` and `sections` separately — they mean different things.**
    `label_found=False` means the FDA holds no label under that name; try the generic name, or say
    the drug was not found. `label_found=True` with empty `sections` means the label exists but does
    not carry that section, which is normal for over-the-counter drugs — say the label does not
    include it, never that the drug has no warnings.

    **`rxcuis` on the result is the join to coverage.** Those are the identifiers
    `check_drug_coverage` takes, so a label lookup is one way to resolve a drug name before asking
    whether a plan covers it.

    Args:
        name: The drug name, brand or generic. Brand is tried first, then generic.
        section: Which part of the label — `indications`, `warnings`, `interactions`,
            `adverse_reactions`, or `dosage`. `warnings` returns the boxed warning too when the
            drug has one; say so if it does, because that is the FDA's most serious warning class.
    """
    deps = ctx.deps
    client = deps.openfda
    if client is None:  # pragma: no cover - `select_tools` makes this unreachable in the app
        raise ModelRetry(NO_CLIENT)
    if section not in SECTIONS:
        raise ModelRetry(f"section must be one of {list(SECTIONS)}; got {section!r}.")

    started = time.perf_counter()
    limit = get_config().live.max_calls_per_run
    key = f"drug_label:{name.strip().lower()}:{section}"
    cached = deps.cached_live(key)
    if isinstance(cached, DrugLabelResult):
        result, hit = cached, True
    elif deps.live_calls >= limit:
        result, hit = (
            DrugLabelResult(
                query=name, requested_section=section, unavailable=BUDGET_SPENT.format(limit=limit)
            ),
            False,
        )
    else:
        result = await client.drug_label(name, section, sequence=deps.next_live_call())
        deps.remember_rows(_label_rows(result))
        deps.remember_live(key, result)
        hit = False

    deps._record(
        "drug_label",
        {"name": name, "section": section},
        _cached_note(_summarize_label(result), hit),
        int((time.perf_counter() - started) * 1000),
    )
    return result


async def drug_recalls(ctx: RunContext[AnswerDeps], name: str) -> DrugRecallResult:
    """Check the FDA's **enforcement (recall) database** for a drug.

    **An empty result is a real answer, not a failed search.** If `recalls` comes back empty and
    `unavailable` is not set, the FDA holds no recall for that drug — say so plainly and with
    confidence. Do not soften it into "I could not find any", which a reader hears as the search
    having failed.

    **When there are recalls, three details change what the answer means, and omitting any of them
    misleads:**

    - `classification` — Class I is a reasonable probability of serious harm; Class III is unlikely
      to cause harm. A reader who hears only "recalled" assumes Class I. Name the class.
    - `status` — a `Terminated` recall is finished. Reporting a resolved 2013 recall as though it
      were a current safety problem would alarm someone over nothing. Pair it with
      `recall_initiation_date`.
    - `product_description` — recalls are **batch-specific**. Certain lots being recalled is not the
      drug being recalled, and the difference is the whole answer for someone holding a bottle.

    Use this when someone asks whether a drug is safe, has been pulled, or has had a problem. For
    what the drug is for or what it warns about, use `drug_label`.

    Args:
        name: The drug name, brand or generic. Both are searched.
    """
    deps = ctx.deps
    client = deps.openfda
    if client is None:  # pragma: no cover - `select_tools` makes this unreachable in the app
        raise ModelRetry(NO_CLIENT)

    started = time.perf_counter()
    limit = get_config().live.max_calls_per_run
    key = f"drug_recalls:{name.strip().lower()}"
    cached = deps.cached_live(key)
    if isinstance(cached, DrugRecallResult):
        result, hit = cached, True
    elif deps.live_calls >= limit:
        result, hit = (
            DrugRecallResult(query=name, unavailable=BUDGET_SPENT.format(limit=limit)),
            False,
        )
    else:
        result = await client.drug_recalls(name, sequence=deps.next_live_call())
        deps.remember_rows(_recall_rows(result))
        deps.remember_live(key, result)
        hit = False

    result.lookups_remaining = max(0, limit - deps.live_calls)
    deps._record(
        "drug_recalls",
        {"name": name},
        _cached_note(_summarize_recalls(result), hit),
        int((time.perf_counter() - started) * 1000),
    )
    return result


# ------------------------------------------------------------ Marketplace --------------------


async def find_drug(ctx: RunContext[AnswerDeps], name: str) -> DrugMatches:
    """Resolve a drug name to the **RxCUI identifiers** `check_drug_coverage` needs.

    Coverage is asked per RxCUI and nobody types one, so this is the first half of every
    drug-coverage question — call it before `check_drug_coverage`, not instead of it.

    **A drug usually resolves to several RxCUIs**, one per strength and form: "Lipitor" comes back
    as 10 mg, 20 mg, 40 mg and 80 mg tablets. They can be covered differently. If the question does
    not say which strength, either ask, or check and **say which one you checked** — silently
    picking one and reporting it as "Lipitor" is how a confident wrong answer gets made here.

    An empty result means the Marketplace does not recognise the name. Check the spelling, or try
    the generic name if you used a brand.

    Args:
        name: The drug name as a person would write it. At least three characters.
    """
    deps = ctx.deps
    client = deps.marketplace
    if client is None:
        raise ModelRetry(NO_MARKETPLACE)

    started = time.perf_counter()
    limit = get_config().live.max_calls_per_run
    key = f"find_drug:{name.strip().lower()}"
    cached = deps.cached_live(key)
    if isinstance(cached, DrugMatches):
        result, hit = cached, True
    elif deps.live_calls >= limit:
        result, hit = DrugMatches(query=name, unavailable=BUDGET_SPENT.format(limit=limit)), False
    else:
        deps.next_live_call()
        result = await client.find_drug(name)
        deps.remember_live(key, result)
        hit = False

    deps._record(
        "find_drug",
        {"name": name},
        _cached_note(_summarize_drug_matches(result), hit),
        int((time.perf_counter() - started) * 1000),
    )
    return result


async def check_drug_coverage(
    ctx: RunContext[AnswerDeps],
    rxcuis: list[str],
    plan_ids: list[str],
    year: int | None = None,
) -> CoverageResult:
    """Check whether specific plans cover specific drugs, **right now**.

    This is the live formulary — what a plan covers today, which is what someone deciding on a plan
    or a prescription actually needs. The vendored plan tables answer the neighbouring question of
    what a plan *filed* for a given year; when the question is "is it covered", prefer this.

    **Four answers, and three of them are not "no":**

    - `Covered` — the plan covers it.
    - `GenericCovered` — the plan does **not** cover the drug as named but **does** cover its
      generic, which `generic_rxcui` identifies. Report this as what it is; calling it "not covered"
      would be wrong and would send a reader away from a drug they can get.
    - `DataNotProvided` — the plan filed no formulary data for it. Say the information is not
      available, not that the drug is not covered.
    - `NotCovered` — the plan does not cover it.

    Get `rxcuis` from `find_drug` and `plan_ids` from `find_plans` (or from the vendored plan
    tables — they are the same 14-character identifiers). Do not invent either.

    Args:
        rxcuis: RxCUI identifiers from `find_drug`.
        plan_ids: 14-character plan IDs, e.g. from `find_plans`.
        year: Plan year. Defaults to the current one — pass it only when the question names a
            different year, because a formulary answer is only true for the year it was asked about.
    """
    deps = ctx.deps
    client = deps.marketplace
    if client is None:
        raise ModelRetry(NO_MARKETPLACE)
    if not rxcuis or not plan_ids:
        raise ModelRetry(
            "check_drug_coverage needs at least one rxcui and one plan_id. Use find_drug to "
            "resolve a drug name first, and find_plans to find plan IDs."
        )

    started = time.perf_counter()
    limit = get_config().live.max_calls_per_run
    if deps.live_calls >= limit:
        result = CoverageResult(unavailable=BUDGET_SPENT.format(limit=limit))
    else:
        resolved = await _resolve_year(deps, year)
        if resolved is None:
            result = CoverageResult(
                unavailable=(
                    "The Marketplace plan year could not be determined, so no coverage was "
                    "checked. Say you could not check it."
                )
            )
        else:
            result = await client.check_drug_coverage(
                rxcuis, plan_ids, resolved, sequence=deps.next_live_call()
            )
            deps.remember_rows(_coverage_rows(result))

    result.lookups_remaining = max(0, limit - deps.live_calls)
    deps._record(
        "check_drug_coverage",
        {"rxcuis": rxcuis, "plan_ids": plan_ids, "year": result.year},
        _summarize_coverage(result),
        int((time.perf_counter() - started) * 1000),
    )
    return result


async def find_plans(
    ctx: RunContext[AnswerDeps],
    zipcode: str,
    ages: list[int],
    income: float,
    year: int | None = None,
    countyfips: str | None = None,
) -> PlanMatches:
    """Find health plans a household can actually buy, with **this year's real premiums**.

    Takes what a person knows — their ZIP, who is in the household, and roughly what they earn — and
    returns the plans on sale to them, priced. `premium` is the full monthly cost and
    `premium_with_credit` is what they would pay after the subsidy they qualify for; **quote both**,
    because the gap between them is usually the most useful number in the answer.

    **This is also how you find out whether HealthCare.gov serves a state at all.** Some states run
    their own marketplaces, and for those `state_not_served` comes back set. That is an answer, not
    a failure: tell the reader their state runs its own exchange rather than saying no plans are
    available.

    **Which states those are changes, and your reference documents may be out of date about it.**
    A state that used HealthCare.gov when your documents were written may run its own marketplace
    now. So when a question turns on which exchange serves somewhere, call this rather than quoting
    a document — this reflects what CMS is selling today, and the document reflects when it was
    written.

    **A ZIP can straddle two counties, and premiums differ between them.** When that happens you
    will be asked to pick one by its FIPS code — you cannot answer for both at once, so say which
    county the prices you quote are for.

    Args:
        zipcode: Five-digit ZIP code.
        ages: One age per person in the household, e.g. `[38, 36, 7]` for a family of three.
        income: Estimated annual household income in dollars, which decides the subsidy.
        year: Plan year. Defaults to the current one.
        countyfips: The county's 5-digit FIPS code. Only needed when a ZIP spans more than one and
            you were asked to choose.
    """
    deps = ctx.deps
    client = deps.marketplace
    if client is None:
        raise ModelRetry(NO_MARKETPLACE)
    if not ages:
        raise ModelRetry("find_plans needs at least one age — one per person in the household.")

    started = time.perf_counter()
    limit = get_config().live.max_calls_per_run
    if deps.live_calls >= limit:
        result = PlanMatches(unavailable=BUDGET_SPENT.format(limit=limit))
        deps._record(
            "find_plans",
            {"zipcode": zipcode, "ages": ages, "income": income},
            _summarize_plans(result),
            int((time.perf_counter() - started) * 1000),
        )
        result.lookups_remaining = 0
        return result

    # **The cache key carries the whole household, not just the ZIP.** Two people in one ZIP with
    # different incomes qualify for different subsidies and therefore see different prices, so a
    # key that omitted income would hand the second caller the first caller's answer. This repo has
    # now got a cache key wrong three times in two lanes; this is the shape of the mistake.
    key = (
        f"find_plans:{zipcode.strip()}:{countyfips or ''}:"
        f"{','.join(str(a) for a in sorted(ages))}:{income:.2f}:{year or ''}"
    )
    cached = deps.cached_live(key)
    if isinstance(cached, PlanMatches):
        cached.lookups_remaining = max(0, limit - deps.live_calls)
        deps._record(
            "find_plans",
            {"zipcode": zipcode, "ages": ages, "income": income, "year": cached.year},
            _cached_note(_summarize_plans(cached), True),
            int((time.perf_counter() - started) * 1000),
        )
        return cached

    # The ZIP-to-county hop, folded in rather than exposed as a tool (§11): the model cannot know a
    # FIPS code, and spending a routing decision on a mechanical lookup buys nothing.
    deps.next_live_call()
    counties, unavailable = await client.counties_for_zip(zipcode)
    if unavailable:
        result = PlanMatches(unavailable=unavailable)
    elif not counties:
        result = PlanMatches(
            unavailable=(
                f"No county could be found for ZIP {zipcode}. Check the ZIP code; it may be "
                f"mistyped."
            )
        )
    else:
        chosen, others = _choose_county(counties, countyfips, zipcode)
        resolved = await _resolve_year(deps, year)
        if resolved is None:
            result = PlanMatches(
                county=chosen,
                unavailable=(
                    "The Marketplace plan year could not be determined, so no plans were looked "
                    "up. Say you could not check."
                ),
            )
        else:
            result = await client.find_plans(
                county=chosen,
                zipcode=zipcode,
                ages=ages,
                income=income,
                year=resolved,
                limit=get_config().live.max_plans,
                sequence=deps.next_live_call(),
            )
            result.other_counties = others
            deps.remember_rows(_plan_rows(result))
            deps.remember_live(key, result)

    result.lookups_remaining = max(0, limit - deps.live_calls)
    deps._record(
        "find_plans",
        {"zipcode": zipcode, "ages": ages, "income": income, "year": result.year},
        _summarize_plans(result),
        int((time.perf_counter() - started) * 1000),
    )
    return result


def _choose_county(counties: list[County], countyfips: str | None, zipcode: str):
    """Pick the county to price against, and report the ones that were not.

    **Answer, then qualify — do not refuse.** The first version raised a `ModelRetry` asking the
    model to choose, on the reasoning that a silent pick returns a real number answering a different
    question. The reasoning was right about *silent* and wrong about *pick*: measured, the agent
    asked the reader which of Davidson and Randolph county it should use and **abstained on a
    question it could have answered**. Someone who asks what plans they can buy is worse served by
    a clarifying question than by prices plus "these are for Davidson County; Randolph differs".

    So the ambiguity travels in `PlanMatches.other_counties` where the answer can carry it, instead
    of stopping the run. An explicit `countyfips` still wins, and naming one that does not belong to
    the ZIP is still a retry, because that one really is a mistake to correct.
    """
    if countyfips:
        for county in counties:
            if county.fips == countyfips:
                return county, [c for c in counties if c.fips != countyfips]
        raise ModelRetry(
            f"countyfips {countyfips!r} is not one of the counties for ZIP {zipcode}. Choose one "
            f"of: {', '.join(f'{c.name} ({c.fips})' for c in counties)}."
        )
    return counties[0], counties[1:]


async def _resolve_year(deps: AnswerDeps, year: int | None) -> int | None:
    """The plan year to ask about: what the caller named, what the request pinned, or CMS's current.

    Asked once per run and cached on `AnswerDeps` (§7c), so the two halves of the structured lane
    agree about which year they are discussing instead of each assuming one.
    """
    if year is not None:
        return year
    if deps.plan_year is not None:
        return deps.plan_year
    if deps.market_year is None and deps.marketplace is not None:
        deps.market_year = await deps.marketplace.market_year()
    return deps.market_year


# ---------------------------------------------------------------- NPPES ----------------------


async def lookup_provider(ctx: RunContext[AnswerDeps], npi: str) -> ProviderResult:
    """Look up a healthcare provider by **NPI** in the national registry.

    Answers who a National Provider Identifier belongs to, what their specialty is, whether the
    number is still active, and roughly where they practise. This is the **only** provider data
    available to you — nothing about providers is held locally.

    **It cannot tell you whether a provider is in a plan's network.** That is the plan's own
    provider directory, which you do not have. The registry says what a provider *is*, never who
    pays for them, and answering a network question from a registry record would be a confident
    wrong answer to the question people most often mean.

    **A provider can hold several specialties, with one marked `primary`.** They come back primary
    first. Say which one is primary rather than reporting the list as if it were one specialty.

    Three non-answers that are not the same as each other: `invalid` means the number is malformed
    (an NPI is ten digits with a check digit, so a typo is detectable — tell the reader);
    `found=False` means the registry holds no such NPI; `unavailable` means the registry could not
    be reached and nothing was checked.

    Args:
        npi: A ten-digit National Provider Identifier.
    """
    deps = ctx.deps
    client = deps.nppes
    if client is None:
        raise ModelRetry(NO_NPPES)

    started = time.perf_counter()
    limit = get_config().live.max_calls_per_run
    key = f"lookup_provider:{npi.strip()}"
    cached = deps.cached_live(key)
    if isinstance(cached, ProviderResult):
        result, hit = cached, True
    elif deps.live_calls >= limit:
        result, hit = ProviderResult(npi=npi, unavailable=BUDGET_SPENT.format(limit=limit)), False
    else:
        result = await client.lookup_npi(npi, sequence=deps.next_live_call())
        deps.remember_rows(_provider_rows(result))
        deps.remember_live(key, result)
        hit = False

    result.lookups_remaining = max(0, limit - deps.live_calls)
    deps._record(
        "lookup_provider",
        {"npi": npi},
        _cached_note(_summarize_provider(result), hit),
        int((time.perf_counter() - started) * 1000),
    )
    return result


def _provider_rows(result: ProviderResult) -> list[Row]:
    """The citable record for a provider lookup.

    A **not-found** lookup is citable too, for the same reason an empty recall search is: "the
    registry holds no such NPI" is a finding, and without a row behind it the grounding validator
    would force an abstention on a question that was answered.
    """
    if result.unavailable:
        return []
    if result.invalid:
        # A malformed NPI is a finding about the *input*, and citable for the same reason an empty
        # search is: the agent established it, and without a row the grounding validator would force
        # an abstention on a question it actually answered.
        return [
            Row(
                row_id=result.row_id,
                view="nppes/npi_registry",
                source="nppes",
                cells={"npi": result.npi, "rejected_because": result.invalid},
                url=result.source_url,
                title=f"NPI registry · {result.npi} · not a valid identifier",
            )
        ]
    if not result.found or result.provider is None:
        return [
            Row(
                row_id=result.row_id,
                view="nppes/npi_registry",
                source="nppes",
                cells={"npi": result.npi, "found": "0"},
                url=result.source_url,
                title=f"NPI registry · {result.npi} · not found",
            )
        ]
    provider = result.provider
    primary = next((t for t in provider.taxonomies if t.primary), None)
    return [
        Row(
            row_id=provider.row_id,
            view="nppes/npi_registry",
            source="nppes",
            cells={
                "npi": provider.npi,
                "name": provider.name,
                "enumeration_type": provider.enumeration_type,
                "primary_specialty": primary.description if primary else None,
                "all_specialties": ", ".join(t.description for t in provider.taxonomies) or None,
                "status": provider.status,
                "city": provider.city,
                "state": provider.state,
            },
            url=result.source_url,
            title=f"NPI registry · {provider.name or provider.npi}",
        )
    ]


def _summarize_provider(result: ProviderResult) -> str:
    if result.unavailable:
        return "NPI registry unavailable — nothing looked up"
    if result.invalid:
        return f"{result.npi} is not a well-formed NPI"
    if not result.found or result.provider is None:
        return f"no provider registered under NPI {result.npi}"
    primary = next((t for t in result.provider.taxonomies if t.primary), None)
    return f"{result.provider.name} — {primary.description if primary else 'no specialty listed'}"


def _coverage_rows(result: CoverageResult) -> list[Row]:
    """One citable record per drug-plan pair. A `DataNotProvided` answer is citable too — it is a
    fact about what the plan filed, and the reader is entitled to see that it was checked."""
    if result.unavailable:
        return []
    return [
        Row(
            row_id=item.row_id,
            view="marketplace/drug_coverage",
            source="marketplace",
            cells={
                "rxcui": item.rxcui,
                "plan_id": item.plan_id,
                "coverage": item.coverage,
                "generic_rxcui": item.generic_rxcui,
                "year": str(result.year) if result.year else None,
            },
            url=item.source_url,
            title=f"Marketplace formulary · plan {item.plan_id} · {result.year}",
        )
        for item in result.coverage
    ]


def _plan_rows(result: PlanMatches) -> list[Row]:
    """One citable record per plan — **and one for "this state is not served", which is a finding.**

    The third instance of the same hole in one phase, after an empty recall search and an unknown
    NPI. *"Georgia runs its own marketplace rather than using HealthCare.gov"* is a fact this tool
    established, and with nothing in `seen_rows` behind it the agent did the only thing left: it
    searched the web and cited a page instead. It had the authoritative answer in hand and cited a
    worse source for it, because ours was not citable.
    """
    if result.unavailable:
        return []
    if result.state_not_served:
        return [
            Row(
                row_id=result.row_id,
                view="marketplace/plan_search",
                source="marketplace",
                # **Short values named after fields the model can see.** The first version put a
                # prose sentence in a `reason` cell and called the state cell `state`; the model
                # paraphrased the sentence and asked for a `zipcode` column that did not exist.
                # A cell is something to copy, not something to read.
                cells={
                    "state_not_served": result.state_not_served,
                    "zipcode": result.zipcode,
                    "year": str(result.year) if result.year else None,
                },
                url=None,
                title=f"HealthCare.gov · {result.state_not_served} not served",
            )
        ]
    return [
        Row(
            row_id=plan.row_id,
            view="marketplace/plan_search",
            source="marketplace",
            # Every key is a `PlanSummary` field name, and every value is that field's own string
            # form — §18c's family 2, which cost `live-03` its retry budget three ways at once:
            # a reformatted number, and two fields the model could see (`source_url`, and
            # `zipcode` on the result) that were not cells it could name.
            cells={
                "plan_id": plan.plan_id,
                "name": plan.name,
                "issuer": plan.issuer,
                "metal_level": plan.metal_level,
                "plan_type": plan.plan_type,
                "premium": _money(plan.premium),
                "premium_with_credit": _money(plan.premium_with_credit),
                "deductible": _money(plan.deductible),
                "out_of_pocket_max": _money(plan.out_of_pocket_max),
                "quality_rating": str(plan.quality_rating) if plan.quality_rating else None,
                "source_url": plan.source_url,
                "zipcode": result.zipcode,
                "year": str(result.year) if result.year else None,
            },
            url=plan.source_url,
            title=f"Marketplace plan · {plan.name} · {result.year}",
        )
        for plan in result.plans
    ]


def _money(value: float | None) -> str | None:
    """A dollar figure as a cell, spelled **exactly as the model saw it** on `PlanSummary`.

    `str(344.5)` rather than `f"{344.5:.2f}"`, and the difference is not cosmetic. The model reads
    `premium: 344.5` off the result and cites `'344.5'`; a cell holding `'344.50'` then fails the
    byte-exact comparison and costs a grounding retry — measured, and on an answer carrying ten plan
    citations it costs several, which is what made `live-03` flaky rather than wrong.

    `None` stays `None`: a missing deductible is not a zero one, and the validator refuses to let a
    NULL cell be reported as a value.
    """
    if value is None:
        return None
    return str(value)


def _summarize_drug_matches(result: DrugMatches) -> str:
    if result.unavailable:
        return "Marketplace unavailable — nothing looked up"
    if not result.matches:
        return f"no Marketplace drug matching {result.query!r}"
    first = result.matches[0]
    return f"{len(result.matches)} match(es), e.g. {first.full_name or first.name}"


def _summarize_coverage(result: CoverageResult) -> str:
    if result.unavailable:
        return "Marketplace unavailable — nothing looked up"
    if not result.coverage:
        return "no coverage rows returned"
    verdicts = ", ".join(sorted({item.coverage for item in result.coverage}))
    return f"{len(result.coverage)} drug/plan pair(s): {verdicts}"


def _summarize_plans(result: PlanMatches) -> str:
    if result.unavailable:
        return "Marketplace unavailable — nothing looked up"
    if result.state_not_served:
        return f"{result.state_not_served} runs its own exchange — not sold on HealthCare.gov"
    if not result.plans:
        return "no plans matched"
    shown = len(result.plans)
    more = f" of {result.total}" if result.total > shown else ""
    where = f" in {result.county.name}" if result.county else ""
    return f"{shown}{more} plan(s){where}"


def _label_rows(result: DrugLabelResult) -> list[Row]:
    """The citable records inside a label result — one per section actually returned.

    One row per *section* rather than one per label, because a section is the unit an answer quotes.
    A single row holding five sections would let a citation name the label and quote whichever part
    it liked, which is the looseness the row shape exists to prevent.
    """
    if result.unavailable or not result.label_found:
        return []
    return [
        Row(
            row_id=section.row_id,
            view="openfda/drug_label",
            source="openfda",
            # **The cell keys are the field names the model saw on `LabelSection`**, not names of
            # our own choosing. This cell used to be called `section` while the model was shown
            # `field`, and the model cited `field` — correctly, by the only name it had been given —
            # and was rejected for naming a column that did not exist. Third bug of this family in
            # one phase; `test_live_row_cells_use_names_the_model_was_shown` is the general guard.
            cells={
                "drug": ", ".join(result.brand_names or result.generic_names) or result.query,
                "field": section.field,
                "text": section.text,
                "effective_time": result.effective_time,
            },
            url=result.source_url,
            title=_label_title(result),
        )
        for section in result.sections
    ]


def _label_title(result: DrugLabelResult) -> str:
    """The citation title a reader sees — source, drug, and how current the label is (§14c)."""
    drug = ", ".join(result.brand_names[:1] or result.generic_names[:1]) or result.query
    when = result.effective_time
    dated = f" · effective {when[:4]}-{when[4:6]}-{when[6:8]}" if when and len(when) == 8 else ""
    return f"openFDA label · {drug}{dated}"


def _recall_rows(result: DrugRecallResult) -> list[Row]:
    """The citable records inside a recall result — **including when there are none.**

    A negative finding is still something a tool established, and this is the case that proved the
    point. *"The FDA holds no recall for this drug"* is true, useful, and exactly what was asked —
    but with nothing in `seen_rows` the grounding validator forces the model to either abstain
    (which would be false: it did answer) or cite something it did not see. So the *search* is the
    citable record: the query that was run and the fact that it matched nothing.

    That is not a loophole in the grounding rule, it is the rule applied honestly. The claim being
    made is "I looked here and found nothing", and this row is precisely the evidence for it.
    """
    if result.unavailable:
        return []
    if not result.recalls:
        return [
            Row(
                row_id=result.row_id,
                view="openfda/drug_enforcement",
                source="openfda",
                cells={
                    "drug": result.query,
                    "recalls_found": "0",
                    "searched": "FDA enforcement (recall) database",
                },
                url=result.source_url,
                title=f"FDA recall search · {result.query} · no matches",
            )
        ]
    return [
        Row(
            row_id=recall.row_id,
            view="openfda/drug_enforcement",
            source="openfda",
            cells={
                "recall_number": recall.recall_number,
                "classification": recall.classification,
                "status": recall.status,
                "recall_initiation_date": recall.recall_initiation_date,
                "reason": recall.reason,
                "product_description": recall.product_description,
                "recalling_firm": recall.recalling_firm,
            },
            url=recall.source_url,
            title=f"FDA recall {recall.recall_number} · {recall.classification or 'unclassified'}",
        )
        for recall in result.recalls
    ]


def _cached_note(summary: str, hit: bool) -> str:
    """Mark a trace step that was answered from this run's cache.

    The step is still recorded rather than hidden. A reader following a trace needs to see that the
    agent asked again — "it looked this up twice" and "it looked this up once and reused it" are
    different behaviours, and only one of them costs the upstream anything.
    """
    return f"{summary} (cached)" if hit else summary


def _summarize_label(result: DrugLabelResult) -> str:
    """The one line the trace panel shows without expanding."""
    if result.unavailable:
        return "FDA label unavailable — nothing looked up"
    if not result.label_found:
        return f"no FDA label found for {result.query!r}"
    if not result.sections:
        return f"label found; no {result.requested_section} section on it"
    fields = ", ".join(s.field for s in result.sections)
    return f"{result.requested_section} from {fields}"


def _summarize_recalls(result: DrugRecallResult) -> str:
    """As above — and it says *no recalls* rather than *0 results*, because that is the finding."""
    if result.unavailable:
        return "FDA recall data unavailable — nothing looked up"
    if not result.recalls:
        return f"no FDA recalls for {result.query!r}"
    shown = len(result.recalls)
    more = f" of {result.total_matching}" if result.total_matching > shown else ""
    return f"{shown}{more} recall(s)"


#: Phase 3's openFDA tools. Registered alongside every earlier lane's, never instead of them.
OPENFDA_TOOLS = (drug_label, drug_recalls)

#: The Marketplace tools. **Registered separately from the rest**, because this is the only source
#: in the phase with a credential: a deployment with no `CMS_MARKETPLACE_API_KEY` still gets the
#: keyless two-thirds of the lane rather than losing all of it (docs/structured-api-tools.md §6).
MARKETPLACE_TOOLS = (find_drug, check_drug_coverage, find_plans)

#: The NPI registry. Keyless like openFDA, so it travels with the `live` flag rather than needing a
#: credential of its own.
NPPES_TOOLS = (lookup_provider,)

#: Every live-API tool, when everything is configured. `select_tools` takes the split into account
#: so adding a tool is a change in this module rather than in three.
LIVE_TOOLS = OPENFDA_TOOLS + NPPES_TOOLS + MARKETPLACE_TOOLS

__all__ = [
    "BUDGET_SPENT",
    "LIVE_TOOLS",
    "MARKETPLACE_TOOLS",
    "NO_CLIENT",
    "NO_MARKETPLACE",
    "NO_NPPES",
    "NPPES_TOOLS",
    "OPENFDA_TOOLS",
    "SECTIONS",
    "check_drug_coverage",
    "drug_label",
    "drug_recalls",
    "find_drug",
    "find_plans",
    "lookup_provider",
]
