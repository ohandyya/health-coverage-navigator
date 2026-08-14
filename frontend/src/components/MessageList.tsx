/**
 * The conversation. Assembles the answer, the abstention state, and the citation list.
 *
 * The branch at the top of `AssistantTurn` is the one that matters: `abstained` selects a
 * completely different rendering, with no citation section at all. It is driven by the boolean,
 * never by the prose.
 */
import { useState } from 'react'
import type { AssistantMessage, Message } from '@/hooks/useChat'
import { AbstentionNotice } from '@/components/AbstentionNotice'
import { AnswerBody } from '@/components/AnswerBody'
import { CitationCard } from '@/components/CitationCard'
import { SourceBadge } from '@/components/SourceBadge'

function distinctLanes(message: AssistantMessage) {
  return [...new Set(message.citations.map((c) => c.source_type))]
}

function AssistantTurn({ message }: { message: AssistantMessage }) {
  const [flashed, setFlashed] = useState<string | null>(null)

  function scrollToCitation(id: string) {
    document.getElementById(`cite-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    setFlashed(id)
    window.setTimeout(() => setFlashed(null), 1400)
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <span className="text-xs font-semibold">Navigator</span>
        {distinctLanes(message).map((lane) => (
          <SourceBadge key={lane} sourceType={lane} />
        ))}
      </div>

      {message.error ? (
        <p className="rounded-lg border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive">
          {message.error}
        </p>
      ) : message.abstained ? (
        <AbstentionNotice answer={message.answer} onCite={scrollToCitation} />
      ) : message.streaming && !message.answer ? (
        // The agent searches before it writes, so there are several seconds with a trace filling
        // in and no answer yet. The caret below only appears once the first token lands, and the
        // trace panel is closed by default — without this the bubble is simply empty and the app
        // looks hung. The latest step is the honest thing to show: it is what it is doing.
        <p className="animate-pulse text-sm text-muted-foreground">
          {message.trace.at(-1)?.summary ?? 'Searching the reference corpus…'}
        </p>
      ) : (
        <>
          <AnswerBody answer={message.answer} onCite={scrollToCitation} />
          {message.streaming && (
            <span className="inline-block h-4 w-1.5 animate-pulse bg-foreground/60 align-middle" />
          )}
        </>
      )}

      {message.citations.length > 0 && (
        <section className="space-y-2 pt-1">
          <h3 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
            Sources
          </h3>
          {message.citations.map((citation) => (
            <CitationCard
              key={citation.id}
              citation={citation}
              flashed={flashed === citation.id}
            />
          ))}
        </section>
      )}

      {message.usage?.latency_ms != null && !message.streaming && (
        <p className="font-mono text-[0.65rem] text-muted-foreground">
          {message.usage.latency_ms} ms
          {message.usage.total_tokens != null && ` · ${message.usage.total_tokens} tokens`}
          {message.usage.model != null && ` · ${message.usage.model}`}
        </p>
      )}
    </div>
  )
}

export function MessageList({ messages }: { messages: Message[] }) {
  if (messages.length === 0) {
    return (
      <div className="mx-auto max-w-md space-y-2 py-16 text-center">
        <h2 className="text-sm font-medium">Ask about health coverage</h2>
        {/* Deliberately no mention of canned answers: the stub banner in `App.tsx` says so when it
            is true, driven by `health.stub`, and duplicating that claim here would leave it stale
            the moment the real agent answers — which it now does. */}
        <p className="text-sm text-muted-foreground">
          Try <em>“What is a deductible?”</em> or <em>“Does Medicare cover acupuncture?”</em>.
          Answers come from HealthCare.gov content, the Medicare handbooks, and Medicare National
          Coverage Determinations — ask something outside those and it will say so.
        </p>
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-3xl space-y-8 px-6 py-6">
      {messages.map((message) =>
        message.role === 'user' ? (
          <div key={message.id} className="space-y-1">
            <p className="text-xs font-semibold">You</p>
            <p className="text-sm">{message.text}</p>
          </div>
        ) : (
          <AssistantTurn key={message.id} message={message} />
        ),
      )}
    </div>
  )
}
