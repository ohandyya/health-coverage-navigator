# Configuration — secrets vs. everything else

Where a value is allowed to live, and why the line is drawn there. [`CLAUDE.md`](../CLAUDE.md)
carries the two rules that must never be broken; this file carries the reasoning and the
easy-to-break details.

## The axis

Configuration is split by **where a value is allowed to live** — a git-ignored machine-local
credential versus a committed, reproducible input — not by how sensitive it feels. That axis is
what settles the environment-variable question.

- **Secrets** → `.env` (git-ignored), read through `Secrets` in
  `src/health_coverage_navigator/settings.py`. `.env.example` is the committed template and must
  never hold a real value.
- **Everything else** → `config.yaml` (committed), read through `Config` in
  `src/health_coverage_navigator/config.py`. Model choice, retrieval parameters, and chunking
  parameters all live there.

## Never add a tunable to `Secrets`

Any field on a `BaseSettings` subclass is populated by an environment variable of the same name,
and an env var is invisible to git — so an eval run configured by one cannot be reproduced from
the repo, and the score is worth less for it. `tests/test_settings.py` asserts the field set for
exactly this reason.

## Never put a secret in `config.yaml`

It is committed, and `make scan` treats a credential-shaped assignment there as a **blocking**
error.

## Three rules that are easy to break by accident

- **`config.yaml` is mandatory, and its fields carry no Python defaults.** A default in code
  beside a value in YAML is two sources of truth that drift silently. A missing key is an error
  that names the key; an unknown key is rejected rather than ignored.
- **`config.py` must not import from `chunking/`.** `chunking/__init__.py` imports `pipeline`,
  which imports `config`, so the reverse direction is a cycle. This is why `ChunkParams` is
  *defined* in `config.py`. `CHUNKER_VERSION` deliberately stayed in `chunking/params.py`: it
  describes what the *code* does rather than how it is tuned, so it must not sit in a file users
  are invited to edit.
- **The five `chunking:` values are pinned by `data/processed/*/chunks_meta.json`.** They feed
  `params_sha256` and every `snapshot_id`. Changing one without running `make chunk` in the same
  change leaves three committed manifests describing chunks nobody can rebuild.

Chunking parameters themselves — what each one means and how the values were chosen — are in
[`chunking.md`](chunking.md).
