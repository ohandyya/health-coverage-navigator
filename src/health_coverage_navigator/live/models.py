"""What the live-API tools return — the shapes the *language model* reads.

These sit here rather than in `agent/models.py` for the same structural reason `web/models.py` and
`structured/models.py` do: the clients build them and `live/` must not import from `agent/`
(docs/structured-api-tools.md §12). Every field name and docstring below is text a model reads, so
it is written for that reader.

Three things carried deliberately rather than smoothed away:

**`unavailable` is a first-class field, not an exception** — the pattern `WebSearchResults`
established. But this lane needs the *inverse* rule as well, and it is new here: **an empty result
is an answer, not an outage.** openFDA answers "nothing matched" with an HTTP 404 and NPPES with a
`result_count` of zero (§10a.1, §10b.1); for *"has drug X been recalled"* the honest reading of that
is **no recalls**, which is exactly what a reader asked for. A client that mapped either to
`unavailable` would abstain on the question the tool exists to answer.

**Sections are a list, not a string.** An SPL label spells its warnings differently depending on
whether the drug is prescription or over-the-counter — `warnings_and_cautions` and `boxed_warning`
on one, plain `warnings` on the other — so a request for "warnings" resolves to whichever of those
the label actually carries, and says which. See `LabelSection`.

**openFDA's own disclaimer travels with the result** (§14c). A source that disclaims its own
reliability in every response should not have that dropped at the boundary.
"""

from typing import Literal

from pydantic import BaseModel, Field

#: The label sections this repo exposes, named for what a person asks rather than for the SPL field
#: that happens to hold them. A `Literal` rather than a plain `str` because `agent/live_tools.py`
#: derives its runtime check from this via `get_args`, so the check and the type cannot drift — the
#: same arrangement `WebTopic` has.
#:
#: The set is deliberately small. A label carries 38 top-level fields and most of them are
#: multi-paragraph prose; offering all of them would be offering 38 ways to spend a context window.
LabelSectionName = Literal["indications", "warnings", "interactions", "adverse_reactions", "dosage"]

#: Which SPL fields back each section, in the order they should be read.
#:
#: **This mapping is one-to-many because SPL is**, and that is the single most consequential detail
#: in this module. A prescription label carries `warnings_and_cautions` and sometimes
#: `boxed_warning`; an over-the-counter label carries neither and uses plain `warnings`. Verified
#: 2026-08-22 against a prescription label (Lipitor) and two OTC ones. A wrapper that read one field
#: name per section would return *nothing* for whichever half of the drug catalogue it guessed
#: against, and — worse — would return it as a confident absence.
#:
#: `warnings` gathers **every** candidate present rather than the first one found. Dropping a boxed
#: warning because a general warnings section also existed is not a trade this tool gets to make.
SECTION_FIELDS: dict[str, tuple[str, ...]] = {
    "indications": ("indications_and_usage",),
    "warnings": ("boxed_warning", "warnings_and_cautions", "warnings"),
    "interactions": ("drug_interactions",),
    "adverse_reactions": ("adverse_reactions",),
    "dosage": ("dosage_and_administration",),
}


class LabelSection(BaseModel):
    """One section of a drug label, as printed on it."""

    row_id: str = ""
    """**Cite this exact string.** One id per *section*, because a section is the unit an answer
    quotes — a single id for the whole label would let a citation name the label and quote whichever
    part it liked.

    It lives here rather than on the result for a reason found the hard way: the result used to
    carry the id and the rows were numbered beneath it, so the model was shown `fda#l1.1` while the
    citable rows were `fda#l1.1.1` — an id it could see and could not cite. **Every id the model can
    see must be citable, and every citable row must have its id visible.**"""

    field: str
    """The SPL field this text came from — `boxed_warning`, `warnings_and_cautions`, `warnings`, and
    so on. **Worth repeating to a reader when it is `boxed_warning`**: that is the FDA's most
    serious warning class, and saying so is part of reporting it accurately."""

    text: str
    """The section text, verbatim. **Quote from this and nowhere else.**"""

    truncated: bool = False
    """Whether this text was cut at a length ceiling. Normally `False` — real sections measured
    500-11,300 characters and the ceiling is far above that.

    **When it is `True`, say so.** The end of a warnings section is not decoration, and a reader who
    is told "here is what the label warns" deserves to know they were shown part of it. Silent
    truncation is the failure this field exists to prevent."""


