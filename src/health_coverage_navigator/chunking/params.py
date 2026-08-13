"""Chunking parameters, and the fingerprint that pins an eval run to them.

Every value here is derived from a measured distribution over the live corpus rather than picked
by feel; docs/chunking.md carries the measurements and the reasoning. The short version is in each
field's comment.

Sizes are in **characters, not tokens**, and there is deliberately no tokenizer dependency. Phase
1a retrieval is lexical, where tokens are irrelevant; Phase 1b's embedding ceiling (~32k chars) is
27x `max_chars`, so a real token count would only ever buy a cost estimate — and one baked into a
committed artifact would be invalidated by any embedding-model change. Use ~4 chars/token when an
estimate is needed (`Chunk.approx_tokens`).
"""

import hashlib
import json

from pydantic import BaseModel

#: Bump on any behaviour change that the parameters below do not capture — a new boundary-ladder
#: rung, a change to heading detection, a different id format. Bumping invalidates every
#: `snapshot_id`, which is the point: an eval score measured under v1 must not silently compare
#: against chunks built by v2.
CHUNKER_VERSION = 1


class ChunkParams(BaseModel):
    model_config = {"frozen": True}

    #: ~300 tokens. The NCD median section body (832) and the healthcare_gov median doc (904) both
    #: fit, so the typical retrieval unit survives whole and only the tail splits.
    max_chars: int = 1200

    #: Set by the gold set, not by taste: the longest `expected_snippet` is 279 chars, so an
    #: overlap above that makes "a snippet is wholly inside at least one chunk" a guarantee rather
    #: than an observation. See `splitter.split_span` for why the snap direction matters.
    overlap_chars: int = 320

    #: A trailing sliver shorter than this is merged back into its predecessor (never across a
    #: section heading).
    min_tail_chars: int = 300

    #: A boundary is only used if it fills at least this much of the budget; otherwise the ladder
    #: falls to the next rung. Stops one early sentence break from emitting a 60-char chunk.
    min_fill_chars: int = 480

    #: Whitespace-normalized floor for a *document* to be chunked at all. Drops the 29
    #: medicare_pubs pages that extract to "Notes" or a cover fragment, and nothing else.
    min_doc_chars: int = 40

    def fingerprint(self) -> str:
        """Stable sha256 over the parameter values, for the chunk manifest's `params_sha256`."""
        payload = json.dumps(self.model_dump(), sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


DEFAULT_PARAMS = ChunkParams()
