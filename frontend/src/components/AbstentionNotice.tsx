/**
 * How an abstention renders.
 *
 * docs/frontend_plan.md §5.1: "a distinct muted panel with its own icon and no citation section.
 * It must be impossible to mistake for an answer at a glance." That is the entire specification,
 * and it is a correctness requirement rather than styling — the grounding guardrail's whole value
 * is that a user can tell *"I don't know"* from *"here is the answer"* without reading carefully.
 *
 * Note what this component does not do: it never inspects the answer text. It renders because
 * `ChatResponse.abstained` is true, which is why that field is a first-class boolean.
 *
 * The body goes through `AnswerBody` rather than being printed raw. §5.1's "no citation section"
 * meant the Phase 0 stub, whose abstention cited nothing. The real agent can abstain *and* point
 * somewhere — declining to name a provider while citing HealthCare.gov on how to check a plan's
 * directory is a better answer, not a contradiction — and printing raw text left a dead `[c1]` in
 * the prose above a citation card it did not link to. The panel is still visually distinct, which
 * is the requirement that actually matters.
 */
import { CircleSlash } from 'lucide-react'
import { AnswerBody } from '@/components/AnswerBody'

export function AbstentionNotice({
  answer,
  onCite,
}: {
  answer: string
  onCite?: (id: string) => void
}) {
  return (
    <div className="flex gap-3 rounded-lg border border-dashed border-border bg-muted/50 px-4 py-3">
      <CircleSlash className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
      <div className="space-y-1">
        <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
          Not in my reference material
        </p>
        <div className="text-muted-foreground">
          <AnswerBody answer={answer} onCite={onCite ?? (() => {})} />
        </div>
      </div>
    </div>
  )
}
