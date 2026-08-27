"""`MarketplaceClient` — CMS's Marketplace API, the one live source in this phase with a credential.

Imports nothing from `agent/`, like every other module in this package.

**Three things here that `OpenFdaClient` does not have to deal with:**

**1. A key that expires every 60 days** (docs/structured-api-tools.md §3). CMS emails a replacement
automatically, and `Secrets` is frozen behind an `lru_cache`, so a rotation needs `.env` edited
*and*
the process restarted. That makes a 401 the *likely* failure in month three, and it is the one an
operator can fix in a minute — but only if the message says "the key" rather than "the API". Hence
`_CREDENTIAL_REASON` rather than folding 401 into the generic status text.

**2. The key rides in the query string**, which means it is in every URL this client builds — and
those URLs become citation links rendered in a browser and serialised into eval run files. `_url`
**strips it**, and `tests/test_live_clients.py` asserts no citation URL carries it. This is the one
place in the phase where a plumbing mistake leaks a credential into a tracked artefact.

**3. A 400 can be an answer.** CMS's API serves only the states that use HealthCare.gov; a state
running its own exchange comes back `400 "state is not a valid marketplace state"` — verified
2026-08-22 for CA, GA and NY, against NC and TX which work. That is not an outage and not "no plans
available": the reader is in the wrong place, and telling them their state runs its own marketplace
is the answer they need. §10d records the finding.

**The response envelopes here are the ones the published spec gets wrong** (§7a), so every shape
below is transcribed from a live call rather than from the OpenAPI document: `/drugs/autocomplete`
returns a **bare array** where the spec declares an object, and `/drugs/covered` returns `coverage`
where the spec declares a property named after a Swagger tag.
"""

import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from health_coverage_navigator.config import LiveConfig, get_config
from health_coverage_navigator.live._http import (
    build_client,
    describe_status,
    describe_transport_error,
)
from health_coverage_navigator.live.models import (
    County,
    CoverageResult,
    DrugCoverage,
    DrugMatch,
    DrugMatches,
    PlanMatches,
    PlanSummary,
)

logger = logging.getLogger(__name__)

MARKETPLACE_BASE_URL = "https://marketplace.api.healthcare.gov/api/v1"

#: The query parameter CMS takes the key in. Named rather than inlined because `_url` has to strip
#: exactly this one before a URL is ever stored or shown.
APIKEY_PARAM = "apikey"

UNAVAILABLE_TEMPLATE = (
    "The Marketplace plan data could not be checked ({reason}). You have seen no plan, "
    "drug-coverage or pricing data for this question. Say plainly that you could not check it; do "
    "not report that "
    "a plan does not cover something, because nothing was looked up."
)

#: Distinct from the generic status text on purpose. §3: the 60-day expiry makes this the likely
#: failure eventually, and an operator reading "the API is unavailable" will go looking for an
#: outage that is not there.
_CREDENTIAL_REASON = (
    "this server's Marketplace API key was rejected — it may have expired, as CMS keys do every 60 "
    "days"
)

#: CMS's wording when a state runs its own exchange rather than using HealthCare.gov.
_NOT_A_MARKETPLACE_STATE = "not a valid marketplace state"


class MarketplaceNotConfiguredError(RuntimeError):
    """No `CMS_MARKETPLACE_API_KEY`. Raised at startup, never mid-run.

    The Marketplace analogue of `WebSearchNotConfiguredError`. Unlike the web lane this is **not**
    fatal to the phase: openFDA and NPPES are keyless, so `app.py` degrades this to "the Marketplace
    tools are not registered" and the agent is told what it therefore cannot answer, rather than the
    whole live lane refusing to start.
    """


