"""Extra per-question metrics, layered onto the runner's built-in retrieval scoring.

`evals/runner.py` owns the metrics that come from the gold labels alone — `recall@5`, `mrr`,
`abstention_accuracy`, `false_abstention_rate`. This module owns the ones that need something
*else*: the corpus, to check a quotation is real, or a model, to check an answer is right.

The split that matters is **cost**, and it is why the graders are a list the caller composes
rather than a fixed pipeline:

- the deterministic graders here are free, need no key, and run on every `make eval`;
- the LLM judge costs a model call per question and is opt-in behind `--judge`.

That keeps `make eval` reproducible from the repo — the argument `config.py` makes for keeping
tunables out of the environment applies to a score's inputs generally.

A grader returns a `dict[str, float]` merged into `EvalQuestionResult.metrics`, and
`runner.aggregate()` averages each key over the questions that reported it. Returning `{}` means
"this question does not have that metric", which is different from scoring it zero: an abstention
carries no citations, so grading its groundedness would drag the corpus-wide average down with a
number about nothing.
"""

import collections
from collections.abc import Awaitable, Callable

from health_coverage_navigator.agent.index import CorpusIndex
from health_coverage_navigator.api.models import ChatResponse
from health_coverage_navigator.evals.models import GoldQuestion

#: What every grader looks like from the runner's side.
#:
#: Awaitable for the same reason `AnswerFn` is: `judge_grader` has to await a model, and
#: `asyncio.run` raises inside a running loop. The two deterministic graders below never await —
#: they are `async def` to fit the one that must be.
Grader = Callable[[GoldQuestion, ChatResponse], Awaitable[dict[str, float]]]


def _normalize(text: str) -> str:
    """Collapse whitespace, for a comparison that survives line wrapping.

    Same normalisation `tests/test_gold_set.py` uses on `expected_snippet`, and for the same
    reason: the corpora wrap mid-sentence, so a byte-exact substring test would fail on quotations
    that are in fact verbatim.
    """
    return " ".join(text.split()).lower()


def routing_grader() -> Grader:
    """Did the answer come from the right **kind** of source?

    Phase 1-c's new measurement, and the one this project is actually about. Answer correctness and
    routing correctness come apart in exactly the case that matters: asked for a plan's deductible,
    an agent that quotes a passage defining "deductible" can produce prose that reads as correct
    while citing something that cannot know the number. Grading only the answer would score that as
    a near miss rather than as the wrong lane.

    Scored on the **majority lane of the citations**, not on the tool trace: what the reader is
    shown is what the answer claims to rest on, and a lookup the agent ran and then ignored is not
    evidence. An abstention is not graded — declining is a different judgement, already measured by
    `abstention_accuracy`, and folding it in here would let a run that abstains on everything score
    perfectly on routing.

    Phase 2 widens this to three lanes by adding questions, not by changing this function.

    **Phase 3 is the first widening that could not be done by adding questions alone**, and the
    reason is §14a: a live-API answer and a vendored-row answer carry the *same* `source_type`, on
    purpose, because they make the same kind of claim. So `routing_correct` cannot see the split
    that the phase exists to measure — reaching a rate-limited endpoint for something the mirror
    answers offline, or trusting the mirror where only the live source is current. A second metric
    reads `tools_used` instead, and only for questions that assert one.
    """

    async def grade(question: GoldQuestion, response: ChatResponse) -> dict[str, float]:
        if question.expected_source_type is None or response.abstained:
            return {}
        scores: dict[str, float] = {}
        lanes = collections.Counter(c.source_type for c in response.citations)
        if not lanes:
            scores["routing_correct"] = 0.0
        else:
            chosen, _ = lanes.most_common(1)[0]
            scores["routing_correct"] = float(chosen == question.expected_source_type)

        if question.expected_tools:
            # Graded on the *trace* rather than on the citations, which is the opposite of the rule
            # above and deliberately so. `routing_correct` asks what the answer rests on; this asks
            # what the agent reached for — and the live-vs-mirror failure is a reaching failure. A
            # run that called the right tool and cited nothing has still routed correctly and is
            # failing something else, which `groundedness` already measures.
            used = {step.tool for step in response.trace if step.tool}
            scores["lane_detail_correct"] = float(set(question.expected_tools) <= used)
        return scores

    return grade


