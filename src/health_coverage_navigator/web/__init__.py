"""The Phase 2 web lane: Tavily's `/search`, wrapped for provenance and honest degradation.

Design: docs/web_search_tool.md. This package must not import from `agent/` — the same rule
`structured/` follows, and what keeps every test here runnable with no model and no PydanticAI.
"""
