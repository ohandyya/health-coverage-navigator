"""The agent: one PydanticAI agent over the reference corpus.

Phase 1a built it with four full-text tools; Phase 1b registered `vector_search` alongside them and
made *which* tools it sees a per-run choice (`config.Toolset`). Same agent, same corpus, same
output contract — the only thing that changed is that there is now more than one way to find a
chunk, and choosing is the agent's problem.

Deliberately empty of re-exports, for the same reason `api/__init__.py` is
(docs/progress.md, 2026-08-13): a convenience import here would make
`import health_coverage_navigator.agent.index` execute `runtime.py`, which pulls in
`pydantic_ai` and `settings.Secrets` — so a retrieval-only eval or a BM25 unit test would fail
without an `OPENAI_API_KEY`. Import the module you actually want.

What lives where:

| module | holds |
|---|---|
| `bm25.py` | the ranking formula and an inverted index, standard library only |
| `index.py` | `CorpusIndex` — the chunks, the BM25 index, and the four lexical primitives |
| `models.py` | the models the *model* sees: tool results in, `AgentAnswer` out |
| `prompt.py` | `system_prompt(toolset)` — the grounding rule, plus per-toolset search guidance |
| `tools.py` | the five tools, wrapped so every call lands in the trace, and `select_tools` |
| `runtime.py` | `build_agent()`, `stream_answer()`, `answer_question()` |

Semantic retrieval itself lives in `vectors/`, not here, for the same layering reason `index.py`
sits below `tools.py`: it is testable and runnable with no model and no `pydantic_ai` import.
"""
