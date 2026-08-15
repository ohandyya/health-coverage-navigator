"""The Phase 1a agent: one PydanticAI agent over the reference corpus.

Deliberately empty of re-exports, for the same reason `api/__init__.py` is
(docs/progress.md, 2026-08-13): a convenience import here would make
`import health_coverage_navigator.agent.index` execute `runtime.py`, which pulls in
`pydantic_ai` and `settings.Secrets` — so a retrieval-only eval or a BM25 unit test would fail
without an `OPENAI_API_KEY`. Import the module you actually want.

What lives where:

| module | holds |
|---|---|
| `bm25.py` | the ranking formula and an inverted index, standard library only |
| `index.py` | `CorpusIndex` — the chunks, the BM25 index, and the four search primitives |
| `models.py` | the models the *model* sees: tool results in, `AgentAnswer` out |
| `prompt.py` | the system prompt, carrying the grounding rule |
| `tools.py` | the four tools, wrapped so every call lands in the trace |
| `runtime.py` | `build_agent()`, `stream_answer()`, `answer_question()` |
"""
