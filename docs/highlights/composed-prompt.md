# The system prompt is composed per configuration, because a stale sentence is an instruction

One of the [technical highlights](../technical_highlights.md).

## The problem

This agent ships in more than one configuration. The retrieval toolset is a per-run flag
(`lexical` / `vector` / `both`), and each later lane is an independent boolean: the relational
tables, web search, the live APIs, and the Marketplace subset that needs a credential. That is
**3 × 2⁴ = 48 shapes** the same code can take, and every one of them is a shape somebody actually
runs — the eval sweep runs paired arms (`--no-web`, `--no-live`) precisely to measure what each lane
costs.

A single hardcoded prompt is wrong in every configuration but one. And the ways it is wrong get
progressively more expensive:

**1. It corrupts the measurement.** The original prompt asserted that search matches "on words, not
meaning" and named `grep_corpus`. Both are false in a vector-only run. The moment the prompt
describes tools the agent does not have, an A/B between toolsets stops measuring the toolsets and
starts partly measuring *how well each configuration copes with a misleading prompt*. The headline
number quietly becomes a number about something else.

**2. Stale text becomes an instruction to abstain.** This is the dangerous direction, and it is not
hypothetical. Every pre-Phase-2 variant listed *"anything needing current news"* as a reason to
abstain. Left in place with web search registered, that sentence declines exactly the questions the
lane was added to answer. Phase 3 hit the same wall harder: the abstention list said plan years the
tables do not hold are out of reach, and — measured — asked *"what plans can a 40-year-old buy in
ZIP 27360"*, the agent **abstained without calling a single tool.** It had been told it could not,
and it believed it.

**3. A lane described only in the negative disappears.** Asked *"what kinds of question can you
answer?"*, a three-lane run described the corpus and the tables and **never mentioned the web** —
because every other reference to that lane was a hedge ("the last place to look"). A capability the
model never mentions is one the reader never asks for.

**4. A prompt that contradicts itself is resolved by the model, not by you.** The first attempt at
Phase 3 *kept* the Phase 2 sentence naming "a recent recall" as a reason to search the web, and
*added* a step saying the web is the wrong source for recalls. Measured: the agent called
`drug_recalls`, got 44 recalls back, searched the web anyway, and cited the web.

The naive fix — maintain one prompt per combination — is 48 documents that drift the moment anyone
edits a sentence.

## The approach: one copy of each sentence, assembled per run

`system_prompt(toolset, structured, web, live, marketplace)` takes the same booleans that select the
tools, and builds the instructions from per-lane fragments. **Every lane-dependent region of the
prompt composes** — not just the tool list:

| Region | Varies by | Why it must |
|---|---|---|
| `_sources` | all four | describes what is held, and closes with an accurate list of what is not |
| `_SEARCH_STEPS` | toolset | `search_corpus` and `vector_search` fail in opposite ways |
| the numbered steps | all four | routing guidance only makes sense for lanes that exist |
| `_citation_forms` | all four | a run told about `row_id` it cannot produce burns its retry budget discovering that |
| `_self_description` | all four | the answer to "what can you do" is generated, not trusted |
| `_out_of_reach` | all four | **each landed lane removes a reason to abstain** |

Four properties make it work.

### 1. Capabilities are stated affirmatively, and absences are generated

Each lane owns one positive paragraph. The absences are then *derived from the same booleans* rather
than written by hand:

```python
missing = []
if not structured: missing.append(_MISSING_PLANS)
if not web:        missing.append(_MISSING_WEB)
if not live:       missing.append(_MISSING_LIVE)
if not marketplace: missing.append(_MISSING_MARKETPLACE)
missing.append("no provider directories or networks, and no user account.")
```

So "you have no access to anything else" can never survive a lane landing. The affirmative-first
rule is deliberate and comes from failure #3 above:

> Lead sentence is affirmative on purpose. Every *other* mention of this lane is a hedge […] But a
> lane described only in the negative is one the model drops when asked what it can do, which is
> what happened.

`_self_description` closes the same loop from the other side — it builds the lane list from the
booleans and appends *"Give the last of those the same weight as the first"*, because the lane named
last is the one that gets dropped.

### 2. Superseded text is **replaced**, never rebutted

This is the sharpest lesson in the file. Rather than adding a correction beside the Phase 2 web
step, there are two mutually exclusive versions of that step and exactly one is emitted:

```python
(_WEB_STEP_FIRST_WITH_LIVE if live else _WEB_STEP_FIRST_NO_LIVE,) + _WEB_STEPS
```

The `_WITH_LIVE` variant simply **does not contain** the recall example. As the comment puts it:

> The recall example is removed rather than contradicted, and that distinction is the whole fix […]
> A prompt that argues with itself is resolved by the model, not by the author.

### 3. Some paragraphs exist only at an *intersection*

Composition is not just per-lane on/off. Two fragments belong to neither lane alone:

```python
+ ((_WEB_STEP_LIVE_OVERLAP,) if web and live else ())
+ ((_RECONCILIATION_STEP,) if structured and live else ())
```

`_WEB_STEP_LIVE_OVERLAP` — *"the web is somebody writing about the source you can query"* — is
meaningless without both lanes. `_RECONCILIATION_STEP` answers *which source wins when the vendored
tables and the live APIs both bear on a question*, which cannot be asked unless both halves of the
structured lane exist. Neither could be owned by a single lane's fragment, and neither should appear
in a run that cannot face the conflict.

### 4. Structure is generated, not transcribed

The steps are stored as **unnumbered bodies** and numbered at render:

```python
def _number(steps: tuple[str, ...]) -> str:
    return "\n".join(f"{n}. {body}" for n, body in enumerate(steps, start=1))
```