class DrugLabelResult(BaseModel):
    """What `drug_label` reports: what the label says, or why it could not be read.

    Three distinct states, and they must not be conflated:

    - `unavailable` set — openFDA could not be reached. **Nothing was looked up.**
    - `label_found=False` — openFDA was reached and holds no label matching this name.
    - `label_found=True` with empty `sections` — a label exists, but it does not carry the section
      that was asked for. Common and unremarkable: an over-the-counter label has no
      `drug_interactions` section at all.
    """

    query: str
    """The drug name that was actually searched for."""

    row_id: str = ""
    """Cite this **only when `sections` is empty** — it identifies the label search itself, which is
    the evidence for both negative findings this result can carry: *the FDA holds no label under
    that name*, and *the label exists but does not carry that section*. Either is a true answer, and
    without an id to point at the grounding validator would force an abstention on a question that
    was answered.

    Blank whenever `sections` is populated, because then each section carries its own citable id and
    an unusable id in the payload is an invitation to cite something the validator will reject."""

    unavailable: str | None = None
    """Set when openFDA could not be reached — an outage, a timeout, a rate limit, or this run's
    lookup budget being spent.

    When this is set: **say plainly that you could not check the FDA's labelling data.** Do not fill
    the gap from your own knowledge, and do not report "no warnings found" — nothing was found
    because nothing was asked."""

    label_found: bool = False
    """Whether openFDA holds a label for this drug at all. `False` with `unavailable` unset is a
    real answer — the FDA's labelling database has no match for that name. Consider that the name
    may be misspelled, or a brand name where a generic would match."""

    requested_section: str = ""
    """The section that was asked for."""

    sections: list[LabelSection] = Field(default_factory=list)
    """The label text for that section. **Empty with `label_found=True` means the label genuinely
    does not carry this section** — say that, rather than implying the drug has no warnings."""

    brand_names: list[str] = Field(default_factory=list)
    generic_names: list[str] = Field(default_factory=list)
    """What the label calls this drug. Check these before trusting the match: a search for one brand
    can land on a label that lists several, and the generic name is what tells you whether you have
    the drug the question meant."""

    rxcuis: list[str] = Field(default_factory=list)
    """Every RxCUI on this label — **the identifiers `check_drug_coverage` takes.** A label covers
    all strengths and forms of the drug, so this is usually several. This is the join between what
    the FDA says about a drug and what a plan covers."""

    effective_time: str | None = None
    """When this version of the label took effect, `YYYYMMDD`. A label is a point-in-time document;
    say how current it is when the question turns on that."""

    source_url: str = ""
    """The openFDA query that produced this, re-fetchable by anyone."""

    disclaimer: str = ""
    """openFDA's own words about its reliability. **Carry the substance of this into any answer that
    relies on this result** — the FDA asks that its labelling data not be used to make medical
    decisions, and a health tool repeating a label owes a reader that caveat."""


class DrugRecall(BaseModel):
    """One recall the FDA has published."""

    row_id: str
    """**Cite this exact string, as a `row_id`.** A live record is a row: same citation shape as a
    query against the vendored plan tables, because it makes the same kind of claim.

    Named `row_id` rather than `record_id` for exactly that reason. The first version called it
    `record_id`, and the model dutifully cited it in the `result_id` field — the *web* shape, whose
    validator then rejected it as a URL nobody retrieved. The design said "a live record is a row"
    while the field name said otherwise, and the name won."""

    recall_number: str
    classification: str | None = None
    """`Class I` (reasonable probability of serious harm), `Class II` (temporary or reversible
    harm), or `Class III` (unlikely to cause harm). **Say which** — the classes are not
    interchangeable and a reader hearing "recalled" assumes the worst one."""

    status: str | None = None
    """`Ongoing`, `Completed`, `Terminated`, or `Pending`. **A terminated recall from years ago is
    not a current safety problem**, and reporting it as though it were would alarm a reader over
    something already resolved. Always pair this with `recall_initiation_date`."""

    reason: str = ""
    """Why the product was recalled, in the FDA's words."""

    product_description: str = ""
    """Which product, down to strength and lot. Recalls are **batch-specific**: a recall of certain
    lots is not a recall of the drug."""

    recalling_firm: str | None = None
    distribution_pattern: str | None = None
    recall_initiation_date: str | None = None
    """`YYYYMMDD`, when the recall began."""

    source_url: str = ""


