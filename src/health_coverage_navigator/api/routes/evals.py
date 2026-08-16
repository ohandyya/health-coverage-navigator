"""The gold set, and eval runs triggered from the browser.

This settles docs/frontend_plan.md §10.2, which was marked "decide at F0": **runs are triggerable
over HTTP.** `POST /api/evals/runs` starts a real background job and a companion `/stream` endpoint
follows its progress, reusing §4.5's SSE machinery exactly as that section anticipated.

Progress events are **buffered and replayed**, not pushed to a live subscriber. That is not
premature generality: the Phase 0 answerer is canned and finishes all 35 questions in under a
millisecond, so a push-only stream would routinely finish before the browser finished opening it,
and the dashboard would show an empty run that had in fact completed. Replay-then-follow makes the
client's connection timing irrelevant, which stays true when Phase 1a makes a run slow.

The wire models for the gold set are defined here rather than in `api/models.py` because they
reference `GoldQuestion`, and `api/models.py` must not import `evals` — see that module's
docstring. FastAPI emits them into `components.schemas` under their class names either way, so
codegen is unaffected.
"""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from starlette.responses import StreamingResponse

from health_coverage_navigator.api.deps import AppContext, get_context
from health_coverage_navigator.api.models import (
    ErrorResponse,
    EvalRun,
    EvalRunEventEnvelope,
    EvalRunFailed,
    EvalRunFinished,
    EvalRunProgress,
    EvalRunStarted,
    EvalRunSummary,
)
from health_coverage_navigator.config import get_config
from health_coverage_navigator.evals.answerers import AnswerFn, agent_answerer, stub_answerer
from health_coverage_navigator.evals.grading import (
    Grader,
    groundedness_grader,
    key_fact_coverage_grader,
)
from health_coverage_navigator.evals.models import GoldQuestion
from health_coverage_navigator.evals.runner import (
    list_runs,
    load_run,
    new_run_id,
    run_gold_set,
    write_run,
)

router = APIRouter()

EvalEvent = EvalRunStarted | EvalRunProgress | EvalRunFinished | EvalRunFailed


@dataclass(frozen=True, slots=True)
class _RunConfig:
    """What a browser-triggered run measures, and what the record pins it to."""

    answer_fn: AnswerFn
    graders: list[Grader]
    model: str | None = None
    toolset: str | None = None
    vectors_snapshot_id: str | None = None


def _run_config(ctx: AppContext) -> _RunConfig:
    """Which answerer a browser-triggered run measures, and how it is graded.

    Mirrors `evals/runner.py`'s CLI, minus the retrieval-only runners: those are tuning tools for
    whoever is changing `bm25_b` or comparing embeddings, not something the dashboard offers, and
    adding them would put runs that cannot abstain next to runs that can with nothing on screen to
    explain the gap. The `runner` label the record carries still distinguishes them, so a CLI
    `bm25` run and a browser `agent` run sit in the same table honestly.

    **`--toolset` is likewise not exposed.** The dashboard runs whatever `config.yaml` configures
    and records which that was, so a browser run is comparable to a CLI one; choosing a toolset per
    click would be a request body for a decision made three times from the command line, and the UI
    tracks the phases rather than leading them.

    The **LLM judge is deliberately not reachable over HTTP.** A button that quietly spends money
    on every click is the wrong affordance; `make eval-judge` is an explicit act.
    """
    if ctx.stub or ctx.index is None:
        return _RunConfig(stub_answerer(), [key_fact_coverage_grader()])
    toolset = get_config().agent.toolset
    return _RunConfig(
        agent_answerer(ctx.index, ctx.vectors, toolset),
        [groundedness_grader(ctx.index), key_fact_coverage_grader()],
        model=get_config().agent.model,
        toolset=toolset,
        vectors_snapshot_id=None if ctx.vectors is None else ctx.vectors.snapshot_id,
    )


class EvalQuestionsResponse(BaseModel):
    count: int
    questions: list[GoldQuestion]
    """The authoring model, reused verbatim rather than projected into an API-only shape: the
    dashboard's "click a failed question and see it against the expected answer" needs
    `expected_answer`, `expected_snippet` and `notes` — essentially the whole thing."""


