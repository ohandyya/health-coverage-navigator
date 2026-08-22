"""Tests for `web/client.py` — the whole web lane below the agent, with no network and no key.

**Every test here drives a real `AsyncTavilyClient`**, not a stand-in for one. That is the payoff
of the SDK choice recorded in docs/web_search_tool.md §4: the SDK accepts an injected
`httpx.AsyncClient`, so `httpx.MockTransport` can back the entire path — request construction, the
SDK's own status-code-to-exception mapping, our retry transport, hygiene, and every degradation —
against canned HTTP responses. A hand-rolled fake client would have tested our code against our own
idea of the SDK's behaviour, which is exactly the assumption most worth checking.

**Fixtures are synthetic, and that is a licensing decision rather than a testing preference.** It
would be natural to record real Tavily responses to committed JSON. A Tavily response body carries
extracted text from third-party web pages, this repo is public, and CLAUDE.md's data guardrail
requires per-source clearance before anything is committed — a news article's body text is
copyrighted by its publisher. Invented content over `example.org` URLs asserts everything below,
reads better in a diff, and raises no question at all.
"""

import json

import httpx
import pytest

from health_coverage_navigator.config import WebConfig
from health_coverage_navigator.web.client import (
    WebSearchClient,
    WebSearchNotConfiguredError,
    _retry_after_seconds,
    _RetryAfterTransport,
    domain_of,
    normalize_url,
)

CONFIG = WebConfig(
    search_depth="basic",
    max_results=3,
    chunks_per_source=3,
    max_results_per_domain=2,
    max_searches_per_run=3,
    timeout_s=5.0,
    retry_after_cap_s=5.0,
    exclude_domains=(),
)


def result(url: str, content: str = "Some page text.", **extra) -> dict:
    return {"url": url, "title": f"Title for {url}", "content": content, "score": 0.9, **extra}


def build(handler) -> WebSearchClient:
    """A `WebSearchClient` over a real `AsyncTavilyClient` over a mocked transport."""
    from tavily import AsyncTavilyClient

    http = httpx.AsyncClient(
        base_url="https://api.tavily.com", transport=httpx.MockTransport(handler)
    )
    return WebSearchClient.open(config=CONFIG, client=AsyncTavilyClient(api_key="k", client=http))


def responding(*results: dict, status: int = 200, headers: dict | None = None):
    """A handler returning one fixed payload, and recording the request bodies it saw."""
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        body = {"query": "q", "results": list(results), "response_time": 0.1}
        return httpx.Response(status, json=body, headers=headers or {})

    handler.seen = seen  # type: ignore[attr-defined]
    return handler


# ---------------------------------------------------------------- the happy path -------------


async def test_search_returns_citable_results() -> None:
    client = build(responding(result("https://example.org/a"), result("https://example.com/b")))
    found = await client.search("open enrollment deadline", sequence=1)

    assert found.unavailable is None
    assert [r.result_id for r in found.results] == ["web#s1.1", "web#s1.2"]
    assert [r.domain for r in found.results] == ["example.org", "example.com"]
    assert found.results[0].url == "https://example.org/a"


async def test_result_ids_are_numbered_by_search() -> None:
    """The id says which call produced the row — the same role `sequence` plays for a query."""
    client = build(responding(result("https://example.org/a")))
    second = await client.search("q", sequence=2)
    assert second.results[0].result_id == "web#s2.1"


async def test_config_reaches_the_request_body() -> None:
    handler = responding(result("https://example.org/a"))
    client = build(handler)
    await client.search("q", topic="news", time_range="week")

    sent = handler.seen[0]  # type: ignore[attr-defined]
    assert sent["search_depth"] == "basic"
    assert sent["chunks_per_source"] == 3
    assert sent["topic"] == "news"
    assert sent["time_range"] == "week"
    # Never taken: Tavily's own synthesized answer is uncitable, and raw content floods the context.
    assert sent.get("include_answer") in (None, False)
    assert sent.get("include_raw_content") in (None, False)


async def test_over_fetches_so_hygiene_has_something_to_drop() -> None:
    """Asking for exactly `cap` and then dropping duplicates would leave the model short."""
    handler = responding(result("https://example.org/a"))
    client = build(handler)
    await client.search("q")
    assert handler.seen[0]["max_results"] > CONFIG.max_results  # type: ignore[attr-defined]


async def test_published_date_survives_when_tavily_sends_one() -> None:
    client = build(responding(result("https://example.org/a", published_date="2026-08-01")))
    found = await client.search("q", topic="news")
    assert found.results[0].published_date == "2026-08-01"


