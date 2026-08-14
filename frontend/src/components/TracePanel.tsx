/**
 * The agent trace — a developer surface, and the primary debugging tool for every later phase
 * (docs/frontend_plan.md §5.1).
 *
 * One detail worth stating because §5.1's mock and §4.2's schema disagree about it: the mock
 * labels a step `retrieve`, but `retrieve` is a **tool**, not a `kind` — the enum is
 * `plan | tool_call | tool_result | synthesis`. So the label is `tool ?? kind`, and the stub emits
 * both shapes so the fallback is exercised.
 *
 * Optional fields are genuinely optional: a `plan` step has no duration and no token count, and
 * the stub always emits one, so "undefined ms" cannot creep in unnoticed.
 */
import { ChevronRight, PanelRightClose } from 'lucide-react'
import { useState } from 'react'
import type { TraceStep } from '@/api/client'
import { cn } from '@/lib/utils'

const KIND_COLOR: Record<TraceStep['kind'], string> = {
  plan: 'bg-muted-foreground/50',
  tool_call: 'bg-lane-reference',
  tool_result: 'bg-lane-structured',
  synthesis: 'bg-lane-web',
}

function Step({ step }: { step: TraceStep }) {
  const [open, setOpen] = useState(false)
  const hasDetail = step.input != null

  return (
    <li className="border-b border-border/60 last:border-0">
      <button
        type="button"
        onClick={() => hasDetail && setOpen((v) => !v)}
        className={cn('flex w-full items-start gap-2 px-3 py-2 text-left', hasDetail && 'cursor-pointer')}
      >
        <span className="w-4 shrink-0 pt-1 font-mono text-[0.65rem] text-muted-foreground">
          {step.index + 1}
        </span>
        <span className={cn('mt-1.5 size-1.5 shrink-0 rounded-full', KIND_COLOR[step.kind])} />
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-1.5">
            <span className="truncate text-xs font-medium">{step.tool ?? step.kind}</span>
            {hasDetail && (
              <ChevronRight
                className={cn('size-3 text-muted-foreground transition-transform', open && 'rotate-90')}
              />
            )}
          </span>
          <span className="mt-0.5 block truncate text-[0.7rem] text-muted-foreground">
            {step.summary}
          </span>
          {(step.duration_ms != null || step.tokens != null) && (
            <span className="mt-0.5 block font-mono text-[0.65rem] text-muted-foreground">
              {[
                step.duration_ms != null ? `${step.duration_ms} ms` : null,
                step.tokens != null ? `${step.tokens} tok` : null,
              ]
                .filter(Boolean)
                .join(' · ')}
            </span>
          )}
        </span>
      </button>
      {open && step.input && (
        <pre className="overflow-x-auto bg-muted/50 px-3 py-2 text-[0.65rem] leading-relaxed">
          {JSON.stringify(step.input, null, 2)}
        </pre>
      )}
    </li>
  )
}

export function TracePanel({
  steps,
  onClose,
}: {
  steps: TraceStep[]
  onClose: () => void
}) {
  return (
    <aside className="flex w-72 shrink-0 flex-col border-l border-border bg-card">
      <header className="flex items-center justify-between border-b border-border px-3 py-2">
        <h2 className="text-xs font-semibold tracking-wide uppercase">Agent trace</h2>
        <button type="button" onClick={onClose} aria-label="Close trace panel">
          <PanelRightClose className="size-4 text-muted-foreground hover:text-foreground" />
        </button>
      </header>
      {steps.length === 0 ? (
        <p className="px-3 py-4 text-xs text-muted-foreground">
          Ask something — the steps behind the answer show up here.
        </p>
      ) : (
        <ul className="flex-1 overflow-y-auto">
          {steps.map((step) => (
            <Step key={step.index} step={step} />
          ))}
        </ul>
      )}
    </aside>
  )
}