That replaced a hand-maintained integer — `step=6 if structured else 4` — which is one magic number
per lane combination, and would have needed eight by Phase 2. Small, but the reasoning generalises:
*a mis-numbered list is a small but real signal to the model that the instructions were not written
for the tools it actually has.* Interpolated paragraphs are `textwrap.fill`-ed at render for the
same class of reason: hand-wrapping text with a lane list spliced into it leaves a ragged line in
whichever configuration it was not wrapped for.

## The other half: knowing what does not belong in a prompt

Composition solves *which* instructions to send. The module is equally opinionated about which
instructions should not exist at all, because a guardrail already covers them:

> - It does not ask the model to only cite chunks it retrieved — `seen_chunks` plus the output
>   validator make that impossible rather than requested.
> - It does not ask for verbatim snippets on trust — the validator checks each one against the chunk.
> - It does not ask the model to limit its tool calls — `UsageLimits` does.
>
> **Everything a guardrail can enforce is enforced.** What is left here is what only the model can
> do: decide which tool to reach for, when the evidence is enough, and when to say it does not know.

And there is a sharper corollary: **an instruction that fights a guardrail loses, expensively.**
`_self_description` deliberately does *not* say "no citations needed" for a question about the agent
itself — tempting, since there is nothing to cite — because `_validate_grounding` refuses a
non-abstained answer with an empty citation list. That instruction would spend the grounding-retry
budget losing an argument with code.

The prompt also lives in **its own module** rather than in an f-string inside `runtime.py`, on the
theory that *"a prompt buried in an f-string gets edited casually; one that is the entire contents of
a file gets edited on purpose."* Every fragment carries a `#:` comment recording the failure that
produced it, so the file reads as a changelog of measured mistakes rather than a wall of prose.

## Details that decide whether it actually works

- **The booleans are the same ones that select the tools.** `select_tools(toolset, structured, web,
  live, marketplace)` and `system_prompt(...)` take an identical signature and are called side by
  side in `_build_agent`, so the prompt cannot describe a toolset the agent was not given.
- **Composed, not cached.** It is a handful of string joins, and the `Agent` it feeds is already
  behind an `lru_cache` keyed on the same axes — caching the string too would be a second cache to
  keep coherent for no gain.
- **The vocabulary is derived where it can be.** `SECTIONS = get_args(LabelSectionName)` means the
  values a tool advertises and the values it accepts cannot drift.
- **Toolset guidance describes failure modes, not features.** The `both` variant does not say "you
  have two searches"; it says which one is weak on a patient's phrasing and which is weak on exact
  identifiers, and that scores are not comparable across them.

## Evidence

- **Every axis has a test asserting the prompt tracks it.**
  `test_the_prompt_describes_only_the_tools_that_exist` (parametrised over all three toolsets) and
  `test_the_prompts_differ_between_toolsets` pin Phase 1b;
  `test_the_prompt_only_promises_lanes_the_run_actually_has` pins Phase 2;
  `test_the_prompt_describes_the_lane_only_when_it_exists` and
  `test_the_prompt_claims_the_marketplace_only_when_it_is_registered` pin Phase 3's two booleans
  independently.
- **The intersection fragments are pinned as intersections.**
  `test_the_reconciliation_rule_reaches_the_model_only_with_both_halves` asserts the paragraph is
  absent unless *both* halves of the structured lane are registered — a plain per-lane test would
  pass while the fragment leaked into a mirror-only run.
- **The negative-direction failures have tests of their own.**
  `test_the_prompt_tells_the_agent_to_name_every_lane_it_holds` guards the disappearing-lane failure,
  and asserts the stale *"fixed library"* phrasing is gone;
  `test_the_prompt_says_the_corpus_can_be_stale_about_current_facts` guards the reverse.
- **Measured downstream, not just asserted structurally.** The Phase 2 arm recorded `web_search`
  called on **0 of 30** reference questions and 4 of 4 web questions with `routing_correct` 1.000;
  Phase 3's `lane_detail_correct` read **6/6**, and the `--no-live` control arm exists to check that
  six more tools cost the already-answerable questions nothing.
- **What is *not* claimed.** There is no ablation isolating the prompt composition from the tools it
  describes — no run of "Phase 3 tools with the Phase 2 prompt" as a scored arm. The evidence for
  composition is the *specific failures it fixed*, each observed in a real sweep and each recorded
  beside the fragment that fixes it (the untried-tool abstention on ZIP 27360, the web citation
  beside a correct `drug_recalls` call, the unmentioned web lane). That is a weaker claim than a
  measured delta, and it is the honest one.

## Why it presents well

The reflex when an agent misbehaves is to add a sentence to the prompt. Almost every problem here
was fixed by **removing or replacing** one instead — and the two most expensive failures were caused
by text that was *correct when written* and became an instruction to decline work, or an argument the
model got to settle. That reframes prompt maintenance as a versioning problem rather than a wording
problem: the question is not "is this sentence good?" but "under which configurations is this
sentence still **true**?"

The second thing worth pointing at is the boundary. Everything a validator, a usage limit, or a type
can enforce is enforced in code, and the prompt is left with only the judgement calls — which tool to
reach for, when the evidence is enough, when to say it does not know. Knowing which half of that line
a given instruction belongs on is most of the skill, and the giveaway is the case where an
*appealing* instruction was left out because a guardrail would have overruled it.

Full design rationale: [agent.md §3a](../agent.md) — the owning section — and [§3](../agent.md) for
the axes themselves · [web_search_tool.md §5](../web_search_tool.md) ·
[structured-api-tools.md §15](../structured-api-tools.md). The `#:` comments in
[`agent/prompt.py`](../../src/health_coverage_navigator/agent/prompt.py) carry the per-fragment
history behind each one.
