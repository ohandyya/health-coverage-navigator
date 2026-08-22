"""`NppesClient` — CMS's NPI registry, the only route to provider data in this repo.

Imports nothing from `agent/`, like every module in this package.

**No credential and no local artifact** (docs/structured-api-tools.md §5), so like openFDA this
cannot be misconfigured. The `version` parameter is required, not optional.

**Two things here that neither other client has:**

**1. "No such NPI" is `result_count: 0` with an HTTP 200** — not openFDA's 404, and not an outage.
Two upstreams in one phase, two conventions for the same meaning, which is exactly why there is no
shared "is this empty" helper: one that was right for both would be wrong for at least one.

**2. Specialty lives in `taxonomies[]`, and a provider can hold several with exactly one flagged
`primary`.** Reading `taxonomies[0]` would occasionally report a secondary specialty as *the*
specialty — a wrong answer that looks entirely reasonable. `_specialty` picks the primary and says
so; the rest are returned separately rather than dropped.

**Everything this returns is personal data about a real person**, which is why nothing recorded from
it is ever committed: §8a decided provider fixtures are synthesised, and `tests/synthetic.py`
builds them. This client is the reason that decision was needed.
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
from health_coverage_navigator.live.models import ProviderRecord, ProviderResult, Taxonomy

logger = logging.getLogger(__name__)

NPPES_BASE_URL = "https://npiregistry.cms.hhs.gov"

#: Required by the registry, not optional. Pinned rather than left to a default so an upstream
#: version bump is a visible line in a diff instead of a silent change of response shape.
NPPES_API_VERSION = "2.1"

UNAVAILABLE_TEMPLATE = (
    "The NPI registry could not be checked ({reason}). You have seen no provider information for "
    "this question. Say plainly that you could not check it; do not report that a provider does "
    "not exist, because nothing was looked up."
)


@dataclass(slots=True)
class NppesClient:
    """The NPI registry's read API, with degradation. Holds no per-run state."""

    _client: httpx.AsyncClient
    _config: LiveConfig

    @classmethod
    def open(
        cls,
        *,
        config: LiveConfig | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> "NppesClient":
        """Build the client. **Never raises for a missing credential** — there isn't one."""
        resolved = get_config().live if config is None else config
        return cls(
            build_client(
                NPPES_BASE_URL,
                timeout_s=resolved.timeout_s,
                retry_after_cap_s=resolved.retry_after_cap_s,
                transport=transport,
            ),
            resolved,
        )

    async def close(self) -> None:
        await self._client.aclose()

    def _url(self, params: dict[str, str]) -> str:
        """The public, re-fetchable URL for a lookup. No credential to strip."""
        return f"{NPPES_BASE_URL}/api/?{urlencode(params)}"

    async def lookup_npi(self, npi: str, *, sequence: int = 1) -> ProviderResult:
        """One provider by NPI. Never raises.

        `result_count: 0` comes back as `found=False` with `unavailable` unset — the registry holds
        no such NPI, which is an answer.
        """
        params = {"version": NPPES_API_VERSION, "number": npi}
        try:
            response = await self._client.get("/api/", params=params)
        except httpx.HTTPError as exc:
            logger.info("nppes lookup failed: %s", exc)
            return ProviderResult(
                npi=npi,
                unavailable=UNAVAILABLE_TEMPLATE.format(reason=describe_transport_error(exc)),
            )

        if response.status_code != 200:
            return ProviderResult(
                npi=npi,
                unavailable=UNAVAILABLE_TEMPLATE.format(
                    reason=describe_status(response.status_code)
                ),
            )

        try:
            payload = response.json()
        except ValueError:
            logger.warning("nppes returned a body that is not JSON")
            return ProviderResult(
                npi=npi,
                unavailable=UNAVAILABLE_TEMPLATE.format(
                    reason="the NPI registry returned a response this tool could not read"
                ),
            )
        if not isinstance(payload, dict):
            return ProviderResult(
                npi=npi,
                unavailable=UNAVAILABLE_TEMPLATE.format(
                    reason="the NPI registry returned a response this tool could not read"
                ),
            )

        # NPPES reports its own errors inside a 200. An invalid NPI (wrong length, bad check digit)
        # comes back this way rather than as a 4xx, and it is a fact about the *input* — telling the
        # reader their number is malformed is more useful than reporting an outage.
        errors = payload.get("Errors")
        if isinstance(errors, list) and errors:
            first = errors[0] if isinstance(errors[0], dict) else {}
            return ProviderResult(
                npi=npi,
                invalid=str(first.get("description") or "the NPI registry rejected that number"),
                row_id=f"npi#{npi}.x",
                source_url=self._url(params),
            )

        results = payload.get("results")
        if not isinstance(results, list) or not results or not isinstance(results[0], dict):
            # `result_count: 0` — the registry holds no such NPI. An answer, not a failure.
            return ProviderResult(
                npi=npi,
                found=False,
                row_id=f"npi#{npi}.0",
                source_url=self._url(params),
            )

        return ProviderResult(
            npi=npi,
            found=True,
            provider=_provider(results[0], npi, sequence),
            source_url=self._url(params),
        )


def _name(basic: dict[str, Any]) -> str:
    """A display name for either kind of enumeration.

    NPPES has two: an individual (`NPI-1`) carries `first_name`/`last_name`, an organization
    (`NPI-2`) carries `organization_name` and no personal name at all. Reading only the personal
    fields would return an empty name for every hospital and group practice in the registry.
    """
    organization = str(basic.get("organization_name") or "").strip()
    if organization:
        return organization
    parts = [
        str(basic.get(key) or "").strip() for key in ("first_name", "middle_name", "last_name")
    ]
    name = " ".join(part for part in parts if part)
    credential = str(basic.get("credential") or "").strip()
    return f"{name}, {credential}" if name and credential else name


def _taxonomies(raw: Any) -> list[Taxonomy]:
    """Every specialty on the record, **primary first**.

    Ordering rather than filtering: a provider's secondary specialties are real information, and a
    reader asking "what does this doctor do" is better served by all of them with the primary named
    than by one with the others silently dropped.
    """
    if not isinstance(raw, list):
        return []
    entries = [
        Taxonomy(
            code=str(item.get("code") or ""),
            description=str(item.get("desc") or ""),
            primary=bool(item.get("primary")),
            state=str(item.get("state") or "") or None,
            license=str(item.get("license") or "") or None,
        )
        for item in raw
        if isinstance(item, dict)
    ]
    return sorted(entries, key=lambda entry: not entry.primary)


def _provider(record: dict[str, Any], npi: str, sequence: int) -> ProviderRecord:
    raw_basic = record.get("basic")
    basic: dict[str, Any] = raw_basic if isinstance(raw_basic, dict) else {}
    raw_addresses = record.get("addresses")
    addresses: list[Any] = raw_addresses if isinstance(raw_addresses, list) else []
    location: dict[str, Any] = next(
        (
            item
            for item in addresses
            if isinstance(item, dict) and item.get("address_purpose") == "LOCATION"
        ),
        {},
    )
    taxonomies = _taxonomies(record.get("taxonomies"))
    return ProviderRecord(
        row_id=f"npi#{sequence}.1",
        # `number` is upstream's spelling; this is the one place it becomes `npi`.
        npi=str(record.get("number") or npi),
        name=_name(basic),
        enumeration_type=str(record.get("enumeration_type") or ""),
        status=str(basic.get("status") or "") or None,
        taxonomies=taxonomies,
        city=str(location.get("city") or "") or None,
        state=str(location.get("state") or "") or None,
        last_updated=str(basic.get("last_updated") or "") or None,
    )


__all__ = ["NPPES_API_VERSION", "NPPES_BASE_URL", "UNAVAILABLE_TEMPLATE", "NppesClient"]
