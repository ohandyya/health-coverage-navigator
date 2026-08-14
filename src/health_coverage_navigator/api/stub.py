"""Canned answers, so the whole UI can be built and proven before an agent exists.

This is the point of Phase F0 (docs/plan.md, Phase 0; docs/frontend_plan.md §6): stand the
interface up while the stakes are zero, so Phase 1a only has to swap this module for a real agent
rather than debug an agent and a UI at the same time.

Three properties this module is built around, each of which is a UI bug it prevents:

**Every surface is exercised.** The default answer carries two citations with deliberately
*different* shapes — one with an external `url`, one without; one with a `score`, one without — and
a trace covering all four `TraceStep.kind` values, one of them with every optional field absent.
A canned response that only ever fills every field cannot catch a component that renders
`undefined ms`.

**Trigger words, not a second endpoint.** `abstain` and `lanes` in the message select the
abstention and all-lanes variants. Building those as React fixtures instead would bypass the real
serializer and the real generated types, which is exactly the coupling `make types` exists to
create.

**Deterministic.** `message_id` is a hash of the request, so a test can assert an exact body and a
browser reload gives a stable React key. Nothing here touches the disk, the network, or the clock.

The canned snippets are quoted verbatim from the committed corpus and are deliberately free of
phone numbers and e-mail addresses: `make scan`'s `pii:email` baseline is 0 and `pii:phone` is a
reviewed 37, and a stub that quoted a helpline number would silently move a reviewed count.
"""

import hashlib

from health_coverage_navigator.api.models import (
    AnswerClaim,
    ChatRequest,
    ChatResponse,
    Citation,
    TraceStep,
    Usage,
)

#: Substrings that select the abstention variant. The first is the explicit developer lever; the
#: rest are real out-of-corpus shapes borrowed from the gold set's abstention questions
#: (evals/gold/questions.yaml, `abs-*`), so the stub abstains on the same questions Phase 1a must.
ABSTAIN_TRIGGERS = ("abstain", "dermatologist", "roth ira", "npi ")

#: Selects the all-lanes variant. Phase 1a genuinely only produces `reference` citations, but the
#: green and amber badge styling cannot be verified against data that never carries those lanes.
LANES_TRIGGER = "lanes"

#: The default answer, one sentence per claim. Split out as constants rather than sliced apart at
#: runtime, because `AnswerClaim.text` has to be a verbatim substring of the answer and a `.split()`
#: that silently stops being verbatim is exactly the bug Phase 4's highlighting would inherit.
CLAIM_1 = (
    "A **deductible** is the amount you pay for covered health care services before your "
    "insurance plan starts to pay. [c1]"
)
CLAIM_2 = (
    "After you meet it, you usually pay only a copayment or coinsurance, and your plan pays "
    "the rest. [c2]"
)
LANES_SENTENCE = "Open Enrollment for 2026 coverage runs November 1 – January 15. [c3]"

ANSWER = f"{CLAIM_1} {CLAIM_2}"

ABSTAIN_ANSWER = (
    "I don't have that in my reference material. My corpus covers HealthCare.gov consumer "
    "content, the Medicare publications, and Medicare National Coverage Determinations — this "
    "question falls outside all three, so I'd rather say so than guess."
)


def _citations() -> list[Citation]:
    """The two reference citations behind the default answer.

    Both are real documents: `GET /api/corpus/{doc_id}` resolves each one against the committed
    corpus, so the citation drill-down is exercised end to end rather than pointing at more canned
    text. Their shapes differ on purpose — see the module docstring.
    """
    return [
        Citation(
            id="c1",
            source_type="reference",
            title="Deductible — HealthCare.gov Glossary",
            url="https://www.healthcare.gov/glossary/deductible",
            doc_id="glossary_deductible",
            chunk_id="healthcare_gov:glossary_deductible#000",
            snippet=(
                "The amount you pay for covered health care services before your insurance plan "
                "starts to pay. With a $2,000 deductible, for example, you pay the first $2,000 "
                "of covered services yourself."
            ),
            score=0.81,
        ),
        Citation(
            id="c2",
            source_type="reference",
            title="Medicare & You 2026, p. 41",
            url=None,
            doc_id="10050_p041",
            chunk_id="medicare_pubs:10050_p041#000",
            snippet=(
                "You pay a copayment for each emergency department visit and 20% of the "
                "Medicare-approved amount for doctors’ services. The Part B deductible applies."
            ),
            score=None,
        ),
    ]


