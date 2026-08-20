"""What the web tool returns — the shapes the *language model* reads.

These sit here rather than in `agent/models.py` for the same structural reason
`structured/models.py` exists: `client.py` builds them and `web/` must not import from `agent/`
(docs/web_search_tool.md §9). The prompt-surface rule still applies — every field name and
docstring below is text a model reads, so it is written for that reader.

Two things carried deliberately rather than smoothed away:

**`published_date` is `None` far more often than it is set.** Tavily only returns it for
`topic="news"`. That is not a gap to paper over with a guess: *"is this current"* is the entire
reason a health question reaches the web, and a 2019 page about a 2019 deadline reads exactly like
a 2026 one. An undated result says so, and the tool description tells the model what that means.

**`unavailable` is a first-class field, not an exception.** A rate limit or an outage must be
distinguishable from "nothing matched" — the web analogue of the relational lane's rule that zero
rows is a finding rather than a failure (docs/relational-tool.md §6). Collapsing the two would let
an outage be reported to a reader as "the web does not cover this".
"""

from pydantic import BaseModel, Field


class WebResult(BaseModel):
    """One page the web search returned, as the agent sees it."""

    result_id: str
    """Cite this exact string. It is the only handle that resolves back to a source.

    A URL is deliberately **not** the handle: a model can write a plausible URL it never retrieved,
    and `https://www.cms.gov/...` looks exactly as citable whether a tool returned it or not. An id
    of this shape cannot be guessed into existence, because nothing carries one until a search
    assigns it."""

    url: str
    """Where the page lives. Shown to the reader as the citation's link."""

    domain: str
    """The site this came from, e.g. `cms.gov`. **Read it before trusting the content.** Your other
    sources are CMS publications; a web result is whatever ranked highly today. When two results
    disagree, who published them is part of the answer."""

    title: str

    content: str
    """The relevant extract of the page, verbatim. **Quote from this and nowhere else.**

    It is a handful of passages selected from the page for this query, not the whole page — so an
    answer may sit just outside it. There is no tool to read further into a page: if this stops
    short of what you need, search again with a narrower query."""

    score: float | None = None
    """Tavily's relevance for this query. **Comparable only within one result list**, and on a
    different scale from `search_corpus`'s or `vector_search`'s. A high score means "matched this
    query well", never "is true" or "is authoritative"."""

    published_date: str | None = None
    """When the page was published — **only ever available when you searched with `topic="news"`**.

    `None` means unknown, not recent. For any question that turns on currency, an undated result is
    weak evidence: say what you found and that you could not confirm its date, or search again with
    `topic="news"` to get dated results."""


class WebSearchResults(BaseModel):
    """What `web_search` reports: what was found, or why nothing could be.

    The two are different states and the model must not conflate them. An empty `results` with
    `unavailable=None` means the web genuinely does not appear to cover this. An empty `results`
    with `unavailable` set means **nothing was asked** — the search never happened.
    """

    query: str
    """The query that was actually sent, after any normalisation."""

    results: list[WebResult] = Field(default_factory=list)

    unavailable: str | None = None
    """Set when the web could not be checked at all — a rate limit, an outage, a timeout, or this
    run's search budget being spent.

    When this is set: **say plainly that you could not check the web.** Answer from your other
    sources only if they genuinely cover the question, and otherwise abstain. Do not fill the gap
    from your own knowledge, and do not present a corpus answer as though the web had confirmed
    it."""

    searches_remaining: int = 0
    """How many more web searches this run may make. At zero, reformulating will not help — work
    with what you have or abstain."""