@dataclass(slots=True)
class MarketplaceClient:
    """CMS's Marketplace API, with degradation. Holds no per-run state."""

    _client: httpx.AsyncClient
    _config: LiveConfig
    _api_key: str

    @classmethod
    def open(
        cls,
        *,
        config: LiveConfig | None = None,
        api_key: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> "MarketplaceClient":
        """Build the client, refusing to pretend the lane exists without a key."""
        resolved = get_config().live if config is None else config
        if api_key is None:
            from health_coverage_navigator.settings import get_secrets

            secret = get_secrets().cms_marketplace_api_key
            if secret is None:
                raise MarketplaceNotConfiguredError(
                    "CMS_MARKETPLACE_API_KEY is not set. The Marketplace tools need it — add it to "
                    "`.env` (see `.env.example`, which says where to get a key), or set "
                    "`agent.live_tools: false` to run without the live lane entirely."
                )
            api_key = secret.get_secret_value()
        return cls(
            build_client(
                MARKETPLACE_BASE_URL,
                timeout_s=resolved.timeout_s,
                retry_after_cap_s=resolved.retry_after_cap_s,
                transport=transport,
            ),
            resolved,
            api_key,
        )

    async def close(self) -> None:
        await self._client.aclose()

    def _url(self, path: str, params: dict[str, Any]) -> str:
        """The public URL for a query, **with the API key removed**.

        Load-bearing rather than tidy. A citation URL is rendered in a browser and written into eval
        run files; the key travels in the query string; so without this line every Marketplace
        citation would publish a credential. See the module docstring.
        """
        public = {k: v for k, v in params.items() if k != APIKEY_PARAM}
        query = f"?{urlencode(public)}" if public else ""
        return f"{MARKETPLACE_BASE_URL}{path}{query}"

    async def _request(
        self, method: str, path: str, *, params: dict[str, Any] | None = None, json: Any = None
    ) -> tuple[Any, str, str]:
        """One request. Returns `(payload, unavailable_reason, state_not_served)`.

        Three outcomes rather than two, because this API has three: it worked, it could not be
        reached, or it answered that CMS does not serve this state — which is a fact about the
        market and not a failure of anything.
        """
        query = {**(params or {}), APIKEY_PARAM: self._api_key}
        try:
            response = await self._client.request(method, path, params=query, json=json)
        except httpx.HTTPError as exc:
            logger.info("marketplace %s failed: %s", path, exc)
            return None, UNAVAILABLE_TEMPLATE.format(reason=describe_transport_error(exc)), ""

        if response.status_code == 401 or response.status_code == 403:
            logger.warning("marketplace rejected the API key (%s)", response.status_code)
            return None, UNAVAILABLE_TEMPLATE.format(reason=_CREDENTIAL_REASON), ""

        if response.status_code == 400:
            message = _message_of(response)
            if _NOT_A_MARKETPLACE_STATE in message.lower():
                return None, "", message
            logger.info("marketplace rejected the request: %s", message)
            return None, UNAVAILABLE_TEMPLATE.format(reason="the request was rejected"), ""

        if response.status_code != 200:
            return (
                None,
                UNAVAILABLE_TEMPLATE.format(reason=describe_status(response.status_code)),
                "",
            )

        try:
            return response.json(), "", ""
        except ValueError:
            logger.warning("marketplace %s returned a body that is not JSON", path)
            return (
                None,
                UNAVAILABLE_TEMPLATE.format(
                    reason="the Marketplace service returned a response this tool could not read"
                ),
                "",
            )

    async def market_year(self) -> int | None:
        """The plan year CMS considers current, or `None` when it could not be asked.

        Asked rather than hardcoded (§7c). Resolved once per run and cached on `AnswerDeps`, so this
        does not cost a call per tool.
        """
        payload, unavailable, _ = await self._request("GET", "/market-years")
        if unavailable or not isinstance(payload, dict):
            return None
        current = payload.get("current")
        return current if isinstance(current, int) else None

    async def find_drug(self, name: str, *, sequence: int = 1) -> DrugMatches:
        """Resolve a drug name to RxCUIs. Never raises.

        **The response is a bare JSON array**, not the object the published spec declares (§7a).
        """
        path = "/drugs/autocomplete"
        payload, unavailable, _ = await self._request("GET", path, params={"q": name})
        if unavailable:
            return DrugMatches(query=name, unavailable=unavailable)
        if not isinstance(payload, list):
            return DrugMatches(
                query=name,
                unavailable=UNAVAILABLE_TEMPLATE.format(
                    reason="the Marketplace service returned a response this tool could not read"
                ),
            )
        matches = [
            DrugMatch(
                row_id=f"mkt#d{sequence}.{index}",
                rxcui=str(item.get("rxcui", "")),
                name=str(item.get("name", "")),
                strength=str(item.get("strength", "") or ""),
                route=str(item.get("route", "") or ""),
                full_name=str(item.get("full_name", "") or ""),
            )
            for index, item in enumerate(payload, start=1)
            if isinstance(item, dict) and item.get("rxcui")
        ]
        # An unrecognised name is a real answer — usually a misspelling worth naming — so the
        # search itself is citable when nothing matched.
        return DrugMatches(
            query=name, matches=matches, row_id="" if matches else f"mkt#d{sequence}.0"
        )

    async def check_drug_coverage(
        self, rxcuis: list[str], plan_ids: list[str], year: int, *, sequence: int = 1
    ) -> CoverageResult:
        """Whether each plan covers each drug. Never raises.

        **The envelope is `coverage`**, not the tag-named property the spec declares (§7a).
        """
        path = "/drugs/covered"
        params = {
            "year": str(year),
            "drugs": ",".join(rxcuis),
            "planids": ",".join(plan_ids),
        }
        payload, unavailable, _ = await self._request("GET", path, params=params)
        if unavailable:
            return CoverageResult(unavailable=unavailable, year=year)
        if not isinstance(payload, dict) or not isinstance(payload.get("coverage"), list):
            return CoverageResult(
                year=year,
                unavailable=UNAVAILABLE_TEMPLATE.format(
                    reason="the Marketplace service returned a response this tool could not read"
                ),
            )
        url = self._url(path, params)
        rows = [
            DrugCoverage(
                row_id=f"mkt#c{sequence}.{index}",
                rxcui=str(item.get("rxcui", "")),
                plan_id=str(item.get("plan_id", "")),
                coverage=str(item.get("coverage", "")),
                # Present **only** for `GenericCovered` (§7b). Modelled as conditional rather than
                # merely optional because its absence and its presence mean different answers.
                generic_rxcui=item.get("generic_rxcui"),
                source_url=url,
            )
            for index, item in enumerate(payload["coverage"], start=1)
            if isinstance(item, dict)
        ]
        # An empty envelope is not the same as a `DataNotProvided` verdict: that one arrives as a
        # row and is citable already. This is the residue — the Marketplace returned nothing at all
        # for these drugs and plans — and the lookup is the evidence for saying so.
        return CoverageResult(coverage=rows, year=year, row_id="" if rows else f"mkt#c{sequence}.0")

    async def counties_for_zip(self, zipcode: str) -> tuple[list[County], str]:
        """Every county a ZIP falls in, and an unavailability reason if it could not be asked.

        **A ZIP can span several counties** — 30341 covers DeKalb and Fulton, verified 2026-08-22 —
        and premiums are set per county, so which one is not a detail a wrapper may pick.
        """
        path = f"/counties/by/zip/{zipcode}"
        payload, unavailable, _ = await self._request("GET", path)
        if unavailable or not isinstance(payload, dict):
            return [], unavailable or UNAVAILABLE_TEMPLATE.format(
                reason="the Marketplace service returned a response this tool could not read"
            )
        raw = payload.get("counties")
        if not isinstance(raw, list):
            return [], ""
        return [
            County(
                fips=str(item.get("fips", "")),
                name=str(item.get("name", "")),
                state=str(item.get("state", "")),
            )
            for item in raw
            if isinstance(item, dict) and item.get("fips")
        ], ""

    async def find_plans(
        self,
        *,
        county: County,
        zipcode: str,
        ages: list[int],
        income: float,
        year: int,
        limit: int,
        sequence: int = 1,
    ) -> PlanMatches:
        """Plans available to this household. Never raises."""
        path = "/plans/search"
        body = {
            "household": {
                "income": income,
                "people": [{"age": age, "aptc_eligible": True} for age in ages],
            },
            "market": "Individual",
            "place": {
                "countyfips": county.fips,
                "state": county.state,
                "zipcode": zipcode,
            },
            "year": year,
        }
        payload, unavailable, not_served = await self._request("POST", path, json=body)
        if unavailable:
            return PlanMatches(unavailable=unavailable, year=year, zipcode=zipcode, county=county)
        if not_served:
            return PlanMatches(
                state_not_served=county.state,
                row_id=f"mkt#s{sequence}.{county.state}",
                zipcode=zipcode,
                year=year,
                county=county,
            )
        if not isinstance(payload, dict) or not isinstance(payload.get("plans"), list):
            return PlanMatches(
                year=year,
                county=county,
                unavailable=UNAVAILABLE_TEMPLATE.format(
                    reason="the Marketplace service returned a response this tool could not read"
                ),
            )

        url = self._url(path, {"year": str(year), "zipcode": zipcode, "countyfips": county.fips})
        plans = [
            _plan_summary(raw, f"mkt#p{sequence}.{index}", url)
            for index, raw in enumerate(payload["plans"][:limit], start=1)
            if isinstance(raw, dict)
        ]
        total = payload.get("total")
        return PlanMatches(
            year=year,
            zipcode=zipcode,
            county=county,
            plans=plans,
            total=total if isinstance(total, int) else len(plans),
            # The fourth of the four states this model documents: the search ran and matched
            # nothing. Its `state_not_served` sibling has been citable since §14a-bis; this one was
            # the same hole one branch over.
            row_id="" if plans else f"mkt#p{sequence}.0",
        )


def _message_of(response: httpx.Response) -> str:
    """CMS's error text. The live body uses `message`/`error`; the spec declares `msg`/`err` — one
    more place the published document describes a response the server does not send (§7a)."""
    try:
        body = response.json()
    except ValueError:
        return ""
    if not isinstance(body, dict):
        return ""
    for key in ("message", "error", "msg", "err"):
        value = body.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _amount(entries: Any, keyword: str) -> float | None:
    """The headline figure from a plan's `deductibles` or `moops` list.

    A plan carries several of each — per network tier, per CSR variant, individual versus family —
    and picking the wrong one reports a number that is real but answers a different question. This
    takes the in-network individual entry and returns `None` rather than guessing when there is
    none, because a missing deductible must not read as a zero one.
    """
    if not isinstance(entries, list):
        return None
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if entry.get("network_tier") != "In-Network" or not entry.get("individual"):
            continue
        if keyword.lower() not in str(entry.get("type", "")).lower():
            continue
        amount = entry.get("amount")
        if isinstance(amount, int | float):
            return float(amount)
    return None


def _plan_summary(raw: dict[str, Any], row_id: str, url: str) -> PlanSummary:
    # Narrowed via locals rather than a ternary over two `.get()` calls: pyright cannot see that
    # the second call returns what the first one was checked for, and it is right not to — a dict
    # is mutable and the two calls are not guaranteed to agree.
    raw_issuer = raw.get("issuer")
    issuer: dict[str, Any] = raw_issuer if isinstance(raw_issuer, dict) else {}
    raw_rating = raw.get("quality_rating")
    rating: dict[str, Any] = raw_rating if isinstance(raw_rating, dict) else {}
    global_rating = rating.get("global_rating") if rating.get("available") else None
    return PlanSummary(
        row_id=row_id,
        plan_id=str(raw.get("id", "")),
        name=str(raw.get("name", "")),
        issuer=str(issuer.get("name", "")),
        metal_level=str(raw.get("metal_level", "") or ""),
        plan_type=str(raw.get("type", "") or ""),
        premium=raw.get("premium") if isinstance(raw.get("premium"), int | float) else None,
        premium_with_credit=(
            raw.get("premium_w_credit")
            if isinstance(raw.get("premium_w_credit"), int | float)
            else None
        ),
        deductible=_amount(raw.get("deductibles"), "deductible"),
        out_of_pocket_max=_amount(raw.get("moops"), "maximum out of pocket"),
        quality_rating=global_rating if isinstance(global_rating, int) else None,
        source_url=url,
    )


__all__ = [
    "APIKEY_PARAM",
    "MARKETPLACE_BASE_URL",
    "UNAVAILABLE_TEMPLATE",
    "MarketplaceClient",
    "MarketplaceNotConfiguredError",
]
