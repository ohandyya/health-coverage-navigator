"""`WebSearchClient` — Tavily's `/search`, wrapped so an outage can never look like an answer.

The read side of the web lane. `agent/web_tools.py` adds the trace and the run-scoped bookkeeping;
reaching Tavily is here, and this module imports nothing from `agent/` so every test below runs
with no model, no agent, and no PydanticAI import.

Three jobs, in order of how much they matter:

**1. An outage is not an empty result.** Every failure Tavily can produce — 401, 403/432/433, 429,
a timeout, a 5xx, a malformed body — becomes a `WebSearchResults` with `unavailable` set and no
results. It is never an exception escaping into the agent loop. The alternatives all lose
something: raising kills an answer the corpus could have carried, `ModelRetry` invites the model to
retry a network condition it cannot fix inside a run, and returning a bare empty list makes an
unreachable API indistinguishable from "the web does not cover this" — which in a health tool is a
wrong answer wearing the clothes of an honest one.

**2. Results are typed at the boundary.** `AsyncTavilyClient.search()` returns an untyped `dict`.
`_RawResult` is where a missing `content` or a non-float `score` becomes a clear error instead of
an `AttributeError` three frames later, and where a result too broken to cite is dropped rather
than surfaced. Phase 3 will make the same argument for the Marketplace API; the first live
third-party response just arrives here.

**3. Hygiene, before the model sees anything.** Duplicate URLs collapse and no single domain may
fill the list (docs/web_search_tool.md §7). What is deliberately *not* done: no allowlist, no
re-ranking, no authority scoring. The questions that reach this lane are the ones no official page
has published yet, so filtering to official domains would guarantee an abstention on exactly what
the lane exists to answer — and a heuristic re-ranking is something this phase's eval slice
(routing and groundedness) could not defend with a number.

**The 429 retry lives in a transport, not in this class.** `_RetryAfterTransport` is the only
place that can honour Tavily's `retry-after` header: the SDK maps a 429 to
`UsageLimitExceededError(detail)` and drops the response object, so by the time an exception
reaches us the header is gone. Handling it one layer lower means the SDK sees either a success or
a final 429, and this module stays simple.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from pydantic import BaseModel, ValidationError

from health_coverage_navigator.config import WebConfig, get_config
from health_coverage_navigator.web.models import WebResult, WebSearchResults

logger = logging.getLogger(__name__)

#: Tavily's endpoint. Named here rather than left to the SDK's default so the test transport and
#: the real client agree about what a request URL looks like.
TAVILY_BASE_URL = "https://api.tavily.com"

#: Query parameters stripped before two URLs are compared for deduplication. Campaign tags do not
#: change which page you land on, and Tavily does return the same article reached two ways.
_TRACKING_PARAMS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "gclid",
        "fbclid",
        "mc_cid",
        "mc_eid",
        "ref",
        "source",
    }
)

#: What the model is told when the lane could not be reached. Phrased as an instruction rather than
#: an error string, because it lands in the model's context and the next thing it does is decide
#: whether to answer.
UNAVAILABLE_TEMPLATE = (
    "The web search could not be completed ({reason}). You have not seen any web results for this "
    "question. Say plainly that you could not check the web; answer from your other sources only "
    "if they genuinely cover the question, and otherwise abstain."
)

BUDGET_SPENT = (
    "This run's web-search budget is spent ({limit} searches). Searching again will not help — "
    "answer from what you have already retrieved, or abstain."
)


class WebSearchNotConfiguredError(RuntimeError):
    """No Tavily API key. Raised at startup, never mid-run.

    The web analogue of `VectorsNotBuiltError` and `StructuredNotBuiltError`: it means the lane was
    never available on this machine, which is a deployment fact, not a search failure. `app.py`
    degrades it to `AppContext.web = None` and `routes/chat.py` turns that into a 503 naming the
    fix — the same refusal-to-degrade every other lane makes.
    """


class _RawResult(BaseModel):
    """One result exactly as Tavily sends it. The typed boundary, and nothing more.

    `title` and `score` are optional because a search API is not our schema to guarantee; `url` and
    `content` are not, because a result missing either cannot be cited and is dropped rather than
    shown to the model as evidence it cannot use.
    """

    url: str
    content: str
    title: str = ""
    score: float | None = None
    published_date: str | None = None


class _RawSearch(BaseModel):
    results: list[_RawResult] = []


def _retry_after_seconds(value: str | None) -> float | None:
    """Parse a `Retry-After` header, which RFC 9110 allows in two forms.

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


