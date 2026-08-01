---
name: scan-sensitive
description: Check whether anything unsafe is about to become public — secrets, API keys, tokens, credentials, PII/PHI, or AMA/ADA licence-restricted code tables. Use before committing or pushing, when adding or refreshing a data source under data/, when asked "is this safe to commit", "did I leak anything", "scan for secrets", or after any change that vendors new files into the repo.
---

# Scan for secrets, PII/PHI, and licence-restricted content

This repo is **public**. [CLAUDE.md](../../../CLAUDE.md)'s *Public-repo data guardrail* blocks
three unrelated things from being committed: credentials, PII/PHI, and AMA/ADA-licensed code
tables. `scripts/scan_sensitive.py` checks all three across everything that is or would become
public — tracked, staged, and untracked-but-not-ignored files.

**Your job is the triage, not the matching.** The script finds candidates deterministically and
remembers past decisions; deciding what a *new* finding means is what you are here for.

## Run it

```bash
uv run python scripts/scan_sensitive.py --json     # default: everything public-bound
```

Pick the scope from what the user is actually asking about:

| When | Add | Make target |
| --- | --- | --- |
| "is the repo clean", before publishing or pushing | *(nothing)* | `make scan` |
| "is this commit safe" — only what is staged | `--staged` | `make scan-staged` |
| "did I just write something bad" — work in progress, before `git add` | `--unstaged` | `make scan-unstaged` |
| Everything uncommitted, staged or not | `--staged --unstaged` | — |
| A specific source or directory | `--paths data/raw/medicare_ncd` | — |
| A leak is suspected historically | `--deep-history` (slow: every blob ever committed) | — |
| Patterns were changed | `--self-test` first — proves no detector went dead | `make scan-selftest` |

`--unstaged` covers exactly what `git status` files under *"Changes not staged for commit"*
and *"Untracked files"*. It is the cheapest useful scope — usually a handful of files — so
prefer it when the question is about work just done rather than about the repo as a whole.

Exit codes: **0** clean · **1** blocking hit · **2** advisory count above baseline.

**Baselines only apply to a full-repo scan.** The counts in `sensitive_baseline.toml` are
whole-repo totals, so under `--staged`, `--unstaged` or `--paths` the advisory hits are
listed but not compared, and exit 2 cannot occur. Do not read "not compared" as "fine", and
never adjust a baseline from a scoped run — re-run the full scan first. Blocking detection
is identical in every scope.

The default scope is the only one that answers "is the repo clean". A scoped run that comes
back clean means *those files* are clean; say which scope you ran.

## Triage every finding into exactly one of three outcomes

Say which one you chose, and why. Never report a finding without resolving it.

### 1. Real — it is what it looks like

**A leaked credential is not fixed by deleting the line.** The value is already in git history
and, if it was pushed, on GitHub's servers and possibly in someone's clone or a scraper's index.
Order matters:

1. **Rotate or revoke it at the provider first.** Until that happens, nothing else you do
   reduces the risk.
2. Then remove it from the working tree and put it behind `.env` (already gitignored; see
   [.gitignore](../../../.gitignore)).
3. Then purge it from history (`git filter-repo`, or BFG) if it was ever committed.
4. Force-push, and tell the user that anyone with a clone still has the old value.

Do not silently edit the file and report "fixed" — say plainly that a credential leaked and what
must be rotated. For a licensing hit (an LCD, a Billing/Coding Article, a CPT/CDT code table),
the file must not be vendored at all: remove it and note which cleared subset can be used
instead.

### 2. Regex bug — the pattern matched something it was never meant to match

**Fix the pattern in `scripts/scan_sensitive.py`. Do not allowlist it.** An allowlist entry
silences this one instance while leaving the bad pattern free to bury the next true positive of
the same shape.

Two worked examples already in the file, both from real false positives:

- A bare `sk-[A-Za-z0-9_-]{20,}` matched inside the URL slug `ask-about-preventive-services`.
  Fixed by anchoring to real vendor prefixes (`sk-ant-`, `sk_live_`) plus a lookbehind.
- `pii:npi` matched any Luhn-valid 10-digit run, reporting digits inside a UUID and a vendor
  PDF filename. Fixed by also requiring the identifier to be *labelled* `NPI`.

When you fix a pattern, add the offending string to `ANTI_CANARIES` so it can never regress, and
re-run `--self-test`.

### 3. Cleared — genuinely fine to publish

Add an entry to `scripts/sensitive_baseline.toml` — but **propose it and ask before writing it**.
Every entry needs a `reason` (the script refuses to load one without), and the reason must say
*why it is publishable*, not merely what it is. "CMS institutional mailbox published in the
source content itself" is a reason; "known e-mail" is not.

Prefer the narrowest mechanism that works:

- **`[[allow]]`** when there is a principle — a whole class is safe (`*@*.gov`, toll-free area
  codes).
- **`[baseline]`** when the count is what matters and you want to be told about a *jump*
  (the 42 narrative `CPT` mentions; the 39 published agency phone numbers).

Raising a baseline is a decision, not bookkeeping. Look at the delta before you raise it — a
baseline that only ever ratchets upward unexamined is how the real thing gets through.

## Reporting rules

**Never print a full secret value** — not in your reply, not in a commit message, not in a file.
The script already truncates to a 4-character prefix and a length; keep it that way. A scanner
whose output reprints secrets into a shareable transcript is a leak amplifier.

Report in this order:

1. **Verdict first**, one line: safe to commit, or not, and why.
2. Blocking hits — every one, with the triage outcome you chose.
3. Advisory drift — only where a count moved, with your read on whether it matters.
4. Allowlisted total — one line. Suppression stays visible; do not omit it.

A clean run is worth three lines, not thirty. Do not restate the per-detector "ok" list.

## After clearing a new data source

A scan that clears newly vendored data usually means other things now need updating, and these
are owned elsewhere — say so rather than folding it in:

- `data/README.md` — the per-source entry and its licensing note.
- [CLAUDE.md](../../../CLAUDE.md) — the guardrail's cleared/blocked lists, if the source changes
  what they say.
- [docs/glossary.md](../../../docs/glossary.md) — a new source almost always drags in new domain
  terms, and the glossary rule says they land in the same change.

## Scope limits worth stating out loud

Be honest about these when reporting; do not imply more assurance than the tool gives.

- Gitignored paths are **not scanned**, by design — that is what keeps `.env` and the 45 MB of
  `medicare_pubs` PDFs out of scope. A secret inside a gitignored file is invisible here, and
  correctly so, but it is also invisible if that file is ever un-ignored.
- Regex detection finds **known shapes**. A credential in an unrecognised format, or a secret
  pasted into prose, will not match. A clean scan means "nothing known-bad was found", never
  "there is definitely nothing here".
- `--deep-history` scans history for **credentials only**. The licensing and PII layers describe
  what is publishable *now*, and would otherwise report the same corpus text once per revision.
