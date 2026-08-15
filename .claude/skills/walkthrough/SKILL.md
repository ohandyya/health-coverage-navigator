---
name: walkthrough
description: Walk a human through AI-written code changes one logical step at a time, pausing after each step for them to read the files and ask follow-ups. Use when asked to walk through, explain, or step through recent changes, a commit, a branch, or uncommitted work — or when the user says they want to understand what was just written.
---

# Walk a human through the changes

The user did not write this code — you (or another agent) did. They are reading it to *own* it, not
to approve it. That inverts the usual reporting job: the goal is not to summarize what happened, it
is to hand over understanding, in the order a person can absorb it, at a pace they set.

**Read-only.** Do not edit, fix, run, stage, or commit anything during a walkthrough. If the user
wants a change made, that is a separate request after the walkthrough ends.

**This is not a code review.** [`/code-review`](../../../CLAUDE.md) exists and finds bugs. This skill
explains. See §7 for the one narrow exception.

## 1. Establish the scope

Ask with `AskUserQuestion` — do not guess, and do not skip this even when the conversation makes one
option look obvious. Offer these five, worded for a human, not for git:

| Option | Command | Note |
| --- | --- | --- |
| **Uncommitted work** | `git diff` **plus** `git ls-files --others --exclude-standard` | The second half is not optional. New files an agent just wrote are untracked and appear in *neither* `git diff` nor `--cached`. Omitting them silently drops the most important changes |
| **Staged work** | `git diff --cached` | |
| **The most recent commit** | `git show HEAD` | |
| **This branch vs. `main`** | `git diff main...HEAD` (three dots — from the merge base) | Usually the right one before merging |
| **Pick specific commits** | see below | |

For **pick specific commits**, print `git log --oneline -25` as a plain list and ask the user to
reply with hashes or a range (`bc5747d..HEAD`). Do not try to squeeze commits into
`AskUserQuestion` — it caps at four options and the list is always longer.

Then read the full diff for the chosen scope. **Read the actual files too, not only the diff.** A
diff shows what moved; it does not show what the surrounding function now does, and you cannot
explain a change you only saw as a hunk.

## 2. Break it into steps

This is the part that makes the walkthrough worth doing, and the part most likely to be done lazily.
A file-by-file or commit-by-commit listing is not a walkthrough — the user could have run `git show`
themselves.

**Order the steps by dependency and narrative, not by path, not by commit order, not by size.** The
spine that usually works:

> contract or data model → core logic → callers and wiring → UI → configuration → docs

Ask of every ordering: *could someone understand step N without having read step N-1?* If not, N-1
comes first, whatever the commit history says.

Rules that hold:

- **A step is one idea, not one file.** A step routinely spans several files (a new function and its
  three call sites); a large file routinely appears in two steps (it gained two unrelated things).
- **3–7 steps.** Below three, just explain it in one pass and say so. Above eight, the scope is too
  big to absorb in one sitting — say so, and offer to split it or walk a subset.
- **Tests fold into the step they pin.** Present the behavior, then what locks it: *"here's the rule,
  and here's the test that fails if it breaks."* A test explained apart from its subject teaches
  nothing.
- **Docs get one small step at the end**, if the scope touched any.
- **Collapse mechanical churn.** Renames, import reshuffles, formatting, lockfiles, and generated
  files (`frontend/src/api/schema.d.ts`, `uv.lock`) are one step at most, often just a line in the
  map saying they exist and can be skimmed. Never spend a real step on generated output — say what
  generated it and move on.

Write the step plan to a scratchpad file before starting. A walkthrough spans many turns and may
outlive this context window; the plan is what lets you resume mid-way without renumbering or losing
a step.

## 3. Step 0 — the map

Open with the shape of the whole thing, so the user knows what they are committing to and can jump:

1. **One paragraph**: what this change set does and why it exists. Not a file inventory.
2. **The numbered step list**, one line each, titles only.
3. **The count** — "5 steps" — and a note that they can say *"skip to step 3"* or *"stop"* at any
   point.