class _RetryAfterTransport(httpx.AsyncBaseTransport):
    """Retry a 429 once, and only when the server asks for a short enough wait.

    **This is the only layer that can see the header.** `AsyncTavilyClient._handle_error_response`
    turns a 429 into `UsageLimitExceededError(detail)` and discards the response, so an exception
    reaching `WebSearchClient` no longer knows how long to wait. Wrapping the transport puts the
    decision where the information still exists.

    Deliberately *one* retry, and only for 429. A 5xx is retried by nobody here: it is far more
    often a real outage than a transient blip, and the honest degradation in `WebSearchClient` is a
    better outcome for a waiting reader than a second round trip. Anything more — backoff across
    requests, a token bucket, a response cache — is Phase 3's, which `plan.md` scopes explicitly.
    """

    def __init__(self, wrapped: httpx.AsyncBaseTransport, cap_s: float) -> None:
        self._wrapped = wrapped
        self._cap_s = cap_s

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self._wrapped.handle_async_request(request)
        if response.status_code != 429:
            return response

        delay = _retry_after_seconds(response.headers.get("retry-after"))
        if delay is None or delay > self._cap_s:
            return response

        # The discarded response owns a live stream. Draining it before the retry is what keeps the
        # connection reusable; leaking it would eventually exhaust the pool under an eval run.
        await response.aread()
        await response.aclose()
        logger.info("tavily 429; retrying once after %.1fs", delay)
        await asyncio.sleep(delay)
        return await self._wrapped.handle_async_request(request)

    async def aclose(self) -> None:
        await self._wrapped.aclose()


def tavily_client(config: WebConfig, api_key: str) -> Any:
    """An `AsyncTavilyClient` with the retry transport and the timeout attached.

    **Call this through the module** (`client.tavily_client(...)`), not through a
    `from ... import tavily_client`. It is one of two functions in this repo that spend money
    outside PydanticAI — `vectors/embedder.openai_embedder` is the other — so `tests/conftest.py`
    replaces it suite-wide to keep the no-provider invariant, and a name bound at import time in a
    consumer module would not be replaced.

    The `httpx` client is built by hand rather than left to the SDK for two reasons, both of which
    the SDK cannot give us: the 429 retry above, and a timeout that suits a browser rather than a
    batch job. It is also the seam the whole offline test suite hangs on — `AsyncTavilyClient`
    accepts an injected client, so `httpx.MockTransport` can back every path in this module.
    """
    from tavily import AsyncTavilyClient

    http = httpx.AsyncClient(
        base_url=TAVILY_BASE_URL,
        timeout=config.timeout_s,
        transport=_RetryAfterTransport(httpx.AsyncHTTPTransport(), config.retry_after_cap_s),
    )
    return AsyncTavilyClient(api_key=api_key, client=http)


def normalize_url(url: str) -> str:
    """A URL reduced to what decides whether two results are the same page.

    Drops the fragment (always a within-page anchor), lowercases the host, strips a trailing slash,
    and removes campaign parameters. Deliberately keeps every other query parameter: on a government
    site a query string is frequently the whole address of the content.
    """
    parts = urlsplit(url)
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in _TRACKING_PARAMS
    ]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))


def domain_of(url: str) -> str:
    """The site a result came from, without `www.`. Shown to the model and to the reader."""
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


