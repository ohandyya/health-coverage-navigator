/**
 * Whether the trace panel is open, remembered across reloads.
 *
 * A separate module from `TracePanel.tsx` so that file exports only components — otherwise Vite's
 * fast refresh gives up on it, which is a real cost during UI work rather than a lint nicety.
 */
import { useEffect, useState } from 'react'

const STORAGE_KEY = 'hcn.trace.open'

export function useTracePanel() {
  // Closed by default: the trace is a developer surface (docs/frontend_plan.md §5.1).
  const [open, setOpen] = useState(() => localStorage.getItem(STORAGE_KEY) === 'true')
  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, String(open))
  }, [open])
  return { open, setOpen }
}
