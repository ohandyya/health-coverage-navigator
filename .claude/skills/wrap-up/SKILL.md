---
name: wrap-up
description: Close out a working session by updating docs/progress.md, and README.md when the session changed what the project claims publicly. Use when asked to wrap up, close out, or checkpoint a session, or when a session is ending and progress.md hasn't been updated to reflect it.
---

# Wrap up a session

End-of-session checkpoint. Update [docs/progress.md](../../../docs/progress.md) so the next session — which may be days from now, with no memory of this one — can pick up cold, and [README.md](../../../README.md) so a stranger reading the public repo is not told something that stopped being true.

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

## Sweep `README.md`

**Only if the session changed what an outside reader would conclude** — a capability landed or was withdrawn, counts moved, a phase completed, a command changed. A refactor, a docs tidy-up, or any work that leaves the project's outside shape identical does not touch it. Skip silently in that case; do not write a "no changes needed" line.

[`README.md`](../../../README.md) is the only doc here written for someone who has never seen this repo, and the repo is public. `progress.md` and the README carry the same facts for different readers: `progress.md` is for the next session and keeps the reasoning; the README is the project's public claim about itself and keeps only the conclusion. Never copy log prose into it.

**Rewrite in place; never append.** It has no history section to grow. A change that makes a sentence wrong deletes that sentence rather than qualifying it.

What goes stale, roughly in the order it burns:

- **Status section** — the callout, *What works today*, and *What does not work yet*. The second list is load-bearing: an item that quietly stops being true is an overclaim, which is the worst failure this file can have.
- **Counts** — documents, chunks, gold questions, sources, tests. Read these out of the artifacts (`chunks_meta.json`, `corpus.jsonl`, `evals/gold/questions.yaml`, an actual `pytest` run), never from memory or from this conversation.
- **Eval numbers** — the headline metrics *and* the `runner:` that produced them. A metric quoted without saying what produced it is worse than no metric.
- **Mermaid diagrams** — dashed nodes and edges mean *not built*. When something ships its dashes come off. Where a diagram and the status section disagree, a reader believes the picture.
- **Roadmap table** — the status column.
- **Quickstart** — changed commands, and anything describing behaviour that no longer exists (the stub's trigger words go when the stub does).
- **Engineering decisions** and **Built with Claude Code** — mostly additive, but an entry a later phase invalidated gets rewritten or cut, not left standing as a period piece.
- **Repository map** — only for a new top-level directory or a moved landmark file.

**The rule that outranks the rest: never overstate.** Where there is a choice of phrasing, take the one that makes the gap between what exists and what is planned impossible to miss. If the session ended mid-stream, say so plainly here too — the honesty of the status section is what makes the rest of the file worth believing.

## Finish

Report what you wrote in two or three lines, naming each file you touched. Do not commit unless asked.

If the session changed something that `CLAUDE.md`, `docs/plan.md`, or `docs/frontend_plan.md` owns — a new convention, a scope change, a design decision that supersedes what is written there — say so and ask whether to update it. Do not fold that content into `progress.md`; each doc owns its own material.

If the session introduced health, medical, insurance, or regulatory terminology that [docs/glossary.md](../../../docs/glossary.md) does not already carry, add it there now — same rule, same reason. This is the sweep that catches what the in-the-moment convention missed.
