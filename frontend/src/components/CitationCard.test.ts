import { describe, expect, it } from 'vitest'

import { parseCells } from './CitationCard'

/** A row citation's snippet is `column: value` lines — until Phase 3, when a live-API record could
 *  carry a cell that is a passage rather than a value. These pin the difference. */
describe('parseCells', () => {
  it('splits ordinary cells one per line', () => {
    expect(parseCells('plan_id: 77264NC0010049\npremium: 344.5')).toEqual([
      { column: 'plan_id', value: '77264NC0010049' },
      { column: 'premium', value: '344.5' },
    ])
  })

  it('keeps a published value byte for byte, trailing space included', () => {
    expect(parseCells('TEHBDedInnTier1Individual: $4,500 ')).toEqual([
      { column: 'TEHBDedInnTier1Individual', value: '$4,500 ' },
    ])
  })

  it('keeps a multi-line passage in one cell', () => {
    // The Phase 3 bug: every line was read as a new cell, so a sentence containing a colon
    // invented a column out of its own first clause.
    const snippet = 'field: indications_and_usage\ntext: LIPITOR is indicated:\n• To reduce risk'
    expect(parseCells(snippet)).toEqual([
      { column: 'field', value: 'indications_and_usage' },
      { column: 'text', value: 'LIPITOR is indicated:\n• To reduce risk' },
    ])
  })

  it('does not invent a column from a prose clause containing a colon', () => {
    // The Phase 3 failure shape: mid-sentence colons after several words.
    const cells = parseCells('text: LIPITOR is indicated: to reduce risk\nin adults with CHD')
    expect(cells).toHaveLength(1)
    expect(cells[0].value).toBe('LIPITOR is indicated: to reduce risk\nin adults with CHD')
  })

  it('reads a CamelCase PUF column, which is what the mirror lane emits', () => {
    // Anchoring on snake_case would have broken every existing mirror citation.
    expect(parseCells('StandardComponentId: 21989AK0030001')).toEqual([
      { column: 'StandardComponentId', value: '21989AK0030001' },
    ])
  })
})
