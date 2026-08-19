"""Run the gold set against an answerer, score it, and persist the result.

The answerer is a parameter, not a hardcoded import. That is the whole design: at Phase 0 it was
`api/stub.py`, so a run measured canned answers and every run record said `runner="stub"` — the
dashboard renders that as a badge, because a metric measured against a stub must never read as a
real score. Phase 1a passes the PydanticAI agent instead and the same machinery, the same storage
format, and the same UI report real numbers. Nothing else changed; the choices live in
`evals/answerers.py`.

The metrics themselves are **genuinely computed**, not faked. `recall@5` and `MRR` come from
comparing the `doc_id`s a run actually cited against `expected_doc_ids`; `abstention_accuracy`
compares the `abstained` boolean against `expected_abstain`. The stub simply scores badly on the
first two and well on the third, which is the honest picture of what it is.

Anything needing more than the gold labels — the corpus, or a model — is a **grader**, passed in
from `evals/grading.py`. Keeping those out of this module is what lets the free ones run always and
the paid one run only behind `--judge`.

`expected_doc_ids` is **any-of**, matching how the gold set was authored (docs/progress.md,
2026-08-03): the three corpora are genuinely redundant, so `recall@k` counts a hit if *any* listed
doc lands in the top *k* and `MRR` uses the rank of the first hit.

Runs are written to `data/eval_runs/`, which is git-ignored: a run is a measurement of a retriever
at a moment, not a source of truth.
"""

import argparse
import asyncio
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from health_coverage_navigator.agent.tools import needs_vectors
from health_coverage_navigator.api.models import (
    ChatResponse,
    EvalQuestionResult,
    EvalRun,
    EvalRunSummary,
)
from health_coverage_navigator.config import Toolset, get_config
from health_coverage_navigator.corpus import chunker_snapshots
from health_coverage_navigator.evals.answerers import AnswerFn
from health_coverage_navigator.evals.grading import Grader
from health_coverage_navigator.evals.loader import load_gold_set
from health_coverage_navigator.evals.models import GoldQuestion, GoldSet
from health_coverage_navigator.paths import EVAL_RUNS_DIR
from health_coverage_navigator.structured.catalog import StructuredNotBuiltError
from health_coverage_navigator.structured.store import StructuredStore
from health_coverage_navigator.vectors import embedder as embedder_module
from health_coverage_navigator.vectors.store import (
    VectorIndex,
    VectorsNotBuiltError,
    VectorsStaleError,
)

#: The *k* in recall@k. Five because the chat UI shows a handful of citations and a hit ranked
#: below that is not one a reader would find.
RECALL_K = 5

#: Per-question metric keys `aggregate()` handles itself. Everything else a grader reports is
#: averaged generically, which is what lets a new grader appear in the dashboard without touching
#: this module, the API, or the storage format.
_INTERNAL_METRICS = frozenset({"reciprocal_rank"})

ProgressFn = Callable[[EvalQuestionResult], None]

#: Default for `--concurrency`, and the number is a measurement rather than a guess.
#:
#: **The binding constraint is tokens per minute, not connections.** One agent run costs ~6,000
#: tokens, so the 35-question gold set is ~210,000 — more than a 200k TPM allowance permits in a
#: single minute at *any* concurrency. A run therefore cannot honestly finish faster than about a
#: minute, and asking for more only converts speed into 429s.
#:
#: Measured: sequential is ~284 s and never trips the limit; `--concurrency 5` finished in 46 s and
#: put 16 of 35 questions into an ERR row, dropping recall@5 from 0.867 to 0.433. Three lands near
#: 135k tokens/minute with headroom, for roughly a 3x speedup. Raise it only alongside a real TPM
#: allowance, and read the resulting run for errors before trusting its score.
DEFAULT_CONCURRENCY = 3


