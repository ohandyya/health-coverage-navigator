"""The chunker's behaviour version.

`ChunkParams` used to live here. It moved to `config.py` when configuration was consolidated into
the committed `config.yaml`, because the values are tunables that belong with the other tunables —
and because `config.py` cannot import from this package without a cycle (`chunking/__init__.py`
imports `pipeline`, which imports `config`).

`CHUNKER_VERSION` stayed behind, and the distinction is the point: it is not a tunable. It
describes what the *code* does, so it cannot be moved into a file a user is invited to edit.
"""

#: Bump on any behaviour change that the parameters in `config.yaml` do not capture — a new
#: boundary-ladder rung, a change to heading detection, a different id format. Bumping invalidates
#: every `snapshot_id`, which is the point: an eval score measured under v1 must not silently
#: compare against chunks built by v2.
CHUNKER_VERSION = 1
