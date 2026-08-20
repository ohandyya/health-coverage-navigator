"""The web lane's one tool: the searching, wrapped in what only makes sense *inside a run*.

`web/client.py` does the searching. This module does the same two jobs `tools.py` does for the
reference lane and `structured_tools.py` does for the relational one:

**The trace.** Every call appends a `tool_call` / `tool_result` pair to `deps.trace` with the real
query and a real duration. Here it carries something the other lanes' steps cannot: whether the
agent went to the open web at all. That is the single most useful line in a Phase 2 trace, because
the phase's whole question is which lane a question reached.

**What the agent has actually seen.** Every result is recorded in `deps.seen_results`, and the
output validator refuses a citation of anything else. In this lane that matters more than in either
of the others. A chunk id is meaningless outside this repo and a row id is obviously internal, but a
**URL is guessable** — a model can produce `https://www.cms.gov/newsroom/...` that looks exactly as
citable whether a tool returned it or not. Citing by `result_id` and checking membership here is
what makes that impossible rather than discouraged.

**One tool, not two** (docs/web_search_tool.md §3). Tavily's `content` is already query-reranked
extracted page text with the source URL attached, which is what makes a web result citable at all;
one call produces evidence. `read_url` over `/extract` — the `get_chunk` of this lane — is
deliberately deferred, and the cost is real and stated in the tool description: when a snippet stops
one sentence short, the only recovery is a narrower query. Build it when traces show that biting.
"""

import time

from pydantic_ai import ModelRetry, RunContext

from health_coverage_navigator.agent.deps import AnswerDeps
from health_coverage_navigator.config import get_config
from health_coverage_navigator.web.client import BUDGET_SPENT
from health_coverage_navigator.web.models import WebSearchResults

#: What the tool says when the lane is configured but the client is not there. Unreachable in the
#: app — `routes/chat.py` returns a 503 before the agent runs — but reachable in a test or a script
#: that builds `AnswerDeps` by hand, where a `None` dereference would read as a bug in the agent
#: rather than a missing credential.
NO_CLIENT = (
    "Web search is not available in this session. Answer from the reference corpus or the plan "
    "data if they cover the question, and otherwise say that you could not check the web."
)

#: Tavily's `topic` values that this tool exposes. `finance` is omitted deliberately: it routes to a
#: market-data index that has nothing to say about health coverage, and every value offered to a
#: model is a value it can choose wrongly.
TOPICS = ("general", "news")

TIME_RANGES = ("day", "week", "month", "year")


async def web_search(
    ctx: RunContext[AnswerDeps],
    query: str,
    topic: str = "general",
    time_range: str | None = None,
    max_results: int = 5,
) -> WebSearchResults:
    """Search the **open web** for something your other sources cannot know.

    Reach for this when the question turns on what is true *now* or on something outside your
    library: a recent recall or safety alert, a deadline or figure for a plan year your documents do
    not cover, a news event, a specific insurer's or employer's own announcement.

    **Do not reach for it first.** Your reference corpus and plan data are CMS publications; the web
    is whatever ranked highly today. For "what is a deductible", "does Medicare cover X in general",
    or "how do I appeal", search the corpus — a web search for those returns an insurer's marketing
    page that is citable, plausible, and worse than the official explanation you already hold. When
    both could answer, prefer the source you hold and use the web to check whether it is current.

    **Read the `domain` on every result before you trust it.** Say whose page a claim came from when
    it matters — "according to CMS" and "according to a news report" are different claims, and in
    this domain a reader deserves to know which one they are getting. When results disagree, prefer
    an official source (`cms.gov`, `medicare.gov`, `healthcare.gov`, `fda.gov`) among what you
    actually found — but do not invent one you did not find.

    **Dates.** `published_date` is only returned when `topic="news"`. For anything that turns on
    currency, either search with `topic="news"` so results are dated, or say that you could not
    confirm how recent the page is. An undated page about a deadline is weak evidence.

    **There is no tool to read further into a page.** Each result carries the passages Tavily
    selected for your query, not the whole page. If a result is clearly the right page but stops
    just short of the answer, **search again with a narrower, more specific query** — do not assume
    the answer is absent, and do not guess what the rest of the page says.

    **If `unavailable` comes back set,** the search did not happen — a rate limit or an outage, not
    an empty web. Say plainly that you could not check the web, and never fill that gap from your
    own knowledge.

    Args:
        query: What to search for, in natural language. Terms a page would actually use work better
            than a conversational question.
        topic: `general` for most things; `news` for current events, recalls and announcements —
            and the only way to get publication dates.
        time_range: Restrict to recently published pages: `day`, `week`, `month` or `year`. Use it
            when the question is explicitly about what changed or what is new.
        max_results: How many pages to return, at most 5.
    """
    deps = ctx.deps
    client = deps.web
    if client is None:  # pragma: no cover - `select_tools` makes this unreachable in the app
        raise ModelRetry(NO_CLIENT)

    if topic not in TOPICS:
        raise ModelRetry(f"topic must be one of {list(TOPICS)}; got {topic!r}.")
    if time_range is not None and time_range not in TIME_RANGES:
        raise ModelRetry(f"time_range must be one of {list(TIME_RANGES)} or omitted.")

    limit = get_config().web.max_searches_per_run
    started = time.perf_counter()

    if deps.web_searches >= limit:
        # Deliberately **not** a `ModelRetry`. The budget will not refill inside this run, so a
        # retry could only produce the same refusal while spending the grounding guardrail's
        # allowance on it. This is a result the model reads and acts on — the same shape
        # `unavailable` takes for an outage, for the same reason.
        results = WebSearchResults(query=query, unavailable=BUDGET_SPENT.format(limit=limit))
    else:
        results = await client.search(
            query,
            topic=topic,
            time_range=time_range,
            max_results=max_results,
            sequence=deps.next_web_search(),
        )
        deps.remember_results(results.results)

    results.searches_remaining = max(0, limit - deps.web_searches)
    deps._record(
        "web_search",
        {"query": query, "topic": topic, "time_range": time_range, "max_results": max_results},
        _summarize(results),
        int((time.perf_counter() - started) * 1000),
    )
    return results


def _summarize(results: WebSearchResults) -> str:
    """The one line the trace panel shows without expanding.

    Names the domains rather than only counting results, because *where the agent went* is the
    interesting part of a web step — a reader scanning a trace wants to see `cms.gov` or
    `somebodysblog.example` without expanding anything.
    """
    if results.unavailable:
        return "web unavailable — no results"
    if not results.results:
        return "no web results"
    domains = ", ".join(dict.fromkeys(r.domain for r in results.results))
    return f"{len(results.results)} result(s) from {domains}"


#: Phase 2's web lane. Registered alongside every earlier lane's tools, never instead of them: the
#: measurement of this phase is whether the agent picks the right *kind* of source when three kinds
#: exist, which it cannot do if it only has one.
WEB_TOOLS = (web_search,)

__all__ = ["NO_CLIENT", "TIME_RANGES", "TOPICS", "WEB_TOOLS", "web_search"]
