"""`OpenFdaClient` — the FDA's drug label and enforcement endpoints, wrapped so that neither an
outage looks like an answer nor an answer looks like an outage.

This module imports nothing from `agent/`, so every test against it runs with no model, no agent,
and no PydanticAI import — the arrangement `web/client.py` established.

**The rule this client exists to enforce is the one that is new in Phase 3.** openFDA answers "your
search matched nothing" with **HTTP 404** and a body of `{"error": {"code": "NOT_FOUND"}}`
(docs/structured-api-tools.md §10b.1). For *"has drug X been recalled"*, that 404 **is the answer:
no recalls.** A client that treated a 404 as a failure would abstain on the single question this
endpoint exists to answer, and would do it while looking perfectly correct. So the 404 is mapped
*before* any generic status handling, and `_http.describe_status` deliberately has no entry for it.

**Everything on a label is a list, and not always a one-element one.** `openfda.brand_name` is
`["Lipitor"]`; `drug_interactions` on the same label is a **two**-element list whose halves have to
be joined to read as a section. `effective_time` and `id` are bare strings. This is an SPL artefact
rather than an accident, and it is the likeliest silent bug in the phase — so nothing here indexes
`[0]`, and `_strings` is the one place a list-or-scalar becomes a `list[str]`.

**The API key is optional, and the asymmetry with `MarketplaceClient` is the point.** openFDA works
with no credential at 1,000 requests/day per *IP*; a free key raises that to 120,000 per key. §4
originally declined the key and §4a records why that reversed — the eval sweep, not the per-question
traffic, is what approaches the ceiling. So this client **degrades to keyless** rather than refusing
to start: there is no `OpenFdaNotConfiguredError`, because a missing key costs headroom rather than
capability. Note the parameter is `api_key`, where the Marketplace's is `apikey`.
"""

import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
from pydantic import ValidationError

from health_coverage_navigator.config import LiveConfig, get_config
from health_coverage_navigator.live._http import (
    build_client,
    describe_status,
    describe_transport_error,
)
from health_coverage_navigator.live.models import (
    SECTION_FIELDS,
    DrugLabelResult,
    DrugRecall,
    DrugRecallResult,
    LabelSection,
)

logger = logging.getLogger(__name__)

OPENFDA_BASE_URL = "https://api.fda.gov"

#: What the model is told when openFDA could not be reached. Phrased as an instruction rather than
#: an error string, because it lands in the model's context and the next thing it does is decide
#: whether to answer.
UNAVAILABLE_TEMPLATE = (
    "The FDA drug database could not be checked ({reason}). You have seen no FDA labelling or "
    "recall data for this question. Say plainly that you could not check it; do not report an "
    "absence of warnings or recalls, because nothing was looked up."
)


