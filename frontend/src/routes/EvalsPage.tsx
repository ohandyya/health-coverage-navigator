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
 * **The runner label is prominent.** A run is produced by the agent, by retrieval with no model
 * behind it (`bm25` or `vector`), or by the Phase 0 canned answerer — and they all sit in the same
 * table. A dashboard that showed `recall@5 0.57` without saying which produced it would be actively
 * misleading, the same argument that makes `abstained` a boolean. Only `agent` gets the green
 * treatment; the others are amber, because none of them is a real answer.
 *
 * **Run comparison (Phase 1b).** docs/frontend_plan.md §5.2 held this back until there was a
 * decision that needed it, and lexical-vs-vector-vs-both is that decision. The design point is that
 * a metric delta on its own is not evidence — two runs are only comparable if they were measured
 * under the same corpus, config and model — so `RunCompare` shows what differs between the runs
 * *before* it shows what differs between their scores, and says so when more than the toolset moved.
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

/** Metric names present in either run, so a metric one run does not report still gets a row. */
function metricNames(runs: Array<{ metrics?: Record<string, number> | null }>): string[] {
  const names = new Set<string>()
  for (const run of runs) for (const name of Object.keys(run.metrics ?? {})) names.add(name)
  return [...names].sort()
}

function RunnerBadge({ run }: { run: EvalRunSummary }) {
  return (
    <>
      <span
        className={cn(
          'rounded-full px-2 py-0.5 text-xs ring-1 ring-inset',
          run.runner === 'agent'
            ? 'bg-lane-structured/10 text-lane-structured ring-lane-structured/30'
            : 'bg-lane-web/10 text-lane-web ring-lane-web/30',
        )}
      >
        {run.runner}
      </span>
      {/* The Phase 1b axis. Only agent runs have one — a retrieval-only runner has no tools to
          choose between — so its absence is meaningful rather than missing data. */}
      {run.toolset && (
        <span className="ml-1 rounded-full bg-lane-reference/10 px-2 py-0.5 text-xs text-lane-reference ring-1 ring-inset ring-lane-reference/30">
          {run.toolset}
        </span>
      )}
    </>
  )
}