@dataclass(slots=True)
class WebSearchClient:
    """Tavily's `/search`, with hygiene and degradation. Holds no per-run state."""

    _client: Any
    _config: WebConfig

    @classmethod
    def open(
        cls,
        *,
        config: WebConfig | None = None,
        client: Any = None,
    ) -> "WebSearchClient":
        """Build the client, refusing to pretend the lane exists without a key.

        `config` and `client` are parameters rather than module reads for the same
        injectable-path reason `StructuredStore.open` takes `processed_dir`: a test needs to point
        this at a `MockTransport` without reaching through a cached global.
        """
        resolved = get_config().web if config is None else config
        if client is not None:
            return cls(client, resolved)

        from health_coverage_navigator.settings import get_secrets

        key = get_secrets().tavily_api_key
        if key is None:
            raise WebSearchNotConfiguredError(
                "TAVILY_API_KEY is not set. The web lane needs it — add it to `.env` (see "
                "`.env.example`), or set `agent.web_tools: false` in config.yaml to run without "
                "web search."
            )
        return cls(tavily_client(resolved, key.get_secret_value()), resolved)

    async def close(self) -> None:
        close = getattr(self._client, "close", None)
        if close is not None:
            await close()

    async def search(
        self,
        query: str,
        *,
        topic: str = "general",
        time_range: str | None = None,
        max_results: int | None = None,
        sequence: int = 1,
    ) -> WebSearchResults:
        """One search, and never an exception.

        `sequence` numbers the search within an agent run so a result id says which call produced
        it — the same role `sequence` plays for a structured query's row ids.
        """
        config = self._config
        cap = (
            config.max_results
            if max_results is None
            else max(1, min(max_results, config.max_results))
        )

        try:
            raw = await self._client.search(
                query,
                search_depth=config.search_depth,
                topic=topic,
                time_range=time_range,
                # Over-fetch, because hygiene removes results *after* Tavily ranks them. Asking for
                # exactly `cap` and then dropping three duplicates leaves the model with two.
                max_results=min(cap * 2, 20),
                chunks_per_source=config.chunks_per_source,
                exclude_domains=list(config.exclude_domains) or None,
                include_answer=False,
                include_raw_content=False,
                include_images=False,
            )
        except Exception as exc:  # noqa: BLE001 - every failure degrades; see the module docstring
            return WebSearchResults(
                query=query,
                unavailable=UNAVAILABLE_TEMPLATE.format(reason=_describe(exc)),
            )

        try:
            parsed = _RawSearch.model_validate(raw)
        except ValidationError as exc:
            logger.warning("tavily returned an unusable body: %s", exc)
            return WebSearchResults(
                query=query,
                unavailable=UNAVAILABLE_TEMPLATE.format(
                    reason="the search service returned a response this tool could not read"
                ),
            )

        return WebSearchResults(query=query, results=self._clean(parsed.results, cap, sequence))

    def _clean(self, results: list[_RawResult], cap: int, sequence: int) -> list[WebResult]:
        """Dedupe, cap per domain, and assign citable ids — in Tavily's ranked order.

        Order is preserved rather than re-sorted: Tavily's ranking is the one signal here we did not
        invent, and replacing it with a heuristic of our own is exactly what §7 declines to do.
        """
        seen: set[str] = set()
        per_domain: dict[str, int] = {}
        kept: list[WebResult] = []

        for raw in results:
            if len(kept) >= cap:
                break
            if not raw.url.strip() or not raw.content.strip():
                # Uncitable by construction: nothing to link to, or nothing to quote.
                continue
            key = normalize_url(raw.url)
            if key in seen:
                continue
            domain = domain_of(raw.url)
            if per_domain.get(domain, 0) >= self._config.max_results_per_domain:
                continue

            seen.add(key)
            per_domain[domain] = per_domain.get(domain, 0) + 1
            kept.append(
                WebResult(
                    result_id=f"web#s{sequence}.{len(kept) + 1}",
                    url=raw.url,
                    domain=domain,
                    title=raw.title.strip() or domain,
                    content=raw.content,
                    score=raw.score,
                    published_date=raw.published_date,
                )
            )
        return kept


def _describe(exc: Exception) -> str:
    """One clause naming what went wrong, in words a model can repeat to a reader.

    Mapped from the SDK's exception types rather than from status codes, because that is what
    reaches us — `tavily.errors` raises a distinct class per failure. Anything unrecognised falls
    back to a neutral description: a stack-trace fragment in an answer would be worse than vague.
    """
    name = type(exc).__name__
    return {
        "UsageLimitExceededError": "the search service is rate-limiting requests",
        "TavilyKeylessLimitError": "the search service is rate-limiting requests",
        "ForbiddenError": "the search account has reached its plan limit",
        "InvalidAPIKeyError": "the search service rejected this server's credentials",
        "MissingAPIKeyError": "this server has no search credentials configured",
        "BadRequestError": "the search service rejected the query",
        "TimeoutError": "the search service did not respond in time",
        "ConnectError": "the search service could not be reached",
        "ReadTimeout": "the search service did not respond in time",
        "ConnectTimeout": "the search service could not be reached",
    }.get(name, "the search service is temporarily unavailable")