Then pause. Do not run step 1 in the same turn. The map is itself a step the user gets to react to,
and it is where they redirect you if you carved the change up wrong.

## 4. The shape of each step

Every step, without exception, has these five parts in this order:

**1 — Where you are.** `**Step 2 of 5 — <short title>**`. Always. The user has been reading files
between turns and has lost the thread; this is one line and it restores it.

**2 — Purpose.** What problem this step solves, in two or three sentences. Lead with the *why*: the
bug, the missing capability, the constraint from `CLAUDE.md`. A user who understands why a change
exists can read the code themselves; one who doesn't is just proofreading syntax.

**3 — The change itself.** Prose, plus the **three to ten lines that carry the idea** — the one
function signature, the one condition, the one new field. Never paste a whole hunk. They are about
to read the file; quoting it back to them wastes the turn. Quote the line that would otherwise take
them five minutes to find.

**4 — What to read.** Markdown links, in reading order, each with a note on what to look for and
which one to open first:

> Read [citations.ts](frontend/src/utils/citations.ts#L12-L28) first — the new `citationDomId`.
> Then [Answer.tsx](frontend/src/components/Answer.tsx#L44) for the single call site that changed.

Bare file lists are the failure mode here. "Read these four files" makes the user do the triage the
step was supposed to do for them.

**5 — The pause.** One short line inviting them to read, and to say *next* when ready.

## 5. The pause is the whole point

**End your turn after each step. Every time.**

This is the rule the skill exists to enforce, and the one an agent will erode first — two steps look
adjacent, or a step feels "too small to stop on," or you've already loaded the files so continuing
feels efficient. It is not efficient. It defeats the entire purpose: the user is reading source code
between your turns, and a turn containing steps 2 and 3 means step 3 is read with no memory of step
2's files.

Concretely:

- One step per turn. Never two, no matter how small.
- End with no pending tool calls and nothing queued.
- Never anticipate. Do not add "and in the next step we'll see…" — the next step is theirs to ask
  for.
- **Only these advance the walkthrough: an explicit "next", "continue", "go on", or a jump like
  "skip to step 4".** A follow-up question is not permission to advance. Silence is not permission.
  A "thanks, that makes sense" is not permission — answer, then wait.

## 6. Follow-up questions

Answer them fully, then **stay in the current step**. End by restoring position: *"Still on step 2 of
5 — say next when you're ready."*

Follow-ups are the walkthrough working, not an interruption of it. A question that pulls in a file
outside the current scope is fine — go read it and answer. Do not steer back to the script; the
user's question is better evidence of what they need than your step plan is.

If a follow-up reveals a step landed badly, re-explain it differently rather than defending the
first attempt. And when you don't know — an agent wrote this, possibly not in this conversation —
say so and go read, rather than reconstructing plausible intent. **Never invent a rationale for a
change you cannot account for.** "I can see what this does but not why it was written this way" is a
useful and honest thing for the user to hear.

## 7. When the code looks wrong

Flag it in **one line**, inside the step, then keep walking:

> ⚠️ Worth noting: this path doesn't handle an empty `citations` list — probably fine given the
> caller, but you may want to check. Not chasing it now.

Then continue. Do not open an investigation, do not fix it, do not stack up a findings list. The
user decides whether it's worth chasing; if they say so, chase it, and resume the step afterward.

Bugs are not the job here. A walkthrough that turns into an audit stops being a handover.

## 8. Finish

After the last step, close in a few lines:

- **The through-line** — the one or two sentences that tie the steps together, now that they've seen
  all of them. This is the part they'll remember a week from now.
- **What you skipped** and why (generated files, churn, anything out of scope).
- **Any ⚠️ flags** from §7, gathered in one place, so they aren't scattered across turns.

Nothing else. Do not offer next actions, do not commit, do not update
[docs/human_worklog.md](../../../docs/human_worklog.md) — that file is the user's, and this skill
does not write to it.
