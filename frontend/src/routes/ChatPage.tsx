/**
 * The chat page — docs/frontend_plan.md §5.1.
 *
 * The plan-year selector is here from day one and is sent on every request, per docs/plan.md's
 * cross-cutting principle: CMS keeps several plan years live at once and mixing them silently is
 * this domain's most common correctness bug. Nothing consumes it yet; that is fine, the point is
 * that it can never be forgotten later.
 */
import { PanelRightOpen, SendHorizontal } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { MessageList } from '@/components/MessageList'
import { TracePanel } from '@/components/TracePanel'
import { Button } from '@/components/ui/button'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { type AssistantMessage, useChat } from '@/hooks/useChat'
import { useTracePanel } from '@/hooks/useTracePanel'

const PLAN_YEARS = [2026, 2025, 2024]

export function ChatPage() {
  const { messages, busy, ask } = useChat()
  const { open: traceOpen, setOpen: setTraceOpen } = useTracePanel()
  const [draft, setDraft] = useState('')
  const [planYear, setPlanYear] = useState(String(PLAN_YEARS[0]))
  const bottom = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const lastAssistant = [...messages].reverse().find((m) => m.role === 'assistant') as
    | AssistantMessage
    | undefined

  function submit() {
    const text = draft.trim()
    if (!text || busy) return
    setDraft('')
    void ask(text, Number(planYear))
  }

  return (
    <div className="flex min-h-0 flex-1">
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex-1 overflow-y-auto">
          <MessageList messages={messages} />
          <div ref={bottom} />
        </div>

        <div className="border-t border-border bg-card px-6 py-3">
          <div className="mx-auto flex max-w-3xl items-end gap-2">
            <Textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  submit()
                }
              }}
              placeholder="Ask about coverage…"
              rows={1}
              className="max-h-40 min-h-10 resize-none"
            />
            <Select value={planYear} onValueChange={setPlanYear}>
              <SelectTrigger className="w-30 shrink-0" aria-label="Plan year">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {PLAN_YEARS.map((year) => (
                  <SelectItem key={year} value={String(year)}>
                    Year {year}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button onClick={submit} disabled={busy || !draft.trim()} className="shrink-0">
              <SendHorizontal className="size-4" />
            </Button>
            {!traceOpen && (
              <Button
                variant="outline"
                onClick={() => setTraceOpen(true)}
                aria-label="Open trace panel"
                className="shrink-0"
              >
                <PanelRightOpen className="size-4" />
              </Button>
            )}
          </div>
        </div>
      </div>

      {traceOpen && (
        <TracePanel steps={lastAssistant?.trace ?? []} onClose={() => setTraceOpen(false)} />
      )}
    </div>
  )
}
