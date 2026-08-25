"""Phase 3's live-API clients — the read side of the structured lane's live half.

One module per upstream, plus the HTTP machinery they share. **Nothing here imports from `agent/`**,
which is what lets the whole package be tested with no model, no agent, and no PydanticAI import —
the arrangement `web/` and `structured/` already use.

Why this is a package of its own rather than part of `structured/`: the *lane* is shared and the
*machinery* is not. `structured/` is DuckDB over local Parquet — no network, no credential, no rate
limit — and putting an HTTP client beside it would give one package two failure models. The lane is
expressed by `source_type` and by both toolsets registering together (docs/structured-api-tools.md
§12), never by sharing a directory.
"""