def groundedness_grader(index: CorpusIndex) -> Grader:
    """Is every quotation real?

    Two numbers, because two different things can be wrong and conflating them hides which:

    - `citation_resolution` — does the cited `chunk_id` exist at all? A miss here is a fabricated
      source, the worst failure this tool has.
    - `groundedness` — is the `snippet` actually in that chunk's text? A miss here is a real source
      with words put in its mouth, which reads as more trustworthy and is therefore worse.

    Both should be 1.000 on every agent run. `agent/runtime.py`'s output validator rejects either
    failure with a `ModelRetry` before the response is built, so a number below 1.0 here is a bug
    in that validator, not a score to improve. It is measured anyway precisely because a guardrail
    nobody checks is a guardrail that has already stopped working.

    **Two citation shapes since Phase 1-c, and this grader has to know the difference.** A row
    citation has no `chunk_id` by design, and the first version of this code counted that as an
    unresolvable source — which reported the relational lane working correctly as a fabrication, at
    0.889 on the first structured eval run. A metric that punishes a capability for existing is
    worse than no metric, because it reads exactly like a real regression.

    So the two numbers now cover what each shape can actually be checked against, post hoc:

    - `citation_resolution` spans **both** lanes: a passage must resolve to a chunk, and a row must
      carry the cells it claims. That is the fabrication check, and both shapes have one.
    - `groundedness` covers **passages only**, and reports `{}` when an answer has none. The
      equivalent check for a row — is every cell byte-identical to what the query returned — is
      done by `runtime._validate_row_citation` against the rows recorded in that run, which no
      grader can see afterwards. Re-deriving it here would mean parsing a rendered snippet back
      into columns and guessing which table it came from: a weaker check than the one that already
      ran, dressed up as an independent one.
    """

    async def grade(_question: GoldQuestion, response: ChatResponse) -> dict[str, float]:
        if not response.citations:
            return {}

        passages = [c for c in response.citations if c.chunk_id is not None]
        rows = [c for c in response.citations if c.chunk_id is None and c.doc_id is None]

        resolved = 0
        grounded = 0
        for citation in passages:
            chunk = index.chunk(citation.chunk_id) if citation.chunk_id else None
            if chunk is None:
                continue
            resolved += 1
            if _normalize(citation.snippet) in _normalize(chunk.text):
                grounded += 1
        resolved += sum(1 for citation in rows if citation.snippet.strip())

        metrics = {"citation_resolution": resolved / len(response.citations)}
        if passages:
            metrics["groundedness"] = grounded / len(passages)
        return metrics

    return grade


def key_fact_coverage_grader() -> Grader:
    """A crude, free proxy for answer correctness: does the answer mention each key fact's terms?

    Deliberately named *coverage* and not *correctness*. It is lexical overlap — it cannot tell a
    correct claim from its negation, and an answer that says "you do **not** pay a copayment" scores
    the same as one that says you do. It is here for two honest uses: as a smoke test that
    `make eval` output moves at all without paying for a judge, and as the thing the LLM judge's
    `answer_correctness` is compared against, so it is visible when the judge and the words
    disagree.

    Never read it as the correctness number. `--judge` produces that one.
    """

    async def grade(question: GoldQuestion, response: ChatResponse) -> dict[str, float]:
        if question.expected_abstain or not question.answer_key_facts:
            return {}
        answer = _normalize(response.answer)
        covered = 0
        for fact in question.answer_key_facts:
            terms = [t for t in _normalize(fact).split() if len(t) > 3]
            if terms and sum(t in answer for t in terms) / len(terms) >= 0.6:
                covered += 1
        return {"key_fact_coverage": covered / len(question.answer_key_facts)}

    return grade
