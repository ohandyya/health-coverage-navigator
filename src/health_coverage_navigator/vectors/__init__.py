"""The Phase 1b vector store: embeddings over the chunked corpus, in LanceDB.

Deliberately empty of re-exports, for the same reason as `agent/__init__.py`: a convenience
import here would make `import ...vectors.manifest` execute `store.py` and drag in `lancedb` and
`pyarrow`, so reading a manifest — which `evals/runner.py` does on every run, with no vector
search involved — would pay for a vector database it never touches.

| module | holds |
|---|---|
| `embedder.py` | the `Embedder` seam and its OpenAI backing. The only part that spends money |
| `manifest.py` | `VectorManifest` — what the committed sidecar records, and the snapshot id |
| `store.py` | `VectorIndex` (query) and `build_store` (ingest). The only importer of lancedb |

Nothing in this package imports `pydantic_ai`, so `--runner vector` and every store test run
without the agent stack — the same layering rule that keeps `agent/index.py` below
`agent/tools.py`.
"""