async def test_missing_published_date_is_none_not_a_guess() -> None:
    client = build(responding(result("https://example.org/a")))
    found = await client.search("q")
    assert found.results[0].published_date is None


# ---------------------------------------------------------------- hygiene ---------------------


async def test_duplicate_urls_collapse() -> None:
    client = build(
        responding(
            result("https://example.org/a"),
            result("https://example.org/a#section-2"),
            result("https://example.org/a?utm_source=news"),
        )
    )
    found = await client.search("q")
    assert len(found.results) == 1


async def test_one_domain_cannot_fill_the_list() -> None:
    """Otherwise "several sources agree" is one source repeated."""
    client = build(
        responding(
            result("https://example.org/a"),
            result("https://example.org/b"),
            result("https://example.org/c"),
            result("https://example.com/d"),
        )
    )
    found = await client.search("q")
    assert [r.domain for r in found.results] == ["example.org", "example.org", "example.com"]


async def test_results_are_capped() -> None:
    client = build(responding(*(result(f"https://site{i}.org/a") for i in range(10))))
    found = await client.search("q")
    assert len(found.results) == CONFIG.max_results


async def test_max_results_argument_cannot_exceed_the_configured_cap() -> None:
    client = build(responding(*(result(f"https://site{i}.org/a") for i in range(10))))
    found = await client.search("q", max_results=50)
    assert len(found.results) == CONFIG.max_results


async def test_uncitable_results_are_dropped() -> None:
    """No content is nothing to quote; no URL is nothing to link. Either way it cannot be cited."""
    client = build(
        responding(
            result("https://example.org/a", content="   "),
            result("", content="text"),
            result("https://example.com/b"),
        )
    )
    found = await client.search("q")
    assert [r.url for r in found.results] == ["https://example.com/b"]


async def test_tavily_ranking_order_is_preserved() -> None:
    """The one signal here we did not invent. §7 declines to replace it with a heuristic."""
    client = build(
        responding(
            result("https://blog.example.org/a"),
            result("https://cms.example.org/b"),
        )
    )
    found = await client.search("q")
    assert [r.domain for r in found.results] == ["blog.example.org", "cms.example.org"]


# ---------------------------------------------------------------- degradation -----------------
#
# The whole point of the lane's error handling: an outage must never be reportable as "the web does
# not cover this". Each case asserts BOTH that results are empty AND that `unavailable` is set,
# because it is the pair that distinguishes the two states.


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (429, "rate-limiting"),
        (432, "plan limit"),
        (433, "plan limit"),
        (403, "plan limit"),
        (401, "credentials"),
        (400, "rejected the query"),
        (500, "temporarily unavailable"),
    ],
)
async def test_every_tavily_failure_degrades_honestly(status: int, expected: str) -> None:
    client = build(responding(status=status))
    found = await client.search("q")

    assert found.results == []
    assert found.unavailable is not None
    assert expected in found.unavailable
    # The instruction matters as much as the reason: this text lands in the model's context.
    assert "could not check the web" in found.unavailable


async def test_a_timeout_degrades_rather_than_raising() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    found = await build(handler).search("q")
    assert found.results == []
    assert found.unavailable is not None
    assert "did not respond in time" in found.unavailable


async def test_a_connection_failure_degrades_rather_than_raising() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    found = await build(handler).search("q")
    assert found.unavailable is not None


async def test_an_unreadable_body_degrades_rather_than_raising() -> None:
    """A 200 whose shape we cannot use is still a failure to check the web."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [{"nope": 1}]})

    found = await build(handler).search("q")
    assert found.results == []
    assert found.unavailable is not None
    assert "could not read" in found.unavailable


async def test_zero_results_is_a_finding_not_a_failure() -> None:
    """The distinction the whole `unavailable` field exists to preserve."""
    found = await build(responding()).search("q")
    assert found.results == []
    assert found.unavailable is None


# ---------------------------------------------------------------- the 429 retry ---------------


async def test_retry_after_is_honoured_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """The SDK cannot do this: it maps 429 to an exception and drops the response carrying
    the header."""
    slept: list[float] = []

    async def no_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("health_coverage_navigator.web.client.asyncio.sleep", no_sleep)

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={}, headers={"retry-after": "2"})
        return httpx.Response(200, json={"results": [result("https://example.org/a")]})

    from tavily import AsyncTavilyClient

    http = httpx.AsyncClient(
        base_url="https://api.tavily.com",
        transport=_RetryAfterTransport(httpx.MockTransport(handler), CONFIG.retry_after_cap_s),
    )
    client = WebSearchClient.open(config=CONFIG, client=AsyncTavilyClient(api_key="k", client=http))

    found = await client.search("q")
    assert calls["n"] == 2
    assert slept == [2.0]
    assert found.unavailable is None
    assert len(found.results) == 1


async def test_a_long_retry_after_degrades_instead_of_waiting() -> None:
    """A reader watching a spinner does not have a minute. The cap is what says so."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429, json={}, headers={"retry-after": "600"})

    from tavily import AsyncTavilyClient

    http = httpx.AsyncClient(
        base_url="https://api.tavily.com",
        transport=_RetryAfterTransport(httpx.MockTransport(handler), CONFIG.retry_after_cap_s),
    )
    client = WebSearchClient.open(config=CONFIG, client=AsyncTavilyClient(api_key="k", client=http))

    found = await client.search("q")
    assert calls["n"] == 1  # never retried
    assert found.unavailable is not None