function MetricTable({
  runs,
  onPick,
  compared,
  onCompare,
}: {
  runs: EvalRunSummary[]
  onPick: (id: string) => void
  compared: string[]
  onCompare: (id: string) => void
}) {
  const columns = useMemo(() => metricNames(runs), [runs])

  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full text-sm">
        <thead className="bg-muted/50 text-xs text-muted-foreground">
          <tr>
            <th className="px-3 py-2 text-left font-medium" title="Pick two runs to compare">
              Cmp
            </th>
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
              <td className="px-3 py-2">
                <input
                  type="checkbox"
                  aria-label={`Compare ${run.id}`}
                  checked={compared.includes(run.id)}
                  // Stop the row's own handler: ticking a box is a different intent from opening
                  // the run, and doing both at once makes the detail panel jump while you choose.
                  onClick={(event) => event.stopPropagation()}
                  onChange={() => onCompare(run.id)}
                />
              </td>
              <td className="px-3 py-2 font-mono text-xs">{run.id}</td>
              <td className="px-3 py-2 whitespace-nowrap">
                <RunnerBadge run={run} />
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

/** Everything that has to match for two scores to be comparable, as one line per run. */
function provenance(run: EvalRun): Record<string, string> {
  return {
    runner: run.runner,
    toolset: run.toolset ?? '—',
    model: run.model ?? '—',
    config: run.config_fingerprint?.slice(0, 12) ?? '—',
    chunks: Object.values(run.chunker_snapshot_id ?? {}).join(', ') || '—',
    vectors: run.vectors_snapshot_id ?? '—',
  }
}

function RunCompare({ a, b, questions }: { a: EvalRun; b: EvalRun; questions: EvalQuestionsResponse | null }) {
  const byId = useMemo(
    () => new Map((questions?.questions ?? []).map((q) => [q.id, q])),
    [questions],
  )

  const left = provenance(a)
  const right = provenance(b)
  // `toolset` is excluded because differing on it is the *point* of a Phase 1b comparison.
  // Anything else that differs makes the delta below partly a measurement of something the
  // comparison is not asking about, which the reader has to be told rather than left to notice.
  const confounds = Object.keys(left).filter(
    (key) => key !== 'toolset' && left[key] !== right[key],
  )

  const buckets = useMemo(() => {
    const rightById = new Map(b.results.map((r) => [r.question_id, r]))
    const fixed: string[] = []
    const regressed: string[] = []
    let unchanged = 0
    for (const result of a.results) {
      const other = rightById.get(result.question_id)
      if (!other) continue
      if (result.passed === other.passed) unchanged += 1
      else if (other.passed) fixed.push(result.question_id)
      else regressed.push(result.question_id)
    }
    return { fixed, regressed, unchanged }
  }, [a.results, b.results])

  return (
    <div className="space-y-4 rounded-lg border border-border p-4">
      <h3 className="text-sm font-medium">
        Comparing <span className="font-mono text-xs">{a.id}</span> →{' '}
        <span className="font-mono text-xs">{b.id}</span>
      </h3>

      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <tbody>
            {Object.keys(left).map((key) => (
              <tr key={key} className="border-b border-border/50 last:border-0">
                <td className="py-1 pr-4 text-muted-foreground">{key}</td>
                <td className="py-1 pr-4 font-mono">{left[key]}</td>
                <td
                  className={cn(
                    'py-1 font-mono',
                    left[key] !== right[key] && 'text-lane-web',
                  )}
                >
                  {right[key]}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {confounds.length > 0 && (
        <p className="flex items-start gap-2 rounded-lg border border-lane-web/40 bg-lane-web/5 px-3 py-2 text-xs text-lane-web">
          <CircleAlert className="mt-0.5 size-4 shrink-0" />
          <span>
            These runs differ in <strong>{confounds.join(', ')}</strong> as well as the toolset, so
            the deltas below are not attributable to the toolset alone.
          </span>
        </p>
      )}

      <div className="overflow-x-auto rounded-lg border border-border">
        <table className="w-full text-sm">
          <thead className="bg-muted/50 text-xs text-muted-foreground">
            <tr>
              <th className="px-3 py-2 text-left font-medium">Metric</th>
              <th className="px-3 py-2 text-right font-medium">{a.id}</th>
              <th className="px-3 py-2 text-right font-medium">{b.id}</th>
              <th className="px-3 py-2 text-right font-medium">Δ</th>
            </tr>
          </thead>
          <tbody>
            {metricNames([a, b]).map((name) => {
              const first = a.metrics?.[name]
              const second = b.metrics?.[name]
              const delta = first != null && second != null ? second - first : null
              return (
                <tr key={name} className="border-t border-border">
                  <td className="px-3 py-2">{name}</td>
                  <td className="px-3 py-2 text-right font-mono text-xs">
                    {first?.toFixed(3) ?? '—'}
                  </td>
                  <td className="px-3 py-2 text-right font-mono text-xs">
                    {second?.toFixed(3) ?? '—'}
                  </td>
                  <td
                    className={cn(
                      'px-3 py-2 text-right font-mono text-xs',
                      delta != null && delta > 0 && 'text-lane-structured',
                      delta != null && delta < 0 && 'text-destructive',
                    )}
                  >
                    {delta == null ? '—' : `${delta > 0 ? '+' : ''}${delta.toFixed(3)}`}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {/* The half that turns "recall moved 0.03" into something actionable: *which* questions
          changed. A metric delta of zero can still hide two questions swapping places. */}
      <div className="grid gap-3 sm:grid-cols-3">
        <div className="rounded-lg border border-border px-3 py-2">
          <p className="text-xs text-muted-foreground">Fixed</p>
          <p className="font-mono text-sm text-lane-structured">{buckets.fixed.length}</p>
        </div>
        <div className="rounded-lg border border-border px-3 py-2">
          <p className="text-xs text-muted-foreground">Regressed</p>
          <p className="font-mono text-sm text-destructive">{buckets.regressed.length}</p>
        </div>
        <div className="rounded-lg border border-border px-3 py-2">
          <p className="text-xs text-muted-foreground">Unchanged</p>
          <p className="font-mono text-sm">{buckets.unchanged}</p>
        </div>
      </div>

      {(['fixed', 'regressed'] as const).map((bucket) =>
        buckets[bucket].length === 0 ? null : (
          <div key={bucket} className="space-y-1">
            <p className="text-xs font-medium capitalize text-muted-foreground">{bucket}</p>
            {buckets[bucket].map((id) => (
              <div key={id} className="flex items-start gap-3 text-sm">
                <span
                  className={cn(
                    'shrink-0',
                    bucket === 'fixed' ? 'text-lane-structured' : 'text-destructive',
                  )}
                >
                  {bucket === 'fixed' ? '✓' : '✗'}
                </span>
                <span className="w-16 shrink-0 font-mono text-xs text-muted-foreground">{id}</span>
                <span className="min-w-0 flex-1 truncate">{byId.get(id)?.question ?? '—'}</span>
              </div>
            ))}
          </div>
        ),
      )}
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
      {/* Everything after the run id answers "what was this measured under". `model` and
          `config_fingerprint` are here because the configured model is a floating alias, so the
          fingerprint proves which alias was configured and `model` says what it resolved to; the
          chunk snapshots pin the corpus. Together they are what makes two runs comparable. */}
      <h3 className="text-sm font-medium">
        {run.id}{' '}
        <span className="font-normal text-muted-foreground">
          · {run.duration_ms} ms · chunks{' '}
          {Object.values(run.chunker_snapshot_id ?? {}).join(', ') || 'unknown'}
          {run.vectors_snapshot_id && <> · {run.vectors_snapshot_id}</>}
          {run.toolset && <> · toolset {run.toolset}</>}
          {run.model && <> · {run.model}</>}
          {run.config_fingerprint && <> · config {run.config_fingerprint.slice(0, 12)}</>}
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

  // Ids rather than fetched runs: the table lists summaries, and a comparison needs the full
  // records (for per-question results), so the ids are the selection and `compared` is the fetch.
  const [compareIds, setCompareIds] = useState<string[]>([])
  const [compared, setCompared] = useState<[EvalRun, EvalRun] | null>(null)

  const refreshRuns = useCallback(async () => setRuns(await getEvalRuns()), [])

  /** Toggle a run into the comparison, keeping the two most recent picks. */
  const toggleCompare = useCallback((id: string) => {
    setCompareIds((current) =>
      current.includes(id) ? current.filter((x) => x !== id) : [...current, id].slice(-2),
    )
  }, [])

  useEffect(() => {
    if (compareIds.length !== 2) {
      setCompared(null)
      return
    }
    let cancelled = false
    void Promise.all(compareIds.map(getEvalRun))
      .then(([a, b]) => {
        if (!cancelled) setCompared([a, b])
      })
      .catch((e) => setError(String(e)))
    return () => {
      cancelled = true
    }
  }, [compareIds])

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
            No runs yet. Press <strong>Run gold set</strong> to run the agent over all 35
            questions, or <code className="font-mono text-xs">make eval-retrieval</code> for a free,
            instant run that scores retrieval alone.
          </p>
        ) : (
          <MetricTable
            runs={runs}
            onPick={(id) => void getEvalRun(id).then(setSelected).catch((e) => setError(String(e)))}
            compared={compareIds}
            onCompare={toggleCompare}
          />
        )}

        {compareIds.length === 1 && (
          <p className="text-xs text-muted-foreground">
            Tick a second run to compare — lexical vs. vector vs. both is read from this table.
          </p>
        )}

        {compared && <RunCompare a={compared[0]} b={compared[1]} questions={questions} />}

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
