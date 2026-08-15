/**
 * `citationDomId` exists because of a real bug, so it gets a real test.
 *
 * Every answer numbers its own sources from `c1`, so a four-turn conversation had four elements
 * carrying `id="cite-c1"`. `getElementById` returns the first match in document order, which meant
 * clicking `[c1]` in the newest answer scrolled to the *oldest* answer's first source — reliably
 * wrong, and only in a conversation with more than one answer, which is why it survived the first
 * pass through the UI.
 *
 * No DOM here on purpose: the invariant is about the *string*, and asserting it as a pure function
 * keeps this suite in vitest's node environment. Adding jsdom and a component-testing library to
 * cover one string would be a large dependency for a small property.
 */
import { describe, expect, it } from 'vitest'
import { citationDomId } from '@/lib/utils'

describe('citationDomId', () => {
  it('distinguishes the same citation number across different answers', () => {
    // The bug, stated directly: `c1` in two answers must not collide.
    expect(citationDomId('msg_a', 'c1')).not.toBe(citationDomId('msg_b', 'c1'))
  })

  it('distinguishes different citations within one answer', () => {
    expect(citationDomId('msg_a', 'c1')).not.toBe(citationDomId('msg_a', 'c2'))
  })

  it('is deterministic, so the anchor and the scroll target always agree', () => {
    // The card renders its id on mount; the marker computes one on click, potentially much later.
    // If this were not a pure function of its arguments, the two would silently stop matching.
    expect(citationDomId('msg_a', 'c1')).toBe(citationDomId('msg_a', 'c1'))
  })

  it('produces ids unique across a whole conversation', () => {
    const messages = ['msg_a', 'msg_b', 'msg_c', 'msg_d']
    const citations = ['c1', 'c2', 'c3', 'c4']
    const ids = messages.flatMap((m) => citations.map((c) => citationDomId(m, c)))

    expect(new Set(ids).size).toBe(ids.length)
  })
})