class DrugRecallResult(BaseModel):
    """What `drug_recalls` reports.

    **An empty `recalls` with `unavailable` unset is a real, useful answer: the FDA's enforcement
    database holds no recall matching this drug.** Say exactly that. Do not hedge it into "I could
    not find any", which a reader hears as a failed search.
    """

    query: str
    unavailable: str | None = None
    """Set only when openFDA could not be reached. **Never set merely because nothing matched.**"""

    recalls: list[DrugRecall] = Field(default_factory=list)

    row_id: str = ""
    """Cite this **only when `recalls` is empty** — it identifies the search itself, which is the
    evidence for "the FDA holds no recall for this drug". When `recalls` is non-empty this is blank,
    because each recall carries its own citable id and an unusable id in the payload is an
    invitation to cite something the validator will reject."""

    source_url: str = ""

    total_matching: int = 0
    """How many recalls the FDA holds for this drug in total. When this exceeds the number in
    `recalls`, you are seeing the most recent ones — say so rather than implying it is all of
    them."""

    disclaimer: str = ""

    lookups_remaining: int = 0
    """How many more live-API lookups this run may make. At zero, trying again will not help."""


__all__ = [
    "SECTION_FIELDS",
    "DrugLabelResult",
    "DrugRecall",
    "DrugRecallResult",
    "LabelSection",
    "LabelSectionName",
]


# --------------------------------------------------------------- Marketplace ------------------


class DrugMatch(BaseModel):
    """One drug the Marketplace API recognises, and the RxCUI that identifies it."""

    row_id: str = ""
    """Cite this exact string when you say **which** strength or form you checked — that is a claim
    about what this lookup returned, and it needs a row behind it like any other."""

    rxcui: str
    """**The identifier `check_drug_coverage` takes.** Coverage is asked per RxCUI, and an RxCUI
    names one strength and form — not the drug in general."""

    name: str
    strength: str = ""
    route: str = ""
    full_name: str = ""
    """The unambiguous name, e.g. `atorvastatin 20 MG Oral Tablet [Lipitor]`. Use this when telling
    a reader which one you checked — `name` alone does not distinguish the strengths."""


class DrugMatches(BaseModel):
    """What `find_drug` reports.

    **An empty `matches` with `unavailable` unset means the Marketplace does not recognise that
    name** — which is a real answer, and usually means a spelling to check or a brand where a
    generic is listed.
    """

    query: str
    unavailable: str | None = None

    row_id: str = ""
    """Cite this **only when `matches` is empty** — it identifies the search, which is the evidence
    for "the Marketplace does not recognise that name". Blank otherwise: a resolved drug is cited
    through the coverage answer it leads to, not through this lookup."""

    matches: list[DrugMatch] = Field(default_factory=list)
    """Usually several: one per strength and form. **Which one the reader meant is a question, not
    a detail to pick silently** — if the answer turns on strength and they did not say, ask or say
    which you checked."""


class DrugCoverage(BaseModel):
    """Whether one plan covers one drug."""

    row_id: str
    """Cite this exact string."""

    rxcui: str
    plan_id: str
    coverage: str
    """`Covered`, `NotCovered`, `GenericCovered`, or `DataNotProvided`.

    **`GenericCovered` is not `NotCovered`.** It means the plan does not cover the drug as
    prescribed but does cover its generic equivalent, named in `generic_rxcui` — a materially
    different answer for someone deciding what to do next, and reporting it as "not covered" would
    be wrong.

    **`DataNotProvided` is not `NotCovered` either.** The plan filed no formulary information for
    that drug; say the data is not available rather than that the drug is not covered."""

    generic_rxcui: str | None = None
    """Set **only** when `coverage` is `GenericCovered`, naming the covered generic."""

    source_url: str = ""


class CoverageResult(BaseModel):
    """What `check_drug_coverage` reports."""

    unavailable: str | None = None
    year: int | None = None
    """The plan year actually checked. Formularies change year to year, so an answer is only true
    for the year it was asked about."""

    coverage: list[DrugCoverage] = Field(default_factory=list)

    row_id: str = ""
    """Cite this **only when `coverage` is empty** — the plans were asked about these drugs and the
    Marketplace returned nothing at all, which is different from a `DataNotProvided` verdict (that
    one is itself a row). Blank otherwise, because then each pair carries its own citable id."""

    lookups_remaining: int = 0


class County(BaseModel):
    """One county a ZIP code falls in."""

    fips: str
    name: str
    state: str


class PlanSummary(BaseModel):
    """One plan available to this household, as the agent sees it."""

    row_id: str
    """Cite this exact string."""

    plan_id: str
    """The 14-character HIOS Standard Component ID — **the same key the vendored plan tables use**,
    so a plan found here can be looked up there and vice versa."""

    name: str
    issuer: str
    metal_level: str = ""
    plan_type: str = ""
    """`HMO`, `PPO`, `EPO`, `POS`. Decides whether out-of-network care is covered at all."""

    premium: float | None = None
    """Full monthly premium, before any subsidy."""

    premium_with_credit: float | None = None
    """Monthly premium after the advance premium tax credit this household qualifies for. **Quote
    both** — the difference is the subsidy, and a reader comparing plans needs to know which figure
    they are looking at."""

    deductible: float | None = None
    out_of_pocket_max: float | None = None
    quality_rating: int | None = None
    """CMS star rating out of 5, or `None` when the plan is too new to be rated. `None` is not zero
    and must not be reported as a low score."""

    source_url: str = ""


