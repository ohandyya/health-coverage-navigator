import { describe, expect, it } from 'vitest'

import type { EvalRun } from '@/api/client'

import { COMPARISON_AXES, comparability } from './comparability'

/**
 * Two runs' scores are only comparable if they were measured under the same everything-else, and
 * this is the check that says so. It had a hole: `web` and `live` were not in `provenance()` at
 * all, so `eval-live` against its own `eval-no-live` control — the pair the Makefile exists to
 * produce — compared as though the runs were identical, with the whole delta attributed to nothing.
 */
const run = (over: Partial<EvalRun> = {}): EvalRun =>
  ({
    id: 'r',
    created_at: '2026-08-27T00:00:00Z',
    runner: 'agent',
    toolset: 'both',
    structured: true,
    web: true,
    live: true,
    model: 'openai:test',
    config_fingerprint: 'abc123',
    chunker_snapshot_id: {},
    vectors_snapshot_id: 'vectors@1',
    n_questions: 1,
    n_passed: 1,
    metrics: {},
    results: [],
    ...over,
  }) as EvalRun

describe('comparability', () => {
  it('names every lane axis, so none can move undetected', () => {
    expect(COMPARISON_AXES).toEqual(['toolset', 'structured', 'web', 'live'])
  })

  it('sees the live lane move — the case that used to compare as identical', () => {
    const { movedAxes, confounds } = comparability(run(), run({ live: false }))

    expect(movedAxes).toEqual(['live'])
    expect(confounds).toEqual([])
  })

  it('sees the web lane move, missing since Phase 2', () => {
    expect(comparability(run(), run({ web: false })).movedAxes).toEqual(['web'])
  })

  it('reports nothing when the runs really are identical', () => {
    const { movedAxes, confounds } = comparability(run(), run())

    expect(movedAxes).toEqual([])
    expect(confounds).toEqual([])
  })

  it('warns when two axes move at once — neither question is being answered', () => {
    const { movedAxes } = comparability(run(), run({ live: false, toolset: 'lexical' }))

    expect(movedAxes).toEqual(['toolset', 'live'])
  })

  it('keeps a confound separate from an axis, because they are different sentences', () => {
    const { movedAxes, confounds } = comparability(
      run(),
      run({ live: false, model: 'openai:other' }),
    )

    expect(movedAxes).toEqual(['live'])
    expect(confounds).toEqual(['model'])
  })

  it('treats an absent flag as different from a set one', () => {
    // A run recorded before `live` existed carries `null`, and comparing it against one that had
    // the lane on is exactly the comparison a reader must be warned about.
    expect(comparability(run({ live: null }), run({ live: true })).movedAxes).toEqual(['live'])
  })
})
