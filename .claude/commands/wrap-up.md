---
description: Close out a working session by updating docs/progress.md
---

End-of-session checkpoint. Update [docs/progress.md](docs/progress.md) so the next session — which may be days from now, with no memory of this one — can pick up cold.

## Gather

1. Read `docs/progress.md`, noting the date of the newest log entry.
2. Run `git log --format='%h %ad %s' --date=short` since that date, plus `git status` and `git diff --stat`, to see what actually changed on disk. Include uncommitted work.
3. Re-read this conversation for what git cannot show: what was decided, what was tried and abandoned, what was deliberately left unfinished.

If nothing meaningful changed since the last entry, say so and stop. Do not write a filler entry.

## Write the log entry

Prepend a new entry at the top of `## Log` (newest first). Never edit an existing entry — they are true as of their date.

```markdown
### YYYY-MM-DD — short title

**Did:** what changed, in a sentence or two. Not a file listing.

**Decided:** choices made and the reasoning. Only choices someone could reasonably have made differently.

**Rejected:** alternatives considered and why they lost. Omit if none.

**Dead end:** anything tried that did not work, so it is not retried. Omit if none.

**Stopped at:** where work was interrupted mid-stream — a half-finished refactor, a failing test, a question that needs answering before the next step. Omit if the session ended clean.

**Commits:** short hashes. Omit if nothing was committed.
```

Only `**Did:**` is required. Drop the rest when they would be empty — an entry with four "N/A" lines is noise.

**The test for every line: could this be reconstructed from the code or `git log`?** If yes, cut it. No file inventories, no restating what a script does, no counts already recorded elsewhere. The value of this file is entirely in the non-derivable part: reasoning, rejected paths, and where things stopped.

## Rewrite the current state block

Overwrite `## Current state` in place:

- Update the date.
- **Phase** — current phase and an honest one-line read on where it stands.
- **Next up** — the single next actionable thing, with enough context to start without re-deriving it. Point at the relevant section of `plan.md` or `frontend_plan.md`.
- **Open questions** — anything unresolved that will block work later. Drop items once resolved (the log keeps the history).
- Tick checklist items that genuinely completed. Add rows if the session revealed work the checklist was missing.

## Finish

Report what you wrote in two or three lines. Do not commit unless asked.

If the session changed something that `CLAUDE.md`, `docs/plan.md`, or `docs/frontend_plan.md` owns — a new convention, a scope change, a design decision that supersedes what is written there — say so and ask whether to update it. Do not fold that content into `progress.md`; each doc owns its own material.

If the session introduced health, medical, insurance, or regulatory terminology that [docs/glossary.md](docs/glossary.md) does not already carry, add it there now — same rule, same reason. This is the sweep that catches what the in-the-moment convention missed.
