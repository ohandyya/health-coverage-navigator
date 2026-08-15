/**
 * Renders answer markdown, turning `[c1]` markers into links to their citation card.
 *
 * Two guardrails, both from docs/frontend_plan.md §8:
 *
 * **Raw HTML is not rendered.** react-markdown does not enable HTML unless `rehype-raw` is added,
 * so the guardrail here is a plugin we deliberately never install — not a sanitiser we have to
 * keep correct. `allowedElements` narrows it further to the subset §5.1 names.
 *
 * **Markers are rewritten before rendering, not after.** Turning `[c1]` into a real markdown link
 * means the anchor goes through the same restricted renderer as everything else, rather than being
 * injected into already-rendered output. Known limitation: the rewrite is a regex and would also
 * fire inside a code fence. Answers do not contain code fences, and the alternative — a remark AST
 * plugin — is more machinery than the risk justifies at Phase 0.
 */
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

const ALLOWED = [
  'p',
  'strong',
  'em',
  'a',
  'ul',
  'ol',
  'li',
  'h1',
  'h2',
  'h3',
  'h4',
  'code',
  'pre',
  'table',
  'thead',
  'tbody',
  'tr',
  'th',
  'td',
  'blockquote',
  'br',
  'hr',
]

const MARKER = /\[(c\d+)\]/g

export function AnswerBody({ answer, onCite }: { answer: string; onCite: (id: string) => void }) {
  // `#cite-<id>` here is a **private sentinel, not a DOM anchor** — it exists only to survive the
  // trip through the markdown parser so the `a` renderer below can recognise a citation marker and
  // swap it for a button. It is never navigated to and never matches an element: the real card ids
  // are `citationDomId(messageId, citationId)`, which carry the message id this component does not
  // know. The bare `c1` is what reaches `onCite`, and the caller scopes it.
  const withLinks = answer.replace(MARKER, (_, id: string) => `[[${id}]](#cite-${id})`)

  return (
    <div className="prose-sm max-w-none space-y-3 leading-relaxed [&_li]:ml-4 [&_li]:list-disc [&_p]:my-0 [&_table]:w-full [&_td]:border [&_td]:px-2 [&_td]:py-1 [&_th]:border [&_th]:px-2 [&_th]:py-1">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        allowedElements={ALLOWED}
        unwrapDisallowed
        components={{
          a({ href, children, ...rest }) {
            if (href?.startsWith('#cite-')) {
              const id = href.slice('#cite-'.length)
              return (
                <button
                  type="button"
                  onClick={() => onCite(id)}
                  className="mx-0.5 cursor-pointer rounded bg-lane-reference/10 px-1 align-baseline text-[0.7rem] font-medium text-lane-reference ring-1 ring-inset ring-lane-reference/25 hover:bg-lane-reference/20"
                >
                  {children}
                </button>
              )
            }
            return (
              <a href={href} target="_blank" rel="noreferrer" className="underline" {...rest}>
                {children}
              </a>
            )
          },
        }}
      >
        {withLinks}
      </ReactMarkdown>
    </div>
  )
}
