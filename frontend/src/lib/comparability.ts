/**
 * When two eval runs' scores may be compared — the logic behind `EvalsPage`'s comparison panel.
 *
 * A module of its own rather than a helper inside the page, because it is pure data reasoning with
 * no React in it, and because the page would otherwise export two non-components purely so a test
 * could reach them.
 */
import type { EvalRun } from '@/api/client'

/** Everything that has to match for two scores to be comparable, as one line per run. */
function provenance(run: EvalRun): Record<string, string> {
  return {
    runner: run.runner,
    toolset: run.toolset ?? '—',
    structured: run.structured == null ? '—' : String(run.structured),
    // **`web` and `live` were missing here, and that made the panel below lie.** A field this
    // function does not return cannot be detected as differing, so `eval-live` against its own
    // `eval-no-live` control — the pair the Makefile exists to produce — compared as though the two
    // runs were measured identically, with no warning and the whole delta attributed to nothing.
    web: run.web == null ? '—' : String(run.web),
    live: run.live == null ? '—' : String(run.live),
    model: run.model ?? '—',
    config: run.config_fingerprint?.slice(0, 12) ?? '—',
    chunks: Object.values(run.chunker_snapshot_id ?? {}).join(', ') || '—',
    vectors: run.vectors_snapshot_id ?? '—',
  }
}

/** The four deliberate axes, one per phase that added a lane: Phase 1b's `toolset`, Phase 1-c's
 *  `structured`, Phase 2's `web`, Phase 3's `live`. Each has a paired Makefile target and its own
 *  control run (`eval-live` / `eval-no-live`), so differing on one is the *point* of a comparison
 *  rather than a flaw in it. */
export const COMPARISON_AXES = ['toolset', 'structured', 'web', 'live']

/**
 * Why two runs' scores may or may not be compared — **exported to be tested, not to be reused.**
 *
 * Two warnings, because two different things make a delta unattributable and they are different
 * sentences:
 *
 * - a **confound** is something outside the axes that moved — a different model, a different chunk
 *   snapshot — so the delta is partly a measurement of a question nobody asked;
 * - **more than one axis** moving is the subtler one. An axis moving is normally the point, but two
 *   runs that differ in both `toolset` and `live` are answering neither question. The old check
 *   could not say so: it looked only *outside* the axes, so any number of them could move in
 *   silence. Latent while there were two axes; widening to four is what made it worth closing.
 */
export function comparability(a: EvalRun, b: EvalRun) {
  const left = provenance(a)
  const right = provenance(b)
  const differs = (key: string) => left[key] !== right[key]
  return {
    left,
    right,
    movedAxes: COMPARISON_AXES.filter(differs),
    confounds: Object.keys(left).filter((key) => !COMPARISON_AXES.includes(key) && differs(key)),
  }
}
