# LanceDB — Vector Backend Decision & Usage

Scope: Phase 1-b of Health Coverage Navigator (swapping the `retrieve()` tool from
full-text search onto vector retrieval, without changing the agent or the eval set).

---

## 1. Why we chose LanceDB

**Context that drove the decision:**
- The corpus (HealthCare.gov content + *Medicare & You* + NCDs) is small — thousands of
  chunks, not millions. Raw scale isn't the deciding factor for any of the usual options.
- Phase 1-b's whole point is to A/B a vector backend against the Phase 1-a full-text
  baseline on the same ~30-question gold set, behind the same `retrieve()` interface.
- This is a public, open-source repo — anyone cloning it should be able to run the
  pipeline with zero extra infrastructure.
- We already ruled out OpenRouter over BAA/PII exposure for health-domain data. The same
  reasoning favors a backend that never sends our data to a third party.

**Why LanceDB fits:**
- **Embedded, no server.** `pip install lancedb` and it runs in-process against a local
  folder — same "clone and run" simplicity as Chroma, no Postgres instance to stand up
  like pgvector would require.
- **Open source, no feature gating.** Core library is Apache 2.0, fully featured. LanceDB
  Cloud / Enterprise exist but are optional paid tiers we don't touch — everything we use
  is free and self-hosted.
- **Nothing leaves our machine.** Consistent with the BAA/PII stance already taken on
  OpenRouter — no managed cloud vector service in the loop.
- **Disk-based, not memory-bound.** Built on the Arrow/Lance columnar format, so it
  doesn't hit the RAM ceiling as the corpus grows (Medicare & You handbook, later NCD
  expansion, eventually PUF-derived text).
- **Versioned writes.** Every write is a new dataset version — useful for pinning a
  corpus/embedding snapshot to a specific eval run, so a later re-ingest can't silently
  change what an old eval score was measured against.
- **Built-in hybrid search.** Vector *and* full-text (BM25-style) search live in the same
  table. That means the Phase 1-a lexical baseline and the Phase 1-b vector backend can
  share one store, making the comparison cleaner than swapping between two separate
  systems.
- **SQL-style metadata filtering in the same call.** `.where("plan_year = 2026")`
  combined with the vector search directly enforces our "pin the plan year" principle,
  rather than filtering after the fact.
- **Async API.** Pairs naturally with PydanticAI's `async def` tools and a FastAPI
  service handling concurrent requests.

**Alternatives considered:**
- **Chroma** — equally simple to set up, but weaker on cold-restart cost and stays
  in-memory-first, which matters less at our current scale than the eval-reproducibility
  angle above.
- **pgvector** — the better long-term architectural fit *if* Phase 5 ends up putting the
  PUFs in Postgres instead of DuckDB/SQLite (vectors + structured plan data, one
  database, real SQL joins). Not worth the operational overhead in Phase 1-b.
- **Qdrant / Weaviate / Pinecone** — excluded for now: either a server to operate before
  we need one, or a managed cloud service that reopens the BAA question.

---

## 2. How to use it — Option B (manual embeddings)

We compute embeddings ourselves and hand LanceDB plain vectors, rather than using its
built-in embedding-function registry. LanceDB just stores and indexes vectors — it never
calls OpenAI on our behalf, so there's no hidden billing, and we keep full control over
batching (needed to use OpenAI's Batch API discount on bulk ingestion).

### Requirements our `embed()` function must satisfy
- **Fixed dimensionality, every call** — must match the table's `Vector(n)` schema field,
  consistently, for every row and every query.
- **Same model/version for documents and queries** — otherwise similarity search compares
  incompatible embedding spaces.
- **Return type** — a flat `list[float]` or NumPy array. LanceDB stores it as an Arrow
  `FixedSizeList<float32>` under the hood.
- **Guard against empty strings** — OpenAI's embeddings endpoint returns a 400 on empty
  input; filter/skip empty chunks before calling.
- **Full re-ingest if the model changes** — swapping embedding models isn't a config
  change, it's regenerating every stored vector.

### Install

```bash
pip install lancedb pyarrow openai
```

### Bulk ingestion (offline, sync)

One-off script, not part of a live request path, so a blocking sync client is fine —
and it's the natural place to route through OpenAI's Batch API later for the ~50%
discount, since this isn't latency-sensitive.

```python
from openai import OpenAI
import lancedb

client = OpenAI()

def embed(text: str) -> list[float]:
    resp = client.embeddings.create(model="text-embedding-3-small", input=text)
    return resp.data[0].embedding

# chunks: list of dicts from data/processed, e.g.
# {"text": ..., "source_type": "reference", "source_url": ..., "plan_year": 2026}
rows = [
    {**chunk, "vector": embed(chunk["text"])}
    for chunk in chunks
    if chunk["text"].strip()  # guard against empty chunks
]

db = lancedb.connect("data/lancedb")
table = db.create_table("corpus", data=rows)
```

> **Cost lever:** for a large one-time ingest, swap the loop above for OpenAI's Batch API
> (submit a batch job, poll for completion) to get the ~50% discount — this is exactly
> the case where Option B's manual control over the embedding calls pays off compared to
> the registry's automatic (always-synchronous) mode.

### Live query path (PydanticAI tool, async)

Inside the agent, use the async OpenAI client and LanceDB's async connection so an
embedding call never blocks the event loop while other requests are in flight.

```python
from openai import AsyncOpenAI
import lancedb

client = AsyncOpenAI()

async def embed(text: str) -> list[float]:
    resp = await client.embeddings.create(model="text-embedding-3-small", input=text)
    return resp.data[0].embedding

db = await lancedb.connect_async("data/lancedb")
table = await db.open_table("corpus")

async def retrieve(query: str, plan_year: int) -> list[dict]:
    vector = await embed(query)
    return (
        await table.vector_search(vector)
        .where(f"plan_year = {plan_year}")
        .limit(5)
        .to_list()
    )
```

`retrieve()` above is a drop-in PydanticAI tool: `async def`, awaits its own embedding
call, awaits LanceDB's `AsyncTable.vector_search`. Nothing in the path blocks.

### Who pays for the embeddings

Our OpenAI account, same as any other call we make against our existing API key —
Option B just means we call the API explicitly instead of letting LanceDB's registry do
it invisibly, which is what lets us route bulk ingestion through the Batch API discount.
