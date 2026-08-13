"""Run the gold set against an answerer, score it, and persist the result.

The answerer is a parameter, not a hardcoded import. That is the whole design: at Phase 0 it is
`api/stub.py`, so a run measures canned answers and every run record says `runner="stub"` — the
dashboard renders that as a badge, because a metric measured against a stub must never read as a
real score. Phase 1a passes the PydanticAI agent instead and the same machinery, the same storage
format, and the same UI start reporting real numbers. Nothing else changes.

The metrics themselves are **genuinely computed**, not faked. `recall@5` and `MRR` come from
comparing the `doc_id`s a run actually cited against `expected_doc_ids`; `abstention_accuracy`
compares the `abstained` boolean against `expected_abstain`. The stub simply scores badly on the
first two and well on the third, which is the honest picture of what it is.

`expected_doc_ids` is **any-of**, matching how the gold set was authored (docs/progress.md,
2026-08-03): the three corpora are genuinely redundant, so `recall@k` counts a hit if *any* listed
doc lands in the top *k* and `MRR` uses the rank of the first hit.

Runs are written to `data/eval_runs/`, which is git-ignored: a run is a measurement of a retriever
at a moment, not a source of truth.
"""

import argparse
import json
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from health_coverage_navigator.api.models import (
    ChatRequest,
    ChatResponse,
    EvalQuestionResult,
    EvalRun,
    EvalRunSummary,
)
from health_coverage_navigator.corpus import CORPUS_NAMES, chunks_meta_path
from health_coverage_navigator.evals.loader import load_gold_set
from health_coverage_navigator.evals.models import GoldQuestion, GoldSet
from health_coverage_navigator.paths import EVAL_RUNS_DIR

#: The *k* in recall@k. Five because the chat UI shows a handful of citations and a hit ranked
#: below that is not one a reader would find.
RECALL_K = 5

AnswerFn = Callable[[GoldQuestion], ChatResponse]
ProgressFn = Callable[[EvalQuestionResult], None]


def stub_answer_fn(question: GoldQuestion) -> ChatResponse:
    """The Phase 0 answerer: the same canned responses the chat endpoint serves."""
    from health_coverage_navigator.api.stub import stub_answer

    return stub_answer(ChatRequest(message=question.question, plan_year=question.plan_year))


def score_question(question: GoldQuestion, response: ChatResponse) -> EvalQuestionResult:
    """Grade one answer.

    Two different questions are being asked depending on the gold question's shape, and conflating
    them would make the headline number meaningless: an abstention question is graded purely on
    whether the answerer abstained, and an in-corpus question on whether it retrieved a
    document the gold set named — *and* did not abstain, since abstaining on an answerable
    question is a failure that a retrieval-only score would silently reward.
    """
    retrieved = [c.doc_id for c in response.citations if c.doc_id]

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
    in_corpus = [r for r in results if not r.expected_abstain]
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
    return metrics


def chunker_snapshots() -> dict[str, str]:
    """The `snapshot_id` of each corpus's committed chunk manifest.

    Pins a score to the corpus and chunk parameters it was measured under. Without it, a recall
    number that moved between two runs is ambiguous between "the retriever changed" and "the
    chunks changed" — which is precisely the comparison Phase 1b exists to make.
    """
    snapshots: dict[str, str] = {}
    for source in CORPUS_NAMES:
        path = chunks_meta_path(source)
        if path.is_file():
            snapshots[source] = json.loads(path.read_text(encoding="utf-8"))["snapshot_id"]
    return snapshots


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


def run_gold_set(
    answer_fn: AnswerFn,
    gold: GoldSet | None = None,
    *,
    runner: str,
    run_id: str | None = None,
    on_progress: ProgressFn | None = None,
) -> EvalRun:
    """Answer every gold question, score it, and return the run.

    `on_progress` is called after each question so a caller can stream progress. It is a plain
    callback rather than a generator because the HTTP route needs to buffer events for replay,
    and a callback lets the runner stay a synchronous function usable from a CLI.
    """
    gold = gold or load_gold_set()
    run_id = run_id or new_run_id()
    started = time.perf_counter()

    results: list[EvalQuestionResult] = []
    for question in gold.questions:
        try:
            result = score_question(question, answer_fn(question))
        except Exception as exc:  # noqa: BLE001 - one bad question must not abort the run
            result = EvalQuestionResult(
                question_id=question.id,
                passed=False,
                expected_abstain=question.expected_abstain,
                error=f"{type(exc).__name__}: {exc}",
            )
        results.append(result)
        if on_progress is not None:
            on_progress(result)

    return EvalRun(
        id=run_id,
        created_at=datetime.now(UTC),
        runner=runner,
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


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the gold eval set and write the result to data/eval_runs/."
    )
    parser.add_argument(
        "--runner",
        default="stub",
        help="label recorded on the run (default: stub, the Phase 0 canned answerer)",
    )
    parser.add_argument("--no-write", action="store_true", help="print the run instead of saving")
    args = parser.parse_args()

    run = run_gold_set(stub_answer_fn, runner=args.runner)
    if args.no_write:
        print(run.model_dump_json(indent=2))
    else:
        path = write_run(run)
        print(f"Wrote {path}", file=sys.stderr)

    print(f"\n{run.id}  runner={run.runner}  {run.n_passed}/{run.n_questions} passed")
    for name, value in sorted(run.metrics.items()):
        print(f"  {name:<24} {value:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