def score_question(question: GoldQuestion, response: ChatResponse) -> EvalQuestionResult:
    """Grade one answer.

    Three different questions are being asked depending on the gold question's shape, and
    conflating them would make the headline number meaningless:

    * an **abstention** question is graded purely on whether the answerer abstained;
    * an **in-corpus** question on whether it retrieved a document the gold set named — *and* did
      not abstain, since abstaining on an answerable question is a failure a retrieval-only score
      would silently reward;
    * a **structured** question on whether the answer cites the exact cell values the mirror holds.
      Exact, not fuzzy: in this lane an approximate figure is a wrong figure, and a citation that
      "nearly" matches is one whose evidence does not say what the answer says.
    """
    retrieved = [c.doc_id for c in response.citations if c.doc_id]

    if question.is_structured:
        cited = "\n".join(
            c.snippet for c in response.citations if c.source_type == "structured_api"
        )
        matched = [value for value in question.expected_cells if value in cited]
        passed = len(matched) == len(question.expected_cells) and not response.abstained
        return EvalQuestionResult(
            question_id=question.id,
            passed=passed,
            expected_source_type=question.expected_source_type,
            expected_abstain=False,
            abstained=response.abstained,
            rank=None,
            retrieved_doc_ids=retrieved,
            metrics={
                "structured_exact_match": len(matched) / len(question.expected_cells)
                if question.expected_cells
                else 0.0
            },
        )

    if question.expected_abstain:
        return EvalQuestionResult(
            question_id=question.id,
            passed=response.abstained,
            expected_source_type=None,
            expected_abstain=True,
            abstained=response.abstained,
            rank=None,
            retrieved_doc_ids=retrieved,
        )

    rank: int | None = None
    for position, doc_id in enumerate(retrieved, start=1):
        if doc_id in question.expected_doc_ids:
            rank = position
            break

    passed = rank is not None and rank <= RECALL_K and not response.abstained
    return EvalQuestionResult(
        question_id=question.id,
        passed=passed,
        expected_source_type=question.expected_source_type,
        expected_abstain=False,
        abstained=response.abstained,
        rank=rank,
        retrieved_doc_ids=retrieved,
        metrics={"reciprocal_rank": 1.0 / rank if rank else 0.0},
    )


def aggregate(results: list[EvalQuestionResult]) -> dict[str, float]:
    """Headline metrics for a whole run.

    Deliberately a plain dict rather than a typed struct: docs/frontend_plan.md §5.2 requires the
    dashboard to render whatever metrics a run reports, so that groundedness (Phase 1), routing
    accuracy (Phase 2/3) and citation accuracy (Phase 4) can be added without touching the API,
    the storage format, or the table.
    """
    # Split by lane, not merely by "does it abstain". A structured question has no expected doc
    # ids, so leaving it among the retrieval-scored questions would drop recall@5 by construction —
    # a headline metric moving because the *question set* changed is exactly the confusion this
    # harness exists to prevent, and it is how a phase that added a capability could look like a
    # regression.
    in_corpus = [
        r for r in results if not r.expected_abstain and r.expected_source_type != "structured_api"
    ]
    abstentions = [r for r in results if r.expected_abstain]

    metrics: dict[str, float] = {}
    if in_corpus:
        hits = sum(1 for r in in_corpus if r.rank is not None and r.rank <= RECALL_K)
        metrics[f"recall@{RECALL_K}"] = hits / len(in_corpus)
        metrics["mrr"] = sum(r.metrics.get("reciprocal_rank", 0.0) for r in in_corpus) / len(
            in_corpus
        )
        # An answerer that abstains on an answerable question is the failure mode the grounding
        # guardrail is most likely to overshoot into, so it gets its own number rather than being
        # buried inside recall.
        metrics["false_abstention_rate"] = sum(1 for r in in_corpus if r.abstained) / len(in_corpus)
    if abstentions:
        metrics["abstention_accuracy"] = sum(1 for r in abstentions if r.passed) / len(abstentions)

    # Every other per-question metric key — whatever the graders reported — averaged over the
    # questions that reported it. Averaging over *reporters* rather than over the whole set is the
    # point: a grader returns `{}` for a question it does not apply to (groundedness of an
    # abstention that cited nothing), and counting those as zero would report a number about
    # nothing as if it were a failure.
    reported: dict[str, list[float]] = {}
    for result in results:
        for name, value in result.metrics.items():
            if name not in _INTERNAL_METRICS:
                reported.setdefault(name, []).append(value)
    for name, values in sorted(reported.items()):
        metrics[name] = sum(values) / len(values)

    return metrics