async def test_a_429_with_no_retry_after_is_not_retried() -> None:
    """Guessing a delay for a server that named none is how a request hangs."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429, json={})

    from tavily import AsyncTavilyClient

    http = httpx.AsyncClient(
        base_url="https://api.tavily.com",
        transport=_RetryAfterTransport(httpx.MockTransport(handler), CONFIG.retry_after_cap_s),
    )
    client = WebSearchClient.open(config=CONFIG, client=AsyncTavilyClient(api_key="k", client=http))

    assert (await client.search("q")).unavailable is not None
    assert calls["n"] == 1


@pytest.mark.parametrize(
    ("header", "expected"),
    [("3", 3.0), ("0", 0.0), (None, None), ("", None), ("not-a-date", None)],
)
def test_retry_after_parsing(header: str | None, expected: float | None) -> None:
    assert _retry_after_seconds(header) == expected


def test_retry_after_accepts_an_http_date() -> None:
    """RFC 9110 allows both forms and Tavily is free to send either."""
    from datetime import UTC, datetime, timedelta
    from email.utils import format_datetime

    when = datetime.now(UTC) + timedelta(seconds=30)
    parsed = _retry_after_seconds(format_datetime(when))
    assert parsed is not None and 25 <= parsed <= 35


# ---------------------------------------------------------------- helpers ---------------------


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("https://example.org/a", "https://example.org/a/"),
        ("https://example.org/a", "https://EXAMPLE.org/a"),
        ("https://example.org/a", "https://example.org/a#top"),
        ("https://example.org/a", "https://example.org/a?utm_campaign=x"),
    ],
)
def test_urls_that_are_the_same_page(a: str, b: str) -> None:
    assert normalize_url(a) == normalize_url(b)


def test_a_meaningful_query_string_is_not_stripped() -> None:
    """On a government site the query string is frequently the whole address of the content."""
    assert normalize_url("https://example.org/p?id=240.4") != normalize_url("https://example.org/p")


@pytest.mark.parametrize(
    ("url", "domain"),
    [
        ("https://www.cms.gov/x", "cms.gov"),
        ("https://CMS.gov/x", "cms.gov"),
        ("https://sub.example.org/x", "sub.example.org"),
    ],
)
def test_domain_extraction(url: str, domain: str) -> None:
    assert domain_of(url) == domain


# ---------------------------------------------------------------- configuration ---------------


def test_no_key_is_a_startup_error_naming_the_fix(monkeypatch: pytest.MonkeyPatch) -> None:
    """`WebSearchClient.open` refuses to pretend the lane exists. `app.py` degrades it to `None`."""
    import health_coverage_navigator.settings as settings_module

    class NoKey:
        tavily_api_key = None

    monkeypatch.setattr(settings_module, "get_secrets", lambda: NoKey())
    with pytest.raises(WebSearchNotConfiguredError) as excinfo:
        WebSearchClient.open(config=CONFIG)

    message = str(excinfo.value)
    assert "TAVILY_API_KEY" in message
    assert "agent.web_tools" in message


async def test_exclude_domains_reaches_tavily() -> None:
    handler = responding(result("https://example.org/a"))
    config = CONFIG.model_copy(update={"exclude_domains": ("spam.example",)})
    from tavily import AsyncTavilyClient

    http = httpx.AsyncClient(
        base_url="https://api.tavily.com", transport=httpx.MockTransport(handler)
    )
    client = WebSearchClient.open(config=config, client=AsyncTavilyClient(api_key="k", client=http))
    await client.search("q")
    assert handler.seen[0]["exclude_domains"] == ["spam.example"]  # type: ignore[attr-defined]