def _all_lane_citations() -> list[Citation]:
    """One citation per lane, so the badge palette can be verified before Phases 2 and 3 exist."""
    reference, _ = _citations()
    return [
        reference,
        Citation(
            id="c2",
            source_type="structured_api",
            title="Marketplace API — /drugs/covered",
            url="https://developer.cms.gov/marketplace-api/",
            snippet='{"covered": true, "rxcui": "1049502", "plan_id": "12345GA0010001"}',
            score=None,
        ),
        Citation(
            id="c3",
            source_type="web",
            title="2026 Open Enrollment dates — HealthCare.gov",
            url="https://www.healthcare.gov/",
            snippet="Open Enrollment for 2026 coverage runs November 1 through January 15.",
            score=0.44,
        ),
    ]


def _claims() -> list[AnswerClaim]:
    """One claim per sentence. §4.2 permits a single whole-answer claim in Phase 1a, but two costs
    nothing and makes the `[c1]`-scrolls-to-its-citation behaviour real rather than notional."""
    return [
        AnswerClaim(text=CLAIM_1, citation_ids=["c1"]),
        AnswerClaim(text=CLAIM_2, citation_ids=["c2"]),
    ]


def _trace(*, hits: int) -> list[TraceStep]:
    """A four-step trace covering every `kind`.

    Step 1 leaves `tool`, `input`, `duration_ms` and `tokens` all `None` — that is the case the
    trace panel is most likely to render as `undefined`, so the stub always produces it.
    """
    return [
        TraceStep(
            index=0,
            kind="plan",
            summary="Classify the question and pick a lane",
        ),
        TraceStep(
            index=1,
            kind="tool_call",
            tool="retrieve",
            input={"query": "deductible", "k": 8},
            summary="retrieve(query='deductible', k=8)",
            duration_ms=12,
        ),
        TraceStep(
            index=2,
            kind="tool_result",
            tool="retrieve",
            summary=f"{hits} chunks returned" + (", top score 0.81" if hits else ""),
            duration_ms=240,
        ),
        TraceStep(
            index=3,
            kind="synthesis",
            summary="Synthesize an answer from the retrieved context",
            duration_ms=1200,
            tokens=890,
        ),
    ]


def wants_abstention(message: str) -> bool:
    """Whether this message selects the abstention variant.

    `abstained` is a first-class boolean in the contract precisely so the UI never has to
    pattern-match answer prose — but *something* has to decide, and at Phase 0 that something is
    this function rather than a guardrail that does not exist yet.
    """
    lowered = message.lower()
    return any(trigger in lowered for trigger in ABSTAIN_TRIGGERS)


def stub_answer(request: ChatRequest, *, abstain: bool | None = None) -> ChatResponse:
    """The canned `ChatResponse` for one request.

    `abstain=None` auto-detects from the message; pass `True`/`False` to force a variant, which is
    what the tests do rather than depending on the trigger words staying what they are.
    """
    conversation_id = request.conversation_id or "conv_stub"
    digest = hashlib.sha256(
        f"{conversation_id}|{request.message}|{request.plan_year}".encode()
    ).hexdigest()[:12]
    message_id = f"msg_{digest}"

    if abstain is None:
        abstain = wants_abstention(request.message)

    if abstain:
        return ChatResponse(
            conversation_id=conversation_id,
            message_id=message_id,
            abstained=True,
            answer=ABSTAIN_ANSWER,
            claims=[],
            citations=[],
            # A non-empty trace even on an abstention: this is exactly the trace you would read to
            # work out *why* it abstained, so leaving it empty would remove the debugging surface
            # at the only moment it matters.
            trace=_trace(hits=0),
            usage=Usage(latency_ms=310, model=None),
        )

    if LANES_TRIGGER in request.message.lower():
        answer = f"{ANSWER} {LANES_SENTENCE}"
        citations = _all_lane_citations()
        claims = [*_claims(), AnswerClaim(text=LANES_SENTENCE, citation_ids=["c3"])]
    else:
        answer = ANSWER
        citations = _citations()
        claims = _claims()

    return ChatResponse(
        conversation_id=conversation_id,
        message_id=message_id,
        abstained=False,
        answer=answer,
        claims=claims,
        citations=citations,
        trace=_trace(hits=8),
        usage=Usage(
            input_tokens=142,
            output_tokens=61,
            total_tokens=203,
            latency_ms=1452,
            model=None,
        ),
    )
