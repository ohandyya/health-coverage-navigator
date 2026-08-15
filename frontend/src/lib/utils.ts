import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/**
 * The DOM id of one citation card.
 *
 * **Scoped by message, and that is the entire point.** `Citation.id` is only unique *within one
 * answer* — every assistant turn numbers its own sources from `c1` — so a conversation with four
 * answers puts four elements with `id="cite-c1"` in the document. Duplicate ids are invalid HTML,
 * and `getElementById` resolves them to the first match in document order, so clicking `[c1]` in
 * the newest answer scrolled to the *oldest* answer's first source. Prefixing with the message id
 * makes it unique across the whole conversation.
 *
 * Both the card and the marker's click handler derive their id from here rather than each writing
 * its own template literal, so the target and the anchor cannot drift apart.
 */
export function citationDomId(messageId: string, citationId: string) {
  return `cite-${messageId}-${citationId}`
}
