"""The LLM judge behind `make eval-judge` — the only grader that costs money.

Phase 1a's eval slice is *answer correctness and groundedness*. Groundedness is deterministic and
lives in `grading.py`: a quotation is either in the chunk or it is not. Correctness is not, and
nothing cheap approximates it — `key_fact_coverage` scores an answer and its exact negation the
same, because both contain the same words.

So the judge exists, and three things keep it honest:

**It grades against the gold set's `answer_key_facts`, one fact at a time.** Those were authored
with the questions (docs/progress.md, 2026-08-03) precisely so correctness would be gradeable as a
checklist rather than as a vibe. "Is this answer good?" is a question two judges answer
differently; "does this answer state that premiums are not counted toward the out-of-pocket limit?"
is not.

**It never sees the corpus, and it is not asked whether the answer is *true*.** It is asked whether
the answer *says* each thing the gold set says it should, and separately whether it contradicts
one. Truth against the source is groundedness's job, and it is checked deterministically.

**It is a different model from the one it grades** (`config.yaml`'s `evals.judge_model` versus
`agent.model`). A judge marking its own homework agrees with itself most confidently exactly where
both are wrong.

`--judge` is opt-in and unreachable over HTTP. `make eval` stays free, fast, and reproducible from
the repo, which is the same argument `config.py` makes for keeping tunables out of the environment.
"""

from functools import lru_cache

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from health_coverage_navigator.api.models import ChatResponse
from health_coverage_navigator.config import get_config
from health_coverage_navigator.evals.grading import Grader
from health_coverage_navigator.evals.models import GoldQuestion

JUDGE_PROMPT = """\
You are grading one answer produced by a health-coverage assistant against a checklist of facts a
correct answer must contain. You are not the assistant and you are not answering the question.

You will be given the question, the assistant's answer, and the checklist.

For each checklist item, decide whether the answer **states** it. Judge meaning, not wording — the
answer may phrase a fact very differently, and that still counts. What does not count: mentioning
the topic without asserting the fact, hedging it into meaninglessness, or stating its opposite.

Separately, say whether the answer asserts anything that **contradicts** a checklist item. An
answer can cover every item and still contradict one elsewhere; that is worth knowing about
because a contradiction is a worse failure than an omission.

Ignore the answer's citation markers like `[c1]`, its tone, its length, and its formatting. You are
not checking whether the answer is well sourced — that is measured separately and deterministically.
"""


class FactVerdict(BaseModel):
    fact: str
    """The checklist item, copied back so a run file is readable without cross-referencing."""

    supported: bool
    note: str = ""
    """One short clause on why not, when `supported` is false. Empty otherwise."""


class JudgeVerdict(BaseModel):
    facts: list[FactVerdict] = Field(default_factory=list)
    contradicts: bool = False
    contradiction: str = ""


@lru_cache(maxsize=1)
def build_judge() -> Agent[None, JudgeVerdict]:
    from health_coverage_navigator.agent.runtime import _resolve_model

    return Agent(
        _resolve_model(get_config().evals.judge_model),  # type: ignore[arg-type]
        output_type=JudgeVerdict,
        instructions=JUDGE_PROMPT,
    )


def _prompt(question: GoldQuestion, response: ChatResponse) -> str:
    facts = "\n".join(f"- {fact}" for fact in question.answer_key_facts)
    return f"QUESTION\n{question.question}\n\nANSWER\n{response.answer}\n\nCHECKLIST\n{facts}\n"


def judge_grader() -> Grader:
    """Two metrics: how much of the checklist the answer covers, and whether it contradicts it.

    Abstention questions are skipped — they carry no `answer_key_facts`, and whether an abstention
    was *correct* is already measured deterministically by `abstention_accuracy`. Sending them to
    the judge would pay for an opinion the gold labels already settle.
    """
    judge = build_judge()

    async def grade(question: GoldQuestion, response: ChatResponse) -> dict[str, float]:
        if question.expected_abstain or not question.answer_key_facts:
            return {}
        # An abstention on an answerable question covers nothing, by definition. Scoring it as 0.0
        # without a model call is both cheaper and less arguable than asking a judge to read
        # "I don't have that in my reference material" against a checklist.
        if response.abstained:
            return {"answer_correctness": 0.0, "contradiction": 0.0}

        # `await`, not `asyncio.run`. The runner drives graders from inside its own loop, where
        # `asyncio.run` raises — and it raises at runtime rather than at typecheck, so this is the
        # kind of thing that would only surface on a paid `make eval-judge`.
        verdict = (await judge.run(_prompt(question, response))).output
        expected = len(question.answer_key_facts)
        # Denominator from the gold set, numerator clamped to it. A judge that returns four
        # verdicts for a three-item checklist has invented an item, and the score must not be able
        # to exceed 1.0 on the strength of something nobody asked about — a metric that can read
        # 1.33 is a metric nobody can put on a dashboard.
        supported = min(sum(1 for f in verdict.facts if f.fact and f.supported), expected)
        return {
            "answer_correctness": supported / expected,
            "contradiction": float(verdict.contradicts),
        }

    return grade