def _runs_dir(runs_dir: Path | None) -> Path:
    """Resolve the runs directory at call time, not at import time.

    Every persistence function below takes `runs_dir: Path | None = None` rather than defaulting
    directly to `EVAL_RUNS_DIR`, because a default argument binds once at definition and a test
    that monkeypatches the module constant would silently keep writing into the real
    `data/eval_runs/`. That is not hypothetical — it happened, and a test suite that writes into
    the repo's data tree is exactly what the public-repo guardrail exists to prevent.
    """
    return EVAL_RUNS_DIR if runs_dir is None else runs_dir


def new_run_id(now: datetime | None = None, runs_dir: Path | None = None) -> str:
    """`run_2026-08-13_1`, numbered within the day — the shape docs/frontend_plan.md §5.2 shows."""
    runs_dir = _runs_dir(runs_dir)
    now = now or datetime.now(UTC)
    stem = f"run_{now:%Y-%m-%d}"
    existing = len(list(runs_dir.glob(f"{stem}_*.json"))) if runs_dir.is_dir() else 0
    return f"{stem}_{existing + 1}"


async def _grade_one(
    question: GoldQuestion, answer_fn: AnswerFn, graders: Sequence[Grader]
) -> EvalQuestionResult:
    """Answer and score one question, turning any failure into a recorded result.

    Never raises, so `gather` below needs no `return_exceptions` and no second layer of error
    handling. Same rule the sequential version always had: a judge that times out on question 12
    must not throw away the other 34 measurements.
    """
    try:
        response = await answer_fn(question)
        result = score_question(question, response)
        for grader in graders:
            result.metrics.update(await grader(question, response))
    except Exception as exc:  # noqa: BLE001 - one bad question must not abort the run
        result = EvalQuestionResult(
            question_id=question.id,
            passed=False,
            expected_abstain=question.expected_abstain,
            error=f"{type(exc).__name__}: {exc}",
        )
    return result


async def _grade_all(
    questions: Sequence[GoldQuestion],
    answer_fn: AnswerFn,
    graders: Sequence[Grader],
    max_concurrency: int,
    on_progress: ProgressFn | None,
) -> list[EvalQuestionResult]:
    """Every question, at most `max_concurrency` in flight.

    A semaphore and one `gather`, with no branch for the sequential case — `max_concurrency=1`
    genuinely *is* sequential here, because `asyncio.Semaphore` hands the slot to waiters in FIFO
    order and `gather` schedules the tasks in gold-set order. That is why this is a single code
    path rather than a fast one and a safe one that have to be kept agreeing.

    Three properties hold at any concurrency:

    **Results come back in gold-set order**, because that is what `gather` returns regardless of
    completion order. The run record is identical at any setting, so two runs stay diffable.

    **`on_progress` fires in completion order**, from inside the task. That reorders the *events*
    at concurrency > 1 — the objection `evals/answerers.py` originally recorded — which is why the
    default is 1 and the dashboard leaves it there.

    **No callback needs a lock.** Everything runs on one event loop thread; a coroutine is only
    interrupted where it awaits, and `on_progress` never does.
    """
    limit = asyncio.Semaphore(max_concurrency)

    async def graded(question: GoldQuestion) -> EvalQuestionResult:
        async with limit:
            result = await _grade_one(question, answer_fn, graders)
        if on_progress is not None:
            on_progress(result)
        return result

    return list(await asyncio.gather(*(graded(question) for question in questions)))


