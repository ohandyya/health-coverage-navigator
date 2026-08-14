/**
 * The shell: header nav, the two routes, and the footer disclaimer.
 *
 * The stub banner is not decoration. Every answer this build produces is canned, and
 * docs/frontend_plan.md §8 draws a hard line around not letting the UI present something as more
 * than it is. `GET /api/health` reports `stub`, and the banner disappears on its own in Phase 1a
 * when the real agent sets it false — nobody has to remember to delete it.
 */
import { FlaskConical } from 'lucide-react'
import { useEffect, useState } from 'react'
import { NavLink, Navigate, Route, Routes } from 'react-router-dom'
import { type HealthResponse, getHealth } from '@/api/client'
import { ChatPage } from '@/routes/ChatPage'
import { EvalsPage } from '@/routes/EvalsPage'
import { cn } from '@/lib/utils'

function Tab({ to, children }: { to: string; children: React.ReactNode }) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        cn(
          'rounded-md px-3 py-1 text-sm',
          isActive ? 'bg-muted font-medium' : 'text-muted-foreground hover:text-foreground',
        )
      }
    >
      {children}
    </NavLink>
  )
}

export function App() {
  const [health, setHealth] = useState<HealthResponse | null>(null)

  useEffect(() => {
    void getHealth().then(setHealth).catch(() => setHealth(null))
  }, [])

  return (
    <div className="flex h-full flex-col">
      <header className="flex shrink-0 items-center gap-4 border-b border-border bg-card px-6 py-2">
        <h1 className="text-sm font-semibold">Health Coverage Navigator</h1>
        <nav className="flex gap-1">
          <Tab to="/">Chat</Tab>
          <Tab to="/evals">Evals</Tab>
        </nav>
        {health && (
          <span className="ml-auto font-mono text-[0.65rem] text-muted-foreground">
            v{health.version}
          </span>
        )}
      </header>

      {health?.stub && (
        <div className="flex shrink-0 items-center gap-2 border-b border-lane-web/30 bg-lane-web/10 px-6 py-1.5 text-xs text-lane-web">
          <FlaskConical className="size-3.5" />
          <span>
            <strong>Stub mode.</strong> Every answer is canned — no agent and no retrieval exist
            yet. Eval runs are labelled <code className="font-mono">stub</code> for the same reason.
          </span>
        </div>
      )}

      <main className="flex min-h-0 flex-1 flex-col">
        <Routes>
          <Route path="/" element={<ChatPage />} />
          <Route path="/evals" element={<EvalsPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>

      <footer className="shrink-0 border-t border-border px-6 py-2 text-[0.65rem] text-muted-foreground">
        Cited public reference material only. Not medical, legal, or enrollment advice — check
        HealthCare.gov or 1-800-MEDICARE before acting on anything here.
      </footer>
    </div>
  )
}
