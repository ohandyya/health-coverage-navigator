/**
 * A citation: collapsed to a one-line label, expanding to the evidence behind it (§5.1).
 *
 * **Two body shapes since Phase 1-c, and the difference is the point.** A *passage* citation
 * carries prose and drills into `GET /api/corpus/{doc_id}`. A *row* citation — the relational
 * lane, `chunk_id` and `doc_id` both null — carries the cells a query returned, and they are
 * rendered as `Column: value` lines rather than as a paragraph. A row shown as prose hides which
 * columns were read, and which columns were read is the part that makes it evidence.
 *
 * Link handling is unchanged and already covered both cases: a source with an external `url` links
 * out, a corpus source drills in, and a citation with neither must not render a dead control.
 */
import { ChevronRight, ExternalLink, FileText, Loader2 } from 'lucide-react'
import { Fragment, useState } from 'react'
import { type Citation, type CorpusDocument, getDocument } from '@/api/client'
import { SourceBadge } from '@/components/SourceBadge'
import { citationDomId, cn } from '@/lib/utils'

/**
 * A citation of a queried row rather than a retrieved passage.
 *
 * Decided by the *absence* of a chunk rather than by `source_type`, deliberately: Phase 3 puts
 * live-API answers in the same `structured_api` lane, and whether those arrive as rows is not this
 * component's business. What it renders is "cells or prose", and cells are what has no chunk.
 */
function isRow(citation: Citation): boolean {
  return citation.chunk_id == null && citation.doc_id == null && citation.snippet.includes(': ')
}

/** The cited cells, one per line — the shape `runtime._row_citation` builds them in. */
function RowCells({ snippet }: { snippet: string }) {
  const rows = snippet.split('\n').map((line) => {
    const at = line.indexOf(': ')
    return at === -1 ? { column: '', value: line } : { column: line.slice(0, at), value: line.slice(at + 2) }
  })

  return (
    <dl className="grid grid-cols-[minmax(0,auto)_1fr] gap-x-3 gap-y-1 font-mono text-xs">
      {rows.map((row, index) => (
        <Fragment key={`${row.column}-${index}`}>
          <dt className="truncate text-muted-foreground">{row.column}</dt>
          {/* `whitespace-pre` because the value is the published one: '$4,500 ' keeps its trailing
              space, and a citation that renders it away stops being the evidence it claims to be. */}
          <dd className="whitespace-pre break-all">{row.value}</dd>
        </Fragment>
      ))}
    </dl>
  )
}

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
          {isRow(citation) ? (
            <RowCells snippet={citation.snippet} />
          ) : (
            <blockquote className="border-l-2 border-lane-reference/40 pl-3 text-muted-foreground italic">
              {citation.snippet}
            </blockquote>
          )}

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
