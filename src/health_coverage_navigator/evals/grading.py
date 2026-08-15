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
    """

    async def grade(_question: GoldQuestion, response: ChatResponse) -> dict[str, float]:
        if not response.citations:
            return {}

        resolved = 0
        grounded = 0
        for citation in response.citations:
            chunk = index.chunk(citation.chunk_id) if citation.chunk_id else None
            if chunk is None:
                continue
            resolved += 1
            if _normalize(citation.snippet) in _normalize(chunk.text):
                grounded += 1

        total = len(response.citations)
        return {
            "citation_resolution": resolved / total,
            "groundedness": grounded / total,
        }

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