def _strings(value: Any) -> list[str]:
    """Coerce an openFDA field to `list[str]`, whatever shape it arrived in.

    **The one place list-vs-scalar is decided.** openFDA sends nearly everything as a list, but not
    everything (`effective_time` is bare), and the lists are not reliably one element long. Every
    caller in this module goes through here rather than indexing, which is what makes the
    `str`-vs-`list[str]` bug §10b.2 warns about impossible to write by accident rather than merely
    discouraged.
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(v) for v in value if isinstance(v, str | int | float) and str(v).strip()]
    return []


def _quoted(term: str) -> str:
    """One openFDA search term, quoted for its Lucene-ish query syntax.

    Embedded quotes are stripped rather than escaped: openFDA's parser has no documented escape,
    and a name containing a quote is far more likely to be a typo than a real drug.
    """
    return '"' + term.replace('"', "").strip() + '"'


@dataclass(slots=True)
class OpenFdaClient:
    """openFDA's drug endpoints, with degradation. Holds no per-run state."""

    _client: httpx.AsyncClient
    _config: LiveConfig
    _api_key: str | None = None

    @classmethod
    def open(
        cls,
        *,
        config: LiveConfig | None = None,
        api_key: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> "OpenFdaClient":
        """Build the client. **Never raises for a missing credential**, even now that one exists.

        This is the structural difference from `WebSearchClient.open` and `MarketplaceClient.open`,
        and it is why openFDA was built first: a missing key here costs *headroom*, not capability,
        so a clone of this repo has a working FDA lane on checkout whether or not anyone has
        requested one. `api_key=None` with nothing in `.env` is a supported, tested configuration.
        """
        resolved = get_config().live if config is None else config
        if api_key is None:
            from health_coverage_navigator.settings import get_secrets

            secret = get_secrets().openfda_api_key
            api_key = None if secret is None else secret.get_secret_value()
        elif not api_key.strip():
            # `""` means **explicitly keyless**, which `None` cannot mean here because `None` is
            # "read the environment". A test asserting the keyless path needs to say so without
            # depending on whether the developer running it happens to hold a key — otherwise
            # `make check-all` quietly means something different on two machines.
            api_key = None
        return cls(
            build_client(
                OPENFDA_BASE_URL,
                timeout_s=resolved.timeout_s,
                retry_after_cap_s=resolved.retry_after_cap_s,
                transport=transport,
            ),
            resolved,
            api_key,
        )

    async def close(self) -> None:
        """Release the underlying connection pool."""
        await self._client.aclose()

    async def _fetch(self, path: str, params: dict[str, str]) -> tuple[dict[str, Any] | None, str]:
        """One openFDA request. Returns `(payload, unavailable_reason)`.

        **`(None, "")` is the no-match case and is not a failure** — the caller decides what an
        empty result means for its question, because the answer differs: for recalls it is "no
        recalls", for a label it is "the FDA holds no label under that name". Only a non-empty
        second element is an outage.
        """
        # openFDA spells it `api_key`; the Marketplace spells it `apikey`. Two adjacent tools in
        # one lane, two spellings — which is why each client owns its own auth detail rather than a
        # shared helper guessing from the hostname (§4).
        query = dict(params) if self._api_key is None else {**params, "api_key": self._api_key}
        try:
            response = await self._client.get(path, params=query)
        except httpx.HTTPError as exc:
            logger.info("openfda %s failed: %s", path, exc)
            return None, UNAVAILABLE_TEMPLATE.format(reason=describe_transport_error(exc))

        if response.status_code == 404:
            # The whole reason this client exists in the shape it does. See the module docstring.
            return None, ""
        if response.status_code != 200:
            logger.info("openfda %s returned %s", path, response.status_code)
            return None, UNAVAILABLE_TEMPLATE.format(reason=describe_status(response.status_code))

        try:
            payload = response.json()
        except ValueError:
            logger.warning("openfda %s returned a body that is not JSON", path)
            return None, UNAVAILABLE_TEMPLATE.format(
                reason="the FDA service returned a response this tool could not read"
            )
        if not isinstance(payload, dict):
            return None, UNAVAILABLE_TEMPLATE.format(
                reason="the FDA service returned a response this tool could not read"
            )
        return payload, ""

    def _url(self, path: str, params: dict[str, str]) -> str:
        """The public, re-fetchable URL for a query — the citation's link (§14b).

        **`params` here is the *unkeyed* dict**, not the query actually sent — `_fetch` adds the key
        separately and never passes it in. That is deliberate: a citation URL is rendered in a
        browser and serialised into eval run files, so the same leak §14b guards against on the
        Marketplace applies here the moment openFDA stops being keyless. Keeping the key out of the
        dict this reads means there is nothing to remember to strip.
        """
        return f"{OPENFDA_BASE_URL}{path}?{urlencode(params)}"

    async def drug_label(self, name: str, section: str, *, sequence: int = 1) -> DrugLabelResult:
        """One drug's label, narrowed to one section. Never raises."""
        fields = SECTION_FIELDS[section]
        params = {"search": f"openfda.brand_name:{_quoted(name)}", "limit": "1"}
        path = "/drug/label.json"
        payload, unavailable = await self._fetch(path, params)

        if unavailable:
            return DrugLabelResult(query=name, requested_section=section, unavailable=unavailable)

        # A brand-name search that matched nothing is retried against the generic name before it is
        # reported as an absence. Users type both, and "the FDA has no label for ibuprofen" would be
        # a confidently wrong answer produced by searching only the brand field.
        if payload is None:
            params = {"search": f"openfda.generic_name:{_quoted(name)}", "limit": "1"}
            payload, unavailable = await self._fetch(path, params)
            if unavailable:
                return DrugLabelResult(
                    query=name, requested_section=section, unavailable=unavailable
                )
            if payload is None:
                return DrugLabelResult(
                    query=name,
                    requested_section=section,
                    label_found=False,
                    source_url=self._url(path, params),
                )

        results = payload.get("results")
        if not isinstance(results, list) or not results or not isinstance(results[0], dict):
            logger.warning("openfda label body had no usable results")
            return DrugLabelResult(
                query=name,
                requested_section=section,
                unavailable=UNAVAILABLE_TEMPLATE.format(
                    reason="the FDA service returned a response this tool could not read"
                ),
            )
        record: dict[str, Any] = results[0]
        openfda: dict[str, Any] = record.get("openfda") or {}
        meta: dict[str, Any] = payload.get("meta") or {}

        sections: list[LabelSection] = []
        cap = self._config.max_section_chars
        for field in fields:
            # Every candidate that is present, not the first — dropping a boxed warning because a
            # general warnings section also exists is not a trade this tool gets to make.
            text = "\n\n".join(_strings(record.get(field)))
            if not text:
                continue
            sections.append(
                LabelSection(
                    row_id=f"fda#l{sequence}.{len(sections) + 1}",
                    field=field,
                    text=text[:cap],
                    truncated=len(text) > cap,
                )
            )

        effective = _strings(record.get("effective_time"))
        try:
            return DrugLabelResult(
                query=name,
                label_found=True,
                requested_section=section,
                sections=sections,
                brand_names=_strings(openfda.get("brand_name")),
                generic_names=_strings(openfda.get("generic_name")),
                rxcuis=_strings(openfda.get("rxcui")),
                effective_time=effective[0] if effective else None,
                source_url=self._url(path, params),
                disclaimer=str(meta.get("disclaimer", "")),
            )
        except ValidationError as exc:  # pragma: no cover - defensive; every field is coerced above
            logger.warning("openfda label could not be modelled: %s", exc)
            return DrugLabelResult(
                query=name,
                requested_section=section,
                unavailable=UNAVAILABLE_TEMPLATE.format(
                    reason="the FDA service returned a response this tool could not read"
                ),
            )

    async def drug_recalls(self, name: str, *, sequence: int = 1) -> DrugRecallResult:
        """Every recall the FDA has published for a drug. Never raises.

        **An empty list is the good outcome and a real answer.** The 404 that produces it is mapped
        in `_fetch`, so it arrives here as `payload is None` with no `unavailable` — see the module
        docstring for why that distinction is the point of this class.
        """
        path = "/drug/enforcement.json"
        params = {
            # **A space, not a literal `+`.** openFDA's query language writes disjunction as
            # `+OR+`, where `+` is the URL encoding of a space — so writing `+` here gets encoded
            # again to `%2B` and the whole term becomes an unparseable string that matches nothing.
            #
            # That produced the worst bug of this phase: a **false "no recalls"** on a drug with 44
            # of them, on the one endpoint whose entire purpose is that an empty result is a
            # trustworthy answer. Caught only by comparing against a live call — the fixture tests
            # could not see it, because a mock transport returns its canned body no matter what
            # nonsense it is asked. See `test_the_recall_query_is_a_valid_disjunction`.
            "search": (
                f"openfda.brand_name:{_quoted(name)} OR openfda.generic_name:{_quoted(name)}"
            ),
            "limit": str(self._config.max_recalls),
            "sort": "recall_initiation_date:desc",
        }
        payload, unavailable = await self._fetch(path, params)

        if unavailable:
            return DrugRecallResult(query=name, unavailable=unavailable)
        if payload is None:
            # No match. The FDA holds no recall for this drug — which is what was asked, and which
            # is citable: `row_id` is set so the model can point at the search that established
            # it rather than being forced to abstain on a question it actually answered.
            return DrugRecallResult(
                query=name,
                row_id=f"fda#r{sequence}.0",
                source_url=self._url(path, params),
            )

        results = payload.get("results")
        if not isinstance(results, list):
            return DrugRecallResult(
                query=name,
                unavailable=UNAVAILABLE_TEMPLATE.format(
                    reason="the FDA service returned a response this tool could not read"
                ),
            )
        meta: dict[str, Any] = payload.get("meta") or {}
        total = meta.get("results", {}).get("total") if isinstance(meta.get("results"), dict) else 0
        url = self._url(path, params)

        recalls: list[DrugRecall] = []
        for index, raw in enumerate(results, start=1):
            if not isinstance(raw, dict):
                continue
            # Enforcement records are flat scalars, unlike labels — a real asymmetry inside one API,
            # verified 2026-08-22. `str(... or "")` rather than `_strings` for exactly that reason.
            recalls.append(
                DrugRecall(
                    row_id=f"fda#r{sequence}.{index}",
                    recall_number=str(raw.get("recall_number") or ""),
                    classification=raw.get("classification"),
                    status=raw.get("status"),
                    reason=str(raw.get("reason_for_recall") or ""),
                    product_description=str(raw.get("product_description") or ""),
                    recalling_firm=raw.get("recalling_firm"),
                    distribution_pattern=raw.get("distribution_pattern"),
                    recall_initiation_date=raw.get("recall_initiation_date"),
                    source_url=url,
                )
            )

        return DrugRecallResult(
            query=name,
            # Blank when there are recalls: each carries its own citable id, and a result-level id
            # that is not in `seen_rows` is an invitation to cite something that will be rejected.
            row_id="" if recalls else f"fda#r{sequence}.0",
            source_url=url,
            recalls=recalls,
            total_matching=int(total) if isinstance(total, int) else len(recalls),
            disclaimer=str(meta.get("disclaimer", "")),
        )


__all__ = ["OPENFDA_BASE_URL", "UNAVAILABLE_TEMPLATE", "OpenFdaClient"]
