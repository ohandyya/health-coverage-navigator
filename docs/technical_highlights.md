# Technical highlights

The parts of this build worth presenting: where a general problem was met with a specific,
defensible mechanism rather than a prompt instruction or a hope. Each highlight has its own page in
[highlights/](highlights/), and each states the problem, the approach, why the obvious alternative
was rejected, and what evidence exists that it works.

This is a *presentation* index. The reference docs the pages point at carry the full reasoning —
[agent.md](agent.md), [chunking.md](chunking.md), [lancedb.md](lancedb.md),
[relational-tool.md](relational-tool.md), [web_search_tool.md](web_search_tool.md),
[structured-api-tools.md](structured-api-tools.md) — and [progress.md](progress.md) carries the
history.

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

## 4. [A model will call your tools wrongly — so every rejection is written as a correction](highlights/tool-retries.md)

An agent that composes its own tool calls writes unbalanced regexes, out-of-vocabulary arguments and
SQL naming columns that do not exist. Letting those fail the run throws away a working conversation
to punish a typo; swallowing them and returning nothing tells the model the corpus is empty, which
is false. Both turn a **first draft** into a wrong answer.

So every rejection raises `ModelRetry` — the text lands in the conversation and the model tries
again — and every message carries three things: what was wrong, which value caused it, and **what to
send instead**. That third clause is the one that gets skipped, and skipping it is how a retry
budget gets spent producing the same call three times. Sometimes the best message is one you did not
write: a bad column name is answered with DuckDB's own binder error, which ranks the near-misses out
of 151 columns better than any hand-written string could.

The other half is knowing where the bet is unwinnable. A spent search budget, a Tavily outage and a
missing credential are **deliberately not retries** — the model cannot act differently to fix any of
them — so they degrade with an explanation instead. *Retry what the model got wrong; degrade what
the world got wrong.*

---

## 5. [Letting a model write SQL — safely, successfully, and with every number citable](highlights/model-written-sql.md)

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

---

## 6. [Three APIs, three ways of saying "nothing" — and why an empty result is an answer](highlights/live-api-edge-cases.md)

Phase 2's rule was *an outage must never be served as an answer*. Phase 3 needs the inverse too, and
it is the harder half: ask *"has atorvastatin been recalled?"* and the honest reply is often **no** —
which the FDA delivers as an **HTTP 404**. A client written the obvious way calls that a failure, and
the agent hedges on the one question the endpoint exists to answer. Nothing throws, nothing logs, no
test goes red.

Three upstreams, three conventions for that one meaning: openFDA a **404**, NPPES a **200** with
`result_count: 0`, CMS a **400** whose text distinguishes *"that state runs its own exchange"* from a
real rejection. Two of those are codes every HTTP library treats as an error. So there is no shared
"is it empty" helper — one that was right for two of them would be wrong for the third — and instead
a very small shared base under three separately-argued mappings. `describe_status()` enforces the
rule **by omission**: it has no 404 branch, so any path that lets one reach it produces a visibly
wrong sentence rather than a plausible one.

That yields three outcomes where most agent code has two: **retry** what the model called wrongly
(`ModelRetry` naming what to send instead), **degrade** what the world broke (`unavailable` as a
first-class field, phrased as an instruction the model reads before deciding whether to answer), and
**answer** what the world genuinely says is absent — including emitting a **citable row for the
absence**, because a true finding with nothing to cite forces a false abstention or a worse source.
Measured: it once had CMS's own answer in hand, had nothing to point at, and cited a web page
instead.

---

## 7. [The system prompt is composed per configuration, because a stale sentence is an instruction](highlights/composed-prompt.md)

The agent ships in **48 shapes** — three retrieval toolsets × four independent lane booleans — and
the eval sweep runs paired arms across them deliberately. One hardcoded prompt is wrong in 47, and
the ways it is wrong escalate. It **corrupts the measurement** first: describe a tool a run does not
have and an A/B between configurations partly measures how well each copes with a misleading prompt.
Then it gets expensive, because **stale text is an instruction to abstain** — every pre-Phase-2
variant listed "anything needing current news" as out of reach, and Phase 3 measured the agent
declining *"what plans can a 40-year-old buy in ZIP 27360"* **without calling a single tool.** It had
been told it could not.

So every lane-dependent region composes from per-lane fragments — sources, search guidance, steps,
citation forms, self-description, and the abstention list, where **each landed lane removes a reason
to abstain**. Three rules came out of the failures: state capabilities **affirmatively** and generate
the absences from the same booleans (a lane described only in the negative is one the model drops
when asked what it can do — observed); **replace** superseded text rather than rebutting it (keeping
the old sentence and adding a correction produced an agent that called `drug_recalls`, got 44
recalls, and cited the web anyway — *a prompt that argues with itself is resolved by the model, not
by the author*); and let some paragraphs exist only at an **intersection**, since "which source wins,
the vendored table or the live API" cannot be asked unless both are registered.

The other half is what the prompt deliberately does *not* say. Everything a validator or a usage
limit can enforce is enforced in code, leaving only the judgement calls — and one appealing
instruction is omitted because a guardrail would have overruled it and spent the retry budget losing.
