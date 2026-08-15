/**
 * A citation: collapsed to a one-line label, expanding to the snippet actually retrieved and a
 * way through to the full source (docs/frontend_plan.md §5.1).
 *
 * Two branches that both have to work, which is why the stub deliberately emits one citation of
 * each shape: a source with an external `url` links out, and a corpus-only source drills into
 * `GET /api/corpus/{doc_id}`. A citation with neither is still legal and must not render a dead
 * control.
 */
import { ChevronRight, ExternalLink, FileText, Loader2 } from 'lucide-react'
import { useState } from 'react'
import { type Citation, type CorpusDocument, getDocument } from '@/api/client'
import { SourceBadge } from '@/components/SourceBadge'
import { citationDomId, cn } from '@/lib/utils'

export function CitationCard({
  citation,
  messageId,
  flashed,
}: {
  citation: Citation
  /** Which answer this card belongs to. Required, because `citation.id` alone is `c1` in every
   *  answer — see `citationDomId`. */
  messageId: string
  flashed: boolean
}) {
  const [open, setOpen] = useState(false)
  const [doc, setDoc] = useState<CorpusDocument | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function loadDocument() {
    if (!citation.doc_id || doc || loading) return
    setLoading(true)
    setError(null)
    try {
      setDoc(await getDocument(citation.doc_id))
    } catch (err) {
      setError(String(err))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div
      id={citationDomId(messageId, citation.id)}
      className={cn(
        'scroll-mt-4 rounded-lg border border-border bg-card transition-colors',
        flashed && 'cite-flash',
      )}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm"
      >
        <ChevronRight
          className={cn('size-4 shrink-0 text-muted-foreground transition-transform', open && 'rotate-90')}
        />
        <span className="font-mono text-xs text-muted-foreground">[{citation.id}]</span>
        <SourceBadge sourceType={citation.source_type} />
        <span className="truncate">{citation.title}</span>
        {citation.score != null && (
          <span className="ml-auto shrink-0 font-mono text-xs text-muted-foreground">
            {citation.score.toFixed(2)}
          </span>
        )}
      </button>

      {open && (
        <div className="space-y-3 border-t border-border px-3 py-3 text-sm">
          <blockquote className="border-l-2 border-lane-reference/40 pl-3 text-muted-foreground italic">
            {citation.snippet}
          </blockquote>

          <div className="flex flex-wrap items-center gap-3 text-xs">
            {citation.url && (
              <a
                href={citation.url}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 text-lane-reference hover:underline"
              >
                <ExternalLink className="size-3" /> Open source
              </a>
            )}
            {citation.doc_id && (
              <button
                type="button"
                onClick={loadDocument}
                className="inline-flex items-center gap-1 text-lane-reference hover:underline"
              >
                {loading ? <Loader2 className="size-3 animate-spin" /> : <FileText className="size-3" />}
                {doc ? 'Full document loaded' : 'Load full document'}
              </button>
            )}
            {citation.chunk_id && (
              <span className="font-mono text-muted-foreground">{citation.chunk_id}</span>
            )}
          </div>

          {error && <p className="text-xs text-destructive">{error}</p>}

          {doc && (
            <div className="max-h-64 overflow-y-auto rounded border border-border bg-muted/40 p-3">
              <p className="mb-2 text-xs font-medium">{doc.title}</p>
              <p className="whitespace-pre-wrap text-xs leading-relaxed text-muted-foreground">
                {doc.text}
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
