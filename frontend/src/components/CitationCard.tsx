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
 * Still decided partly by the *absence* of a chunk, which is deliberate: Phase 3 puts live-API
 * answers in the same `structured_api` lane, and whether those arrive as rows is not this
 * component's business. What it renders is "cells or prose", and cells are what has no chunk.
 *
 * **But the lane check is now load-bearing, and Phase 2 is why.** A `web` citation also has no
 * `chunk_id` and no `doc_id`, and its snippet is arbitrary prose that may well contain `": "` —
 * *"The deadline is: January 15"*. Without `source_type === 'structured_api'` it would render as a
 * table of invented columns. The original two-condition test was right for the citation shapes that
 * existed when it was written and wrong the moment a third one appeared.
 */
function isRow(citation: Citation): boolean {
  return (
    citation.source_type === 'structured_api' &&
    citation.chunk_id == null &&
    citation.doc_id == null &&
    citation.snippet.includes(': ')
  )
}

/** A line that starts a new cell: a bare identifier, then `: `. Anything else continues the value
 *  above it.
 *
 *  The naive split — one line, one cell — was correct until Phase 3, when a live-API record could
 *  carry a cell that is a **passage** rather than a value: an FDA label section is multi-line prose.
 *  Splitting that per line invented a column out of every sentence containing a colon, so
 *  `LIPITOR is indicated: • To reduce...` rendered as a column named "LIPITOR is indicated".
 *
 *  **Case-insensitive, and that is not laziness.** The first version anchored on snake_case, which
 *  is what the live lane emits — and would have broken every mirror citation, because the Exchange
 *  PUF's columns are CamelCase (`TEHBDedInnTier1Individual`). The two halves of this lane name their
 *  columns differently and both are real.
 *
 *  The remaining ambiguity is accepted rather than solved: a prose line opening with a single
 *  word and a colon (`Note: ...`) still reads as a new cell. Real column names never contain
 *  spaces, which is what the pattern actually keys on, and the cost of the residue is a
 *  mis-labelled row rather than a wrong value. */
const CELL_START = /^[A-Za-z][A-Za-z0-9_]*: /

/** The cited cells — the shape `runtime._row_citation` builds them in, one per line **unless the
 *  value is a passage**, which spans lines. */
export function parseCells(snippet: string): { column: string; value: string }[] {
  const rows: { column: string; value: string }[] = []
  for (const line of snippet.split('\n')) {
    if (CELL_START.test(line)) {
      const at = line.indexOf(': ')
      rows.push({ column: line.slice(0, at), value: line.slice(at + 2) })
    } else if (rows.length > 0) {
      rows[rows.length - 1].value += `\n${line}`
    } else {
      rows.push({ column: '', value: line })
    }
  }
  return rows
}

function RowCells({ snippet }: { snippet: string }) {
  const rows = parseCells(snippet)

  return (
    <dl className="grid grid-cols-[minmax(0,auto)_1fr] gap-x-3 gap-y-1 font-mono text-xs">
      {rows.map((row, index) => (
        <Fragment key={`${row.column}-${index}`}>
          <dt className="truncate text-muted-foreground">{row.column}</dt>
          {/* `whitespace-pre-wrap`, not `whitespace-pre`: the value is the published one, so
              '$4,500 ' keeps its trailing space and a citation that renders it away stops being the
              evidence it claims to be — but a Phase 3 cell can be a multi-line label section, and
              `pre` would send that off the side of the card rather than wrapping it. */}
          <dd className="whitespace-pre-wrap break-words">{row.value}</dd>
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