async def run_gold_set(
    answer_fn: AnswerFn,
    gold: GoldSet | None = None,
    *,
    runner: str,
    run_id: str | None = None,
    on_progress: ProgressFn | None = None,
    graders: Sequence[Grader] = (),
    model: str | None = None,
    toolset: str | None = None,
    structured: bool | None = None,
    vectors_snapshot_id: str | None = None,
    max_concurrency: int = 1,
) -> EvalRun:
    """Answer every gold question, score it, and return the run.

    `on_progress` is called after each question so a caller can stream progress. It is a plain
    callback rather than a generator because the HTTP route needs to buffer events for replay, and
    because a callback stays callable from inside a task without the caller owning an async
    iterator.

    `max_concurrency` defaults to **1 — sequential, and progress events in gold-set order**. A run
    is almost entirely spent waiting on a model, so raising it is close to a linear speedup until
    the provider's tokens-per-minute limit binds, but it reorders progress events, and that is a
    caller's decision rather than this function's: the CLI passes `--concurrency`, the dashboard
    does not. Scores are unaffected either way.
    """
    gold = gold or load_gold_set()
    run_id = run_id or new_run_id()
    started = time.perf_counter()

    results = await _grade_all(gold.questions, answer_fn, graders, max_concurrency, on_progress)

    return EvalRun(
        id=run_id,
        created_at=datetime.now(UTC),
        runner=runner,
        # Both pin the score to what produced it. docs/progress.md asks for these specifically
        # because `config.yaml`'s model is a floating alias — OpenAI publishes no dated snapshot
        # for that family — so a rerun can differ from its baseline with nothing in the repo to
        # say why. `None` when no model was involved (a stub or retrieval-only run).
        config_fingerprint=get_config().fingerprint(),
        model=model,
        structured=structured,
        # Phase 1b's comparison is between runs that differ *only* in `toolset`, so a run that does
        # not name it cannot take part in that comparison. `vectors_snapshot_id` does the same job
        # for the embeddings that `chunker_snapshot_id` does for the chunks — a recall number that
        # moved is otherwise ambiguous between "the retriever changed" and "what it searches did".
        toolset=toolset,
        vectors_snapshot_id=vectors_snapshot_id,
        chunker_snapshot_id=chunker_snapshots(),
        n_questions=len(results),
        n_passed=sum(1 for r in results if r.passed),
        duration_ms=int((time.perf_counter() - started) * 1000),
        metrics=aggregate(results),
        results=results,
    )


# ---- persistence ----


def run_path(run_id: str, runs_dir: Path | None = None) -> Path:
    return _runs_dir(runs_dir) / f"{run_id}.json"


