---
name: sync-frontend
description: Propagate a FastAPI contract change into the frontend — regenerate schema.d.ts, triage the diff, fix what breaks, and check the seams codegen cannot see. Use after changing Pydantic models under src/health_coverage_navigator/api/, after adding or renaming a route, or when asked to sync the frontend, regenerate types, or fix schema drift.
---

# Sync the frontend to the backend contract

The contract crosses the boundary by codegen: Pydantic models → `openapi.json` →
`frontend/src/api/schema.d.ts` → aliased in `client.ts` → consumed by hooks and components.
[docs/frontend_plan.md](../../../docs/frontend_plan.md) §4.3 owns the *design* of that mechanism and
calls it the highest-leverage choice in the document. This skill is the *procedure* for using it.

**`make types` is one line and needs no skill. You are here for the three things around it:** the
diff is the work list, codegen has blind spots that fail at runtime rather than compile time, and an
empty diff after a real backend change is a symptom rather than a success.

## 1. Regenerate, then read the diff

```bash
make types
git diff -- frontend/src/api/schema.d.ts
```

**Read the diff before touching anything else.** `dump_openapi.py` renders with
`indent=2, sort_keys=True` precisely so the dump is byte-stable — every line in that diff is a real
contract change, never dict ordering. It tells you what to look for instead of chasing compiler
errors blind.

Note what changed: added/removed/renamed fields, widened `Literal`s, new schemas, and — separately,
because nothing downstream checks it — any change under the top-level `paths` key.

If the diff is **empty** but you did change the contract, go to §4. Do not report success.

## 2. Follow the compiler

```bash
make ui-check          # tsc --noEmit, oxlint, vitest
```

These it catches reliably. Fix by following the errors:

| Change | What breaks |
| --- | --- |
| Field removed or renamed | Every read site |
| Required field added to a *request* model | The object literal at the call site — e.g. `useChat.ts` where `postChatStream` is called |
| `Literal` gains a value | Any exhaustive map over it. `SourceBadge.tsx`'s `Record<SourceType, …>` is the worked example: add a lane in Python and the file will not compile until someone decides its colour |
| New member in the `StreamEvent` union | `useChat.ts`'s switch on `event.type` stops being exhaustive |

A field *added* to a response model breaks nothing — reading a wider object is fine. That is §6's
territory, not a fix.

`stream.ts` needs no change for new event types. `readEvents<T extends { type: string }>` is generic
and imports nothing from the schema; new members flow through untouched.

## 3. Check the seams the compiler cannot see

**This is the section that earns the skill.** Everything here stays green and breaks at runtime.

| Change | Why `tsc` misses it | Do this |
| --- | --- | --- |
| Route renamed or moved | `client.ts` hand-writes its URL strings; the generated `paths`/`operations` types are unused | Diff the `paths` keys against the URLs in `client.ts` by hand. This is the one that produces a silent 404 |
| New endpoint | Nothing is generated at the call-site level | Add a typed wrapper following the existing shape in `client.ts`, plus its type alias. Confirm the router is registered with `prefix="/api"` in `api/app.py` — anything outside `/api` misses the Vite dev proxy |
| New key in `metrics`, `meta`, or `chunker_snapshot_id` | Generates as an index signature, so TypeScript sees no change | Usually nothing to fix. These are deliberately soft: `EvalsPage.tsx` derives its metric columns from the data because the metric vocabulary grows every phase |
| Required → optional | Generates `?: T \| null`; most read sites still accept it | Check null-handling where it is read |

Do the route check even when the diff looked small. It is cheap and it is the only defence.

## 4. When the diff is empty but the backend changed

Almost always one of [docs/frontend_plan.md](../../../docs/frontend_plan.md) §4.5's two gotchas.
Both have working examples in the repo — copy those rather than re-deriving:

- **A union never reached `components.schemas`.** FastAPI only sees a `StreamingResponse`, so the
  union has to hang off a route's `responses=` metadata, and that takes a *model*, not a bare
  annotated union. See `StreamEventEnvelope` in `api/models.py` and how `chat.py` references it;
  `EvalRunEventEnvelope` is the second instance.
- **The schema filed under the wrong content type.** A streaming route needs a response class with
  `media_type = "text/event-stream"` set *at the class level* — `EventStreamResponse` in
  `api/routes/chat.py`. A bare `StreamingResponse` has `media_type = None` and produces a
  plausible-but-wrong OpenAPI document.

The tell for the second one is a schema that exists but sits under `application/json`.

## 5. The one rule

**Never resolve a compile error by hand-writing a TypeScript interface.** Alias into `schema.d.ts`,
or index into an existing alias:

```ts
export type SourceType = Citation['source_type']     // derived, not restated
claims: ChatResponse['claims']                        // indexed, not re-declared
```

A duplicate keeps compiling while the contract drifts — the exact failure this setup exists to
prevent, and a rule [CLAUDE.md](../../../CLAUDE.md) states outright. Never edit `schema.d.ts` either;
it is regenerated.

## 6. Where to stop

Fix everything the change *forces*, and get the gate green. Then stop.

When a new field could be displayed but nothing requires it, **report the option and ask.** Do not
pick a component and render it. [CLAUDE.md](../../../CLAUDE.md): *"The UI tracks the phases, it
doesn't lead them"* — the contract is frozen early so later phases add values to existing fields,
and a field that exists for Phase 4 does not get Phase 4's UI today.

Same for a widened `Literal`: surface that a decision is needed (what does the new lane look like?)
rather than inventing an answer to unblock the compiler.

## 7. Finish

```bash
make check-all      # ruff, pyright, pytest, tsc, oxlint, vitest
make types-check    # schema.d.ts is current — NOT part of check-all
```

Run `types-check` explicitly. It is deliberately outside `check-all`, so a stale generated file is
otherwise unflagged until someone else hits it.

Stage `schema.d.ts` with the Python change that caused it. It is generated but tracked, and
`types-check` diffs it — leaving it behind breaks the next person's build.

Do not commit unless asked.

Report in a few lines:

1. What the contract change was, read off the schema diff.
2. Which frontend files changed, and which seam from §3 you checked by hand.
3. What you deliberately left for the user to decide, with the question stated plainly.

A sync that changed one field is worth three lines, not thirty. If nothing downstream needed
changing, say that — it is a real and common outcome, not a failure to find work.