class PlanMatches(BaseModel):
    """What `find_plans` reports.

    Four distinct states, and conflating any two of them misleads a reader:

    - `unavailable` set — the Marketplace could not be reached. Nothing was looked up.
    - `state_not_served` set — **HealthCare.gov does not sell plans in that state.** Not an error
      and not an absence of plans: that state runs its own exchange, and the useful answer says so.
    - empty `plans` with neither set — the search ran and found nothing.
    - `plans` populated, with `total` possibly larger than what is shown.
    """

    unavailable: str | None = None

    row_id: str = ""
    """Cite this when **either** negative finding is what you are reporting: `state_not_served` is
    set, or the search ran and `plans` came back empty. The lookup itself is the evidence — that
    HealthCare.gov does not sell in that state, or that nothing matched this household. Blank when
    `plans` is populated, because then each plan carries its own citable id.

    Its absence was the fourth instance in this phase of a citable row whose id the model could not
    see: the row was recorded, the agent had the right answer, and it abstained because it had
    nothing it was allowed to point at."""

    state_not_served: str | None = None
    """Set when the state runs its own exchange rather than using HealthCare.gov. **Say this
    plainly and name the state** — the reader is not out of options, they are in the wrong place,
    and telling them their state runs its own marketplace is the answer they need."""

    zipcode: str = ""
    """The ZIP that was searched. Carried so an answer — and a citation — can name it."""

    year: int | None = None
    plans: list[PlanSummary] = Field(default_factory=list)
    total: int = 0
    """How many plans matched in total. When it exceeds `len(plans)` you are seeing a sample —
    say so rather than implying it is the whole market."""

    other_counties: list[County] = Field(default_factory=list)
    """Other counties this ZIP also covers, which were **not** priced.

    Non-empty means the ZIP straddles a county line and you are seeing one county's prices. **Say
    which county the figures are for and that the others differ** — premiums are set per county, so
    an unqualified answer is right about a place the reader may not live."""

    county: County | None = None
    """Which county was actually priced. Premiums are set per county, so this is part of the
    answer whenever a ZIP spans more than one."""

    lookups_remaining: int = 0


# ----------------------------------------------------------------- NPPES ----------------------


class Taxonomy(BaseModel):
    """One specialty on a provider's registry record."""

    code: str
    description: str
    """The specialty in words, e.g. `Family Nurse Practitioner`."""

    primary: bool
    """**Whether this is the provider's primary specialty.** A provider may hold several and exactly
    one is primary — say which when you report a specialty, because "this doctor is a cardiologist"
    and "cardiology is one of several things this doctor is licensed for" are different claims."""

    state: str | None = None
    license: str | None = None


class ProviderRecord(BaseModel):
    """One provider as the NPI registry holds them."""

    row_id: str
    """Cite this exact string."""

    npi: str
    name: str
    enumeration_type: str = ""
    """`NPI-1` for an individual, `NPI-2` for an organization. A hospital and a doctor are both in
    this registry and they are not interchangeable."""

    status: str | None = None
    """`A` for active. Anything else means the number is no longer in good standing — say so."""

    taxonomies: list[Taxonomy] = Field(default_factory=list)
    """Specialties, **primary first**. Empty is possible and means the registry lists none."""

    city: str | None = None
    state: str | None = None
    """The practice location, **not a mailing address**. Useful for confirming you have the right
    person; not a substitute for a plan's provider directory."""

    last_updated: str | None = None


class ProviderResult(BaseModel):
    """What `lookup_provider` reports.

    Four states, and they are all different:

    - `unavailable` set — the registry could not be reached. Nothing was looked up.
    - `invalid` set — the number is not a well-formed NPI. A fact about the *input*.
    - `found=False` — the registry was reached and holds no such NPI. An answer.
    - `found=True` — `provider` is populated.
    """

    npi: str
    unavailable: str | None = None

    invalid: str | None = None
    """Set when the registry rejected the number as malformed. Tell the reader their NPI looks
    wrong — an NPI is ten digits with a check digit, so a typo is detectable and worth naming."""

    found: bool = False
    provider: ProviderRecord | None = None

    row_id: str = ""
    """Cite this when the registry holds **no** such NPI — the lookup itself is the evidence for
    that finding. Blank when a provider was found, because then `provider.row_id` is the citable
    one."""

    source_url: str = ""
    lookups_remaining: int = 0
