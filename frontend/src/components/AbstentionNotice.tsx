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
 */
import { CircleSlash } from 'lucide-react'

export function AbstentionNotice({ answer }: { answer: string }) {
  return (
    <div className="flex gap-3 rounded-lg border border-dashed border-border bg-muted/50 px-4 py-3">
      <CircleSlash className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
      <div className="space-y-1">
        <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
          Not in my reference material
        </p>
        <p className="text-sm text-muted-foreground">{answer}</p>
      </div>
    </div>
  )
}