class RunState:
    """One in-flight or finished run's event log.

    Held in a module-level dict, which means run history across a restart lives in
    `data/eval_runs/` and only the *streams* are ephemeral. That matches the rest of the app:
    conversations are in-memory too (docs/frontend_plan.md §9).
    """

    def __init__(self, run_id: str, total: int) -> None:
        self.run_id = run_id
        self.events: list[EvalEvent] = [EvalRunStarted(run_id=run_id, total=total)]
        self.updated = asyncio.Event()
        self.done = False

    def emit(self, event: EvalEvent) -> None:
        self.events.append(event)
        if isinstance(event, EvalRunFinished | EvalRunFailed):
            self.done = True
        self.updated.set()
        self.updated.clear()


_RUNS: dict[str, RunState] = {}


@router.get(
    "/evals/questions",
    response_model=EvalQuestionsResponse,
    summary="The gold eval set",
)
def get_questions(ctx: Annotated[AppContext, Depends(get_context)]) -> EvalQuestionsResponse:
    return EvalQuestionsResponse(count=len(ctx.gold.questions), questions=ctx.gold.questions)


@router.get("/evals/runs", response_model=list[EvalRunSummary], summary="Past eval runs")
def get_runs() -> list[EvalRunSummary]:
    return list_runs()


@router.get(
    "/evals/runs/{run_id}",
    response_model=EvalRun,
    responses={404: {"model": ErrorResponse}},
    summary="Per-question results for one run",
)
def get_run(run_id: str) -> EvalRun:
    run = load_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"unknown eval run {run_id!r}")
    return run


@router.post(
    "/evals/runs",
    response_model=EvalRunStarted,
    status_code=202,
    summary="Start an eval run over the gold set",
)
async def post_run(ctx: Annotated[AppContext, Depends(get_context)]) -> EvalRunStarted:
    """Kick off a run and return immediately with its id.

    202, not 200: the run has been accepted, not completed. The client then opens
    `/evals/runs/{id}/stream` to watch it and `/evals/runs/{id}` to read the final result.
    """
    run_id = new_run_id()
    state = RunState(run_id, total=len(ctx.gold.questions))
    _RUNS[run_id] = state

    completed = 0

    def on_progress(result) -> None:
        nonlocal completed
        completed += 1
        # Called from inside the runner's task, on this same event loop, so the event is appended
        # directly. This used to need `loop.call_soon_threadsafe` because the runner was synchronous
        # and executed on a worker thread; it is a coroutine now and there is no boundary to cross.
        state.emit(
            EvalRunProgress(
                run_id=run_id,
                completed=completed,
                total=len(ctx.gold.questions),
                result=result,
            )
        )

    config = _run_config(ctx)

    async def execute() -> None:
        try:
            run = await run_gold_set(
                config.answer_fn,
                ctx.gold,
                runner="stub" if ctx.stub or ctx.index is None else "agent",
                run_id=run_id,
                on_progress=on_progress,
                graders=config.graders,
                model=config.model,
                toolset=config.toolset,
                vectors_snapshot_id=config.vectors_snapshot_id,
                # Deliberately left at the default of 1. Progress events drive a live dashboard,
                # and out-of-order arrival renders as a run that jumps around.
                max_concurrency=1,
            )
            # Still a thread: `write_run` is blocking file I/O, and the runner no longer is.
            await asyncio.to_thread(write_run, run)
            state.emit(EvalRunFinished(run=run))
        except Exception as exc:  # noqa: BLE001 - surfaced to the client, not swallowed
            state.emit(EvalRunFailed(run_id=run_id, message=f"{type(exc).__name__}: {exc}"))

    asyncio.create_task(execute())
    return state.events[0]  # type: ignore[return-value]


class EvalStreamResponse(StreamingResponse):
    media_type = "text/event-stream"


async def _replay_and_follow(state: RunState) -> AsyncIterator[str]:
    sent = 0
    while True:
        while sent < len(state.events):
            event = state.events[sent]
            sent += 1
            yield f"event: {event.type}\ndata: {event.model_dump_json()}\n\n"
        if state.done:
            return
        await state.updated.wait()


@router.get(
    "/evals/runs/{run_id}/stream",
    response_class=EvalStreamResponse,
    responses={
        200: {
            "model": EvalRunEventEnvelope,
            "description": (
                "Server-Sent Events for one run: `started`, then one `progress` per question, "
                "then `finished` (carrying the complete run) or `failed`. Events are replayed "
                "from the beginning, so connecting late loses nothing."
            ),
        },
        404: {"model": ErrorResponse},
    },
    summary="Follow a running eval",
)
async def stream_run(run_id: str) -> EvalStreamResponse:
    state = _RUNS.get(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"no active eval run {run_id!r}")
    return EvalStreamResponse(
        _replay_and_follow(state),
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
