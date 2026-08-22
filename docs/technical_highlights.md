# Technical highlights

The parts of this build worth presenting: where a general problem was met with a specific,
defensible mechanism rather than a prompt instruction or a hope. Each highlight has its own page in
[highlights/](highlights/), and each states the problem, the approach, why the obvious alternative
was rejected, and what evidence exists that it works.

This is a *presentation* index. The reference docs the pages point at carry the full reasoning —
[agent.md](agent.md), [chunking.md](chunking.md), [lancedb.md](lancedb.md),
[relational-tool.md](relational-tool.md), [web_search_tool.md](web_search_tool.md) — and
[progress.md](progress.md) carries the history.

---

## 1. [Hallucinated citations are made structurally impossible, not discouraged](highlights/grounded-citations.md)

An LLM asked to cite its sources will sometimes cite a document it never read, or put real-looking
quotation marks around words the source never said — the worst failure available here, because a
fabricated citation is what makes a wrong answer *credible*. The usual mitigation is to ask nicely,
which is a preference rather than a guarantee.

Instead, the citable set is **recorded by the tools as a side effect of retrieval**, and an output
validator rejects any answer citing outside it or misquoting inside it, handing the model the reason
and letting it retry. There is no wording the model can choose that gets around this. Citations are
then rebuilt from the real evidence, so an invented title has no path to the browser.

**The web lane is where this stops being tidy and starts being necessary.** An invented `chunk_id`
is visibly internal machinery; an invented *URL* is well-formed, plausible, and checkable by nobody
— there is no artifact to compare it against, because the thing it claims to cite is the open web.
So a web citation names a positional id only a search can hand out (`web#s1.2`), never the URL,
which is then read off the recorded result. Validity stops being a property of the string and
becomes membership in a set code filled before the model spoke. The retrieved page is stored
**whole** for the same reason — unlike a chunk it cannot be re-read afterwards, so the citable set
has to *be* the evidence — and the served citation carries its **domain and date in the title**,
because for a health question who published a claim is part of it. The page also records what three
lanes' worth of extension actually cost, including the one that was not free.

`groundedness` and `citation_resolution` read **1.000 on every agent run ever recorded** — and the
reason they are measured anyway is that a number below 1.0 would be a bug in the validator, not a
score to improve.

---

## 2. [A test suite that *cannot* spend money — and the guard that had to be repaired](highlights/offline-test-suite.md)

`make check-all` never reaches a model provider: no key, no cost, no flaky afternoon. The failure
mode this defends against is unusually nasty — **a test that accidentally calls a real API does not
fail, it passes**, and you find out from a bill or never.

Phase 1a enforced it with PydanticAI's `ALLOW_MODEL_REQUESTS = False`. Phase 1b added an embedder
calling the OpenAI SDK directly and **silently punched a hole straight through it**: a safety flag
borrowed from a library only covers that library's surface area. The fix is a suite-wide guard that
refuses the live factory, a deterministic fake at the seam we own, and a production-code change —
because `from x import y` freezes a reference where `import x` keeps a seam, and that difference
decided whether the guard bound at all.

**Phase 2 re-opened the same hole, exactly as the write-up predicted it would** — a Tavily search
leaves through `httpx` and consults neither existing guard. It cost a paragraph rather than a
debugging session, because the shape had already been paid for and written down. Three guards now,
one per dependency that can open a socket.

**373 tests, no API key, no network, about twelve seconds.**

---

## 3. ["Missing" and "wrong" are different failures, and get opposite treatment](highlights/missing-vs-stale.md)

A derived artifact — the vector store, the plan-data mirrors — can be *missing* or *stale*, and
collapsing those into one error type costs you exactly when it matters. A missing store is visibly
unbuilt; a stale one **answers everything, plausibly**, with citations resolving to text that no
longer exists.

So there are two exception types, a committed manifest that makes staleness expressible at all, and
a policy chosen per call site by what wrongness would cost *there*: the API degrades on missing and
**refuses to boot** on stale; the request path 503s naming the command rather than quietly
downgrading to a lane nobody chose; the eval runner treats both as fatal, because a plausible score
for the wrong configuration is the one failure that corrupts the measurement.

The rule underneath: **classify failures by detectability, not severity.**

---

## 4. [Letting a model write SQL — safely, successfully, and with every number citable](highlights/model-written-sql.md)

*"What is a deductible"* is a reference question; *"what is the deductible on plan 38344AK1060002"*
is a **row**, and retrieval answers it with a passage that sounds right and cannot know the number.
So the agent gets 10 CMS tables — 2.96 million rows — through DuckDB reading Parquet in place, and
writes its own SQL, because a typed function per question makes the capability grow one code change
at a time.

That needs four things to hold at once: a **two-layer guard** (DuckDB's own parser accepts a single
`SELECT`; the connection is then sandboxed and locked), ceilings that make a bad query cheap, a set
of mechanisms that make the model's SQL *succeed* — `describe_table` reporting values rather than
types, per-table correctness notes, and DuckDB's binder errors passed back verbatim as retries — and
a citation shape for a row, validated **byte-exact** so `'$4,500 '` keeps the trailing space the
publisher gave it.

Latest run: `structured_exact_match` **1.000**, `routing_correct` **1.000**, and recall on the
reference questions unchanged against a paired `--no-structured` run.