def write_run(run: EvalRun, runs_dir: Path | None = None) -> Path:
    runs_dir = _runs_dir(runs_dir)
    runs_dir.mkdir(parents=True, exist_ok=True)
    path = run_path(run.id, runs_dir)
    path.write_text(run.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


def load_run(run_id: str, runs_dir: Path | None = None) -> EvalRun | None:
    """Load one run, resolved by matching a discovered stem.

    Never `runs_dir / f"{run_id}.json"` from raw input — same rule as `routes/corpus.py`, for the
    same reason (docs/frontend_plan.md §8).
    """
    runs_dir = _runs_dir(runs_dir)
    if not runs_dir.is_dir():
        return None
    for path in runs_dir.glob("*.json"):
        if path.stem == run_id:
            return EvalRun.model_validate_json(path.read_text(encoding="utf-8"))
    return None


def list_runs(runs_dir: Path | None = None) -> list[EvalRunSummary]:
    """Every readable run, newest first. A malformed file is skipped rather than fatal — one bad
    run should not take the dashboard down."""
    runs_dir = _runs_dir(runs_dir)
    if not runs_dir.is_dir():
        return []
    summaries: list[EvalRunSummary] = []
    for path in runs_dir.glob("*.json"):
        try:
            run = EvalRun.model_validate_json(path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            print(f"skipping unreadable eval run {path.name}: {exc}", file=sys.stderr)
            continue
        summaries.append(EvalRunSummary.model_validate(run.model_dump(exclude={"results"})))
    return sorted(summaries, key=lambda s: s.created_at, reverse=True)


# ---- CLI ----


@dataclass(frozen=True, slots=True)
class RunPlan:
    """Everything `--runner` and `--toolset` resolve to: what to ask, who answers, how to grade.

    A dataclass rather than a longer tuple. It was already returning four values, and Phase 1b
    needs two more (`toolset`, `vectors_snapshot_id`) — at six, positional unpacking stops being
    readable and starts being a place to transpose two `str | None`s silently.
    """

    answer_fn: AnswerFn
    gold: GoldSet
    graders: list[Grader]
    model: str | None = None
    toolset: str | None = None
    structured: bool | None = None
    vectors_snapshot_id: str | None = None


async def _build(runner: str, judge: bool, toolset: str | None, structured: bool | None) -> RunPlan:
    """Resolve the flags into an answerer, the questions to ask it, and how to grade it.

    The gold *set* varies by runner, which is the non-obvious part. A retrieval-only run is asked
    only the in-corpus questions: a bare retriever always returns its top k and can never abstain,
    so scoring it on the five abstention questions would report a guaranteed zero as if it were a
    finding.

    Async since Phase 1b, because opening the vector store is — and it is opened here, once per
    run, rather than per question, for the same reason the index is.
    """
    from health_coverage_navigator.evals.answerers import (
        agent_answerer,
        bm25_answerer,
        stub_answerer,
        vector_answerer,
    )
    from health_coverage_navigator.evals.grading import (
        groundedness_grader,
        key_fact_coverage_grader,
        routing_grader,
    )

    gold = load_gold_set()
    if runner == "stub":
        if judge:
            raise SystemExit("--judge on the stub runner would only measure canned text")
        return RunPlan(stub_answerer(), gold, [key_fact_coverage_grader()])

    from health_coverage_navigator.agent.index import get_corpus_index

    index = get_corpus_index()
    graders: list[Grader] = [groundedness_grader(index), key_fact_coverage_grader()]
    in_corpus = GoldSet(questions=gold.in_corpus())

    if runner == "bm25":
        if judge:
            raise SystemExit("--judge on the bm25 runner has no answer to judge")
        return RunPlan(bm25_answerer(index), in_corpus, graders)

    if runner == "vector":
        if judge:
            raise SystemExit("--judge on the vector runner has no answer to judge")
        vectors = await _open_vectors()
        return RunPlan(
            vector_answerer(index, vectors),
            in_corpus,
            graders,
            vectors_snapshot_id=vectors.snapshot_id,
        )

    if judge:
        from health_coverage_navigator.evals.judge import judge_grader

        graders.append(judge_grader())

    resolved = cast(Toolset, toolset or get_config().agent.toolset)
    vectors = await _open_vectors() if needs_vectors(resolved) else None
    lanes = get_config().agent.structured_tools if structured is None else structured
    store = _open_structured() if lanes else None

    # Routing is only measurable once there is more than one lane to route between, so the grader
    # goes on the agent runner and only when the relational lane is registered. Phase 2 widens the
    # same metric to three lanes rather than introducing a second one.
    if store is not None:
        graders.append(routing_grader())

    return RunPlan(
        agent_answerer(index, vectors, resolved, store),
        gold if store is not None else GoldSet(questions=gold.in_corpus() + gold.abstentions()),
        graders,
        model=get_config().agent.model,
        toolset=resolved,
        structured=store is not None,
        vectors_snapshot_id=None if vectors is None else vectors.snapshot_id,
    )


def _open_structured() -> StructuredStore:
    """Open the plan-data mirror, or exit with the command that builds it.

    A hard exit rather than a degradation, matching `_open_vectors`: a run that quietly dropped the
    structured questions would report a score for a configuration nobody asked for, and the number
    would look like every other number in the table.
    """
    try:
        return StructuredStore.open()
    except StructuredNotBuiltError as exc:
        raise SystemExit(f"{exc}") from exc


async def _open_vectors() -> VectorIndex:
    """Open the embedding store, or exit with the command that builds it.

    A `SystemExit` rather than a degraded run: silently falling back to lexical would produce a
    plausible score for a configuration nobody asked for, and the run record would name the
    toolset that was *requested*. That is the one failure that would corrupt the comparison this
    phase exists to make.
    """
    config = get_config().vectors
    embedder = embedder_module.openai_embedder(config.embedding_model, config.dimensions)
    try:
        return await VectorIndex.open(embedder)
    except (VectorsNotBuiltError, VectorsStaleError) as exc:
        raise SystemExit(str(exc)) from exc


def _status(result: EvalQuestionResult) -> str:
    if result.error:
        return "ERR"
    return "PASS" if result.passed else "FAIL"


def _detail(result: EvalQuestionResult) -> str:
    """Why this question scored the way it did, in one column.

    The interesting part is *how* a question failed, which the pass/fail flag alone hides: a
    question that retrieved nothing and a question that retrieved the right document then abstained
    anyway are the same `FAIL` and completely different bugs.
    """
    if result.error:
        # Truncated to keep the column aligned; the full message is reprinted after the run.
        return (result.error[:49] + "…") if len(result.error) > 50 else result.error
    if result.expected_abstain:
        return "abstained" if result.abstained else "answered anyway"
    if result.abstained:
        return "false abstention"
    if result.rank is not None:
        return f"rank {result.rank}"
    return "not retrieved"


def _progress_printer(total: int) -> ProgressFn:
    """One line per graded question, on stderr.

    `run_gold_set` takes `on_progress` exactly so a caller can do this — the HTTP route already
    uses it to stream to the dashboard. Without it the CLI is silent for the whole run, and
    `make eval` is 35 model calls over several minutes: a command that prints nothing for that long
    is indistinguishable from one that has hung, which is the wrong thing to make someone guess
    about while they are spending money.

    **stderr, and flushed.** `--no-write` prints the run JSON to stdout, so progress on stdout would
    end up inside a piped payload. Flushing because stderr is block-buffered when it is not a tty,
    which would hold every line until the run finished and defeat the point.

    The clock is **elapsed since the run started**, not per question. Under `--concurrency` the gap
    between two completions is not how long either took, and a column that silently means something
    different depending on a flag is worse than one that means less. `_grade_all` guarantees this is
    only ever called from one thread, so the counter needs no lock.
    """
    index = 0
    started = time.perf_counter()

    def report(result: EvalQuestionResult) -> None:
        nonlocal index
        index += 1

        correctness = result.metrics.get("answer_correctness")
        suffix = f"  correctness {correctness:.2f}" if correctness is not None else ""
        print(
            f"  {index:>3}/{total}  {_status(result):<4}  {result.question_id:<22} "
            f"{_detail(result):<22} {time.perf_counter() - started:5.1f}s{suffix}",
            file=sys.stderr,
            flush=True,
        )

    return report


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the gold eval set and write the result to data/eval_runs/."
    )
    parser.add_argument(
        "--runner",
        default="agent",
        choices=("agent", "bm25", "vector", "stub"),
        help=(
            "agent: the agent itself (costs a model call per question). "
            "bm25: lexical retrieval only, no model, no key, sub-second. "
            "vector: semantic retrieval only, no model, one cheap embedding call per question. "
            "stub: the Phase 0 canned answerer, kept as the baseline. Default: agent."
        ),
    )
    parser.add_argument(
        "--toolset",
        default=None,
        choices=("lexical", "vector", "both"),
        help=(
            "which retrieval tools the agent may see (agent runner only). Phase 1b's eval axis: "
            "lexical-only / vector-only / both, recorded on the run so the three are comparable. "
            "Default: agent.toolset from config.yaml."
        ),
    )
    parser.add_argument(
        "--structured",
        default=None,
        action=argparse.BooleanOptionalAction,
        help=(
            "whether the agent may see the relational lane over the vendored plan data (agent "
            "runner only). Phase 1-c's eval axis, recorded on the run: --no-structured is what "
            "measures whether the extra lane costs anything on the reference questions. "
            "Default: agent.structured_tools from config.yaml."
        ),
    )
    parser.add_argument(
        "--judge",
        action="store_true",
        help="grade answer correctness with an LLM judge (agent runner only; costs a second "
        "model call per question)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
        metavar="N",
        help=(
            "how many questions to run at once. A run is almost all waiting on the model, so this "
            "is close to a linear speedup until the provider's tokens-per-minute limit binds — "
            "see the note in the source before raising it. 1 runs sequentially. Default: 3."
        ),
    )
    parser.add_argument("--no-write", action="store_true", help="print the run instead of saving")
    args = parser.parse_args()
    if args.concurrency < 1:
        raise SystemExit("--concurrency must be at least 1")
    if args.toolset and args.runner != "agent":
        raise SystemExit(
            f"--toolset applies to the agent runner; the {args.runner} runner has no tools to "
            f"choose between."
        )
    if args.structured is not None and args.runner != "agent":
        raise SystemExit(
            f"--structured applies to the agent runner; the {args.runner} runner has no lanes to "
            f"route between."
        )

    plan = await _build(args.runner, args.judge, args.toolset, args.structured)

    # Printed before the first call rather than after, so the cost is visible while there is still
    # time to interrupt it.
    total = len(plan.gold.questions)
    calls = 0 if plan.model is None else total * (2 if args.judge else 1)
    concurrency = args.concurrency if calls else 1  # threads buy nothing without network waits
    print(
        f"{args.runner} runner · {total} questions"
        + (f" · ~{calls} model calls" if calls else " · no model calls")
        + (f" · {concurrency} at a time" if concurrency > 1 else ""),
        file=sys.stderr,
    )
    if plan.model:
        print(f"  model    {plan.model}", file=sys.stderr)
    if plan.toolset:
        print(f"  toolset  {plan.toolset}", file=sys.stderr)
    if plan.structured is not None:
        print(
            f"  lanes    reference{' + structured' if plan.structured else ' only'}",
            file=sys.stderr,
        )
    if plan.vectors_snapshot_id:
        print(f"  vectors  {plan.vectors_snapshot_id}", file=sys.stderr)
    if args.judge:
        print(f"  judge    {get_config().evals.judge_model}", file=sys.stderr)

    run = await run_gold_set(
        plan.answer_fn,
        plan.gold,
        runner=args.runner,
        graders=plan.graders,
        model=plan.model,
        toolset=plan.toolset,
        structured=plan.structured,
        vectors_snapshot_id=plan.vectors_snapshot_id,
        on_progress=_progress_printer(total),
        max_concurrency=concurrency,
    )

    if args.no_write:
        print(run.model_dump_json(indent=2))
    else:
        path = write_run(run)
        print(f"Wrote {path}", file=sys.stderr)

    # Under `--no-write` stdout *is* the run, so the human summary has to move aside or it lands
    # after the closing brace and `... | jq` fails on trailing data. Otherwise the summary is the
    # command's real output and belongs on stdout.
    out = sys.stderr if args.no_write else sys.stdout
    print(f"\n{run.id}  runner={run.runner}  {run.n_passed}/{run.n_questions} passed", file=out)
    if run.model:
        print(f"  model                    {run.model}", file=out)
    if run.toolset:
        print(f"  toolset                  {run.toolset}", file=out)
    for name, value in sorted(run.metrics.items()):
        print(f"  {name:<24} {value:.3f}", file=out)

    errored = [r for r in run.results if r.error]
    if errored:
        print(f"\n{len(errored)} question(s) errored:", file=sys.stderr)
        for result in errored[:5]:
            print(f"  {result.question_id}: {result.error}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
