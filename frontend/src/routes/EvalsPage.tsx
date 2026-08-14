/**
 * The eval dashboard — docs/frontend_plan.md §5.2.
 *
 * Two things here are load-bearing rather than cosmetic:
 *
 * **Metric columns are derived from the data.** §5.2 requires the table to render "whatever
 * metrics a run reports rather than hardcoding a fixed set", because the metric vocabulary grows
 * every phase: retrieval now, groundedness at Phase 1, routing accuracy at 2/3, citation accuracy
 * at 4. So the columns come from the union of the runs' `metrics` keys.
 *
 * **The runner label is prominent.** Every Phase 0 run is produced by the canned answerer and
 * carries `runner: "stub"`. A dashboard that showed `recall@5 0.03` without saying what produced
 * it would be actively misleading, which is the same argument that makes `abstained` a boolean.
 */
import { CircleAlert, Play } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  type EvalQuestionsResponse,
  type EvalRun,
  type EvalRunEvent,
  type EvalRunSummary,
  getEvalQuestions,
  getEvalRun,
  getEvalRuns,
  startEvalRun,
  streamEvalRun,
} from '@/api/client'
import { readEvents } from '@/api/stream'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

function MetricTable({ runs, onPick }: { runs: EvalRunSummary[]; onPick: (id: string) => void }) {
  const columns = useMemo(() => {
    const names = new Set<string>()
    for (const run of runs) for (const name of Object.keys(run.metrics ?? {})) names.add(name)
    return [...names].sort()
  }, [runs])

  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full text-sm">
        <thead className="bg-muted/50 text-xs text-muted-foreground">
          <tr>
            <th className="px-3 py-2 text-left font-medium">Run</th>
            <th className="px-3 py-2 text-left font-medium">Runner</th>
            {columns.map((name) => (
              <th key={name} className="px-3 py-2 text-right font-medium">
                {name}
              </th>
            ))}
            <th className="px-3 py-2 text-right font-medium">Passed</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => (
            <tr
              key={run.id}
              onClick={() => onPick(run.id)}
              className="cursor-pointer border-t border-border hover:bg-muted/40"
            >
              <td className="px-3 py-2 font-mono text-xs">{run.id}</td>
              <td className="px-3 py-2">
                <span
                  className={cn(
                    'rounded-full px-2 py-0.5 text-xs ring-1 ring-inset',
                    run.runner === 'stub'
                      ? 'bg-lane-web/10 text-lane-web ring-lane-web/30'
                      : 'bg-lane-structured/10 text-lane-structured ring-lane-structured/30',
                  )}
                >
                  {run.runner}
                </span>
              </td>
              {columns.map((name) => (
                <td key={name} className="px-3 py-2 text-right font-mono text-xs">
                  {run.metrics?.[name] != null ? run.metrics[name].toFixed(3) : '—'}
                </td>
              ))}
              <td className="px-3 py-2 text-right font-mono text-xs">
                {run.n_passed}/{run.n_questions}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function RunDetail({ run, questions }: { run: EvalRun; questions: EvalQuestionsResponse | null }) {
  const byId = useMemo(
    () => new Map((questions?.questions ?? []).map((q) => [q.id, q])),
    [questions],
  )

  return (
    <div className="space-y-2">
      <h3 className="text-sm font-medium">
        {run.id}{' '}
        <span className="font-normal text-muted-foreground">
          · {run.duration_ms} ms · chunks{' '}
          {Object.values(run.chunker_snapshot_id ?? {}).join(', ') || 'unknown'}
        </span>
      </h3>
      <div className="max-h-96 overflow-y-auto rounded-lg border border-border">
        {run.results.map((result) => {
          const question = byId.get(result.question_id)
          return (
            <div
              key={result.question_id}
              className="flex items-start gap-3 border-b border-border/60 px-3 py-2 text-sm last:border-0"
            >
              <span className={result.passed ? 'text-lane-structured' : 'text-destructive'}>
                {result.passed ? '✓' : '✗'}
              </span>
              <span className="w-16 shrink-0 font-mono text-xs text-muted-foreground">
                {result.question_id}
              </span>
              <span className="min-w-0 flex-1 truncate">{question?.question ?? '—'}</span>
              <span className="shrink-0 text-xs text-muted-foreground">
                {result.expected_abstain
                  ? result.abstained
                    ? 'abstained'
                    : 'did not abstain'
                  : result.rank != null
                    ? `rank ${result.rank}`
                    : 'not retrieved'}
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

export function EvalsPage() {
  const [questions, setQuestions] = useState<EvalQuestionsResponse | null>(null)
  const [runs, setRuns] = useState<EvalRunSummary[]>([])
  const [selected, setSelected] = useState<EvalRun | null>(null)
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null)
  const [error, setError] = useState<string | null>(null)

  const refreshRuns = useCallback(async () => setRuns(await getEvalRuns()), [])

  useEffect(() => {
    void getEvalQuestions().then(setQuestions).catch((e) => setError(String(e)))
    void refreshRuns().catch((e) => setError(String(e)))
  }, [refreshRuns])

  async function run() {
    setError(null)
    try {
      const started = await startEvalRun()
      setProgress({ done: 0, total: started.total })
      const response = await streamEvalRun(started.run_id)
      for await (const event of readEvents<EvalRunEvent>(response)) {
        if (event.type === 'progress') setProgress({ done: event.completed, total: event.total })
        else if (event.type === 'finished') setSelected(event.run)
        else if (event.type === 'failed') setError(event.message)
      }
      await refreshRuns()
    } catch (err) {
      setError(String(err))
    } finally {
      setProgress(null)
    }
  }

  return (
    <div className="mx-auto w-full max-w-5xl space-y-8 overflow-y-auto px-6 py-6">
      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold">Eval runs</h2>
          <Button onClick={run} disabled={progress !== null}>
            <Play className="size-4" />
            {progress ? `Running ${progress.done}/${progress.total}…` : 'Run gold set'}
          </Button>
        </div>

        {error && (
          <p className="flex items-center gap-2 rounded-lg border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive">
            <CircleAlert className="size-4" /> {error}
          </p>
        )}

        {runs.length === 0 ? (
          <p className="rounded-lg border border-dashed border-border px-4 py-6 text-center text-sm text-muted-foreground">
            No runs yet. Press <strong>Run gold set</strong>, or run{' '}
            <code className="font-mono text-xs">
              uv run python -m health_coverage_navigator.evals.runner
            </code>
            .
          </p>
        ) : (
          <MetricTable
            runs={runs}
            onPick={(id) => void getEvalRun(id).then(setSelected).catch((e) => setError(String(e)))}
          />
        )}

        {selected && <RunDetail run={selected} questions={questions} />}
      </section>

      <section className="space-y-3">
        <h2 className="text-sm font-semibold">
          Gold set{' '}
          <span className="font-normal text-muted-foreground">
            · {questions?.count ?? 0} questions
          </span>
        </h2>
        <div className="overflow-x-auto rounded-lg border border-border">
          <table className="w-full text-sm">
            <thead className="bg-muted/50 text-xs text-muted-foreground">
              <tr>
                <th className="px-3 py-2 text-left font-medium">ID</th>
                <th className="px-3 py-2 text-left font-medium">Question</th>
                <th className="px-3 py-2 text-left font-medium">Corpus</th>
                <th className="px-3 py-2 text-left font-medium">Difficulty</th>
                <th className="px-3 py-2 text-left font-medium">Expected lane</th>
              </tr>
            </thead>
            <tbody>
              {(questions?.questions ?? []).map((q) => (
                <tr key={q.id} className="border-t border-border align-top">
                  <td className="px-3 py-2 font-mono text-xs">{q.id}</td>
                  <td className="px-3 py-2">{q.question}</td>
                  <td className="px-3 py-2 text-xs text-muted-foreground">{q.corpus ?? '—'}</td>
                  <td className="px-3 py-2 text-xs text-muted-foreground">
                    {q.difficulty ?? '—'}
                  </td>
                  <td className="px-3 py-2 text-xs text-muted-foreground">
                    {q.expected_abstain ? 'abstain' : (q.expected_source_type ?? '—')}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  )
}
