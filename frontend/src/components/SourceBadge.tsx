/**
 * The lane badge — `reference` blue, `structured_api` green, `web` amber.
 *
 * This component is where the codegen payoff is most visible. `LANE` is a
 * `Record<SourceType, ...>`, and `SourceType` is derived from the generated schema, so adding a
 * fourth lane to the Pydantic `Literal` makes this file fail to compile until someone decides what
 * colour it is. That is docs/frontend_plan.md §4.1's promise made mechanical.
 */
import type { SourceType } from '@/api/client'
import { cn } from '@/lib/utils'

const LANE: Record<SourceType, { label: string; className: string }> = {
  reference: {
    label: 'reference',
    className: 'text-lane-reference ring-lane-reference/30 bg-lane-reference/10',
  },
  structured_api: {
    label: 'structured API',
    className: 'text-lane-structured ring-lane-structured/30 bg-lane-structured/10',
  },
  web: {
    label: 'web',
    className: 'text-lane-web ring-lane-web/30 bg-lane-web/10',
  },
}

export function SourceBadge({
  sourceType,
  className,
}: {
  sourceType: SourceType
  className?: string
}) {
  const lane = LANE[sourceType]
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset',
        lane.className,
        className,
      )}
    >
      {lane.label}
    </span>
  )
}
