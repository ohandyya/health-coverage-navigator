"""The HTTP machinery all three live clients share — and deliberately nothing else.

**What is genuinely shared is small**: a timeout, a `Retry-After`-honouring transport, a
`User-Agent`, and one function that turns an exception into a clause a model can repeat to a reader.
Everything that distinguishes the three upstreams is *not* here, and that is the design
(docs/structured-api-tools.md §12): three auth mechanisms, three empty-result conventions, three
envelopes. A base class would have to declare all three as overridable, which is a base class in
name only.

**The 429 retry lives in a transport, not in a client.** Phase 2 learned this the expensive way and
`web/client.py` records it: by the time an exception reaches a client, the `Retry-After` header is
gone. Deciding one layer lower is what keeps the decision where the information still exists. This
transport is a near-twin of `web/client._RetryAfterTransport`, and the duplication is deliberate
rather than lazy — that one is wrapped around a vendor SDK's client and this one around our own, and
folding them together would couple the web lane's lifecycle to this package's.
"""

import asyncio
import logging
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

logger = logging.getLogger(__name__)

#: Sent on every live-API request. openFDA and NPPES are public services funded by somebody else;
#: identifying the caller is the minimum courtesy, and it is what lets an operator on their side
#: distinguish this repo's traffic from an anonymous scraper if it ever needs to.
USER_AGENT = "health-coverage-navigator/0.1"


def retry_after_seconds(value: str | None) -> float | None:
    """Parse a `Retry-After` header, which RFC 9110 allows as either seconds or a date.

    Returns `None` for absent or unparseable values — the caller treats that as "do not retry",
    which is the safe direction: guessing a delay for a server that did not name one is how a
    request holds an SSE stream open for a minute.
    """
    if not value:
        return None
    raw = value.strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:  # pragma: no cover - parsedate_to_datetime attaches UTC in practice
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


class RetryAfterTransport(httpx.AsyncBaseTransport):
    """Retry a 429 once, and only when the server asks for a short enough wait.

    Deliberately *one* retry, and only for 429. A 5xx is retried by nobody here: it is far more
    often a real outage than a transient blip, and an honest degradation is a better outcome for a
    waiting reader than a second round trip.
    """

    def __init__(self, wrapped: httpx.AsyncBaseTransport, cap_s: float) -> None:
        self._wrapped = wrapped
        self._cap_s = cap_s

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self._wrapped.handle_async_request(request)
        if response.status_code != 429:
            return response

        delay = retry_after_seconds(response.headers.get("retry-after"))
        if delay is None or delay > self._cap_s:
            return response

        # The discarded response owns a live stream. Draining it before the retry is what keeps the
        # connection reusable; leaking it would eventually exhaust the pool under an eval run.
        await response.aread()
        await response.aclose()
        logger.info("%s 429; retrying once after %.1fs", request.url.host, delay)
        await asyncio.sleep(delay)
        return await self._wrapped.handle_async_request(request)

    async def aclose(self) -> None:
        await self._wrapped.aclose()


def build_client(
    base_url: str,
    *,
    timeout_s: float,
    retry_after_cap_s: float,
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    """An `httpx.AsyncClient` with this repo's timeout, retry, and identity attached.

    `transport` is injectable for the same reason `WebSearchClient.open` takes a `client`: the whole
    offline test suite hangs on being able to hand this an `httpx.MockTransport` without reaching
    through a cached global.
    """
    inner = transport if transport is not None else httpx.AsyncHTTPTransport()
    return httpx.AsyncClient(
        base_url=base_url,
        timeout=timeout_s,
        headers={"User-Agent": USER_AGENT},
        transport=RetryAfterTransport(inner, retry_after_cap_s),
    )


def describe_transport_error(exc: Exception) -> str:
    """One clause naming what went wrong, in words a model can repeat to a reader.

    A stack-trace fragment in an answer would be worse than being vague, so anything unrecognised
    falls back to a neutral description.
    """
    return {
        httpx.ConnectTimeout: "the service could not be reached in time",
        httpx.ReadTimeout: "the service did not respond in time",
        httpx.WriteTimeout: "the service did not respond in time",
        httpx.PoolTimeout: "the request could not be started in time",
        httpx.ConnectError: "the service could not be reached",
    }.get(type(exc), "the service is temporarily unavailable")


def describe_status(status: int) -> str:
    """One clause naming an HTTP failure. `404` is **not** here on purpose.

    A 404 from openFDA means "nothing matched", which is an answer and never a failure
    (docs/structured-api-tools.md §10b.1) — so it must never reach a function whose job is to
    describe outages. Each client maps its own empty-result convention before anything gets here.
    """
    if status == 401 or status == 403:
        return "the service rejected this server's credentials"
    if status == 429:
        return "the service is rate-limiting requests"
    if 500 <= status < 600:
        return "the service is temporarily unavailable"
    return f"the service refused the request (HTTP {status})"


__all__ = [
    "USER_AGENT",
    "RetryAfterTransport",
    "build_client",
    "describe_status",
    "describe_transport_error",
    "retry_after_seconds",
]
