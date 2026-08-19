"""End-to-end checks on the HTTP surface.

Two apps are under test here, because the server has two answering modes and both are real:

- **`client`** — `create_app(stub=True)`, serving `api/stub.py`. Most of this file uses it, and
  that is deliberate rather than left over. The stub is a *fixture with known content*: it fills
  every UI branch on purpose (two citations of different shapes, a trace covering all four `kind`
  values, an all-lanes variant), so it is what the contract and the plumbing can be asserted
  against exactly. An agent's output cannot be asserted exactly, by definition.
- **`agent_client`** — `create_app(stub=False)` with the model scripted, for what only the real
  path has: the 503 when the corpus was never chunked, the health lane tracking the index, and the
  agent's answer reaching the browser through the same SSE grammar.

What is testable here is the *contract* and the *plumbing*: that every UI branch is exercised, that
citation drill-down resolves against the real corpus, and that the static mount does not eat the
API — the reason `create_app` takes a `dist_dir`, since that is an order-dependent failure whose
only symptom is the wrong response body. The agent's own behaviour is `tests/test_agent.py`'s.

One Phase 0 test is deliberately gone. `test_stream_done_payload_equals_the_non_streaming_response`
compared two independent code paths for equality; `answer_question()` is now `stream_answer()`
drained to its `done` event, so they cannot differ and the property is asserted structurally in
`tests/test_agent_stream.py`. It survives here for the stub, which is still two paths.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from health_coverage_navigator.agent.runtime import build_agent
from health_coverage_navigator.api.app import create_app
from health_coverage_navigator.api.models import MARKER_RE, ChatRequest, ChatResponse
from health_coverage_navigator.api.stub import stub_answer
from health_coverage_navigator.evals.loader import load_gold_set
from health_coverage_navigator.evals.models import GoldSet


@pytest.fixture(scope="session")
def client():
    with TestClient(create_app(dist_dir=Path("/nonexistent-dist"), stub=True)) as c:
        yield c


def _patch_vectors(monkeypatch: pytest.MonkeyPatch, vectors) -> None:
    async def load_vectors():
        return vectors

    monkeypatch.setattr("health_coverage_navigator.api.app._load_vectors", load_vectors)


@pytest.fixture(autouse=True)
def _no_vector_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every app in this module boots without a vector store unless it asks for one.

    Autouse because `create_app`'s lifespan calls `_load_vectors`, which builds the *live* OpenAI
    embedder — and `conftest._no_live_embeddings` refuses that, correctly. Without this, every test
    that merely stands up an app (the SPA-fallback and static-mount ones included) would fail on a
    provider call it never wanted to make.
    """
    _patch_vectors(monkeypatch, None)


def _use_fixture_stores(monkeypatch: pytest.MonkeyPatch, agent_kit=None, structured=None) -> None:
    """Point the app's lifespan at the fixture corpus, vector store and plan mirror.

    The index would otherwise build all 6,722 chunks and the mirror would need a 24 MB download,
    and these tests are about HTTP wiring rather than retrieval. `structured` is passed explicitly
    rather than defaulted to the real store because a test that boots against whatever happens to
    be on the developer's disk is a test that passes for the wrong reason.
    """
    index = None if agent_kit is None else agent_kit.index
    monkeypatch.setattr("health_coverage_navigator.api.app._load_index", lambda: index)
    monkeypatch.setattr("health_coverage_navigator.api.app._load_structured", lambda: structured)
    _patch_vectors(monkeypatch, None if agent_kit is None else agent_kit.vectors)


@pytest.fixture
def agent_client(agent_kit, structured_store, monkeypatch: pytest.MonkeyPatch):
    """The real answering path: the two-chunk fixture corpus, the sample plan mirror, and a
    scripted model.

    Every lane the configuration asks for is present, which is what makes this the *answering*
    fixture — `_unavailable` refuses to serve a run whose configured lanes are half there, and a
    test that tripped that would be testing the 503 rather than the answer.
    """
    _use_fixture_stores(monkeypatch, agent_kit, structured=structured_store)
    with (
        build_agent(structured=True).override(
            model=agent_kit.script(agent_kit.SEARCH, agent_kit.answer())
        ),
        TestClient(create_app(dist_dir=Path("/nonexistent-dist"), stub=False)) as c,
    ):
        yield c


@pytest.fixture
def no_corpus_client(monkeypatch: pytest.MonkeyPatch):
    """A server that booted on a fresh clone, where `make chunk` has never run."""
    _use_fixture_stores(monkeypatch)
    with TestClient(create_app(dist_dir=Path("/nonexistent-dist"), stub=False)) as c:
        yield c


@pytest.fixture
def no_plan_data_client(agent_kit, monkeypatch: pytest.MonkeyPatch):
    """A server with a corpus but no plan mirror — a clone that ran `make chunk`, not `make puf`.

    The configuration asks for the relational lane, so this must be a 503 naming the download and
    **not** a quietly reference-only answer: the question that reaches this server is a
    plan-specific one, and prose about plans in general is the wrong answer to it.
    """
    _use_fixture_stores(monkeypatch, agent_kit, structured=None)
    with TestClient(create_app(dist_dir=Path("/nonexistent-dist"), stub=False)) as c:
        yield c


@pytest.fixture(scope="session")
def gold() -> GoldSet:
    return load_gold_set()


def _parse_sse(body: str) -> list[dict]:
    """Split an SSE body into its JSON payloads."""
    events = []
    for frame in body.split("\n\n"):
        if not frame.strip():
            continue
        header, _, data = frame.partition("\ndata: ")
        payload = json.loads(data)
        assert header == f"event: {payload['type']}", (
            f"frame header {header!r} disagrees with its payload type {payload['type']!r}"
        )
        events.append(payload)
    return events


# ---------------------------------------------------------------- 1. health -----------------


def test_health_reports_stub_mode(client: TestClient):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["stub"] is True, "canned answers must never be mistakable for real ones"

    lanes = {lane["source_type"]: lane for lane in body["lanes"]}
    assert set(lanes) == {"reference", "structured_api", "web"}, (
        "the three-lane vocabulary exists from Phase 0 even though two lanes are empty"
    )
    assert lanes["structured_api"]["configured"] is False, "a stub server holds no plan data"
    assert lanes["web"]["configured"] is False


def test_health_drops_the_stub_flag_when_the_agent_answers(agent_client: TestClient):
    """This is what removes the UI's stub banner — `App.tsx` renders it off `health.stub` alone."""
    body = agent_client.get("/api/health").json()
    assert body["stub"] is False
    lanes = {lane["source_type"]: lane for lane in body["lanes"]}
    assert lanes["reference"]["configured"] is True


def test_health_reports_the_structured_lane_live_with_a_mirror(agent_client: TestClient):
    """Phase 1-c turns the second badge on, and it is reported off the *store* rather than the
    config: a lane that is switched on with no data behind it can only 503."""
    lanes = {lane["source_type"]: lane for lane in agent_client.get("/api/health").json()["lanes"]}
    assert lanes["structured_api"]["configured"] is True
    assert "tables" in lanes["structured_api"]["detail"]
    assert "2026" in lanes["structured_api"]["detail"], "the detail names the plan year on disk"


def test_health_reports_the_structured_lane_down_without_a_mirror(no_plan_data_client: TestClient):
    body = no_plan_data_client.get("/api/health").json()
    lanes = {lane["source_type"]: lane for lane in body["lanes"]}
    assert lanes["structured_api"]["configured"] is False
    assert "make puf" in lanes["structured_api"]["detail"]


def test_health_reports_the_reference_lane_down_without_chunks(no_corpus_client: TestClient):
    """`configured` tracks the *index*, not the document count. Citation drill-down works off the
    committed corpus either way, so reporting off `docs` would show a live lane on a server that
    can only 503."""
    body = no_corpus_client.get("/api/health").json()
    lanes = {lane["source_type"]: lane for lane in body["lanes"]}
    assert lanes["reference"]["configured"] is False
    assert "make chunk" in lanes["reference"]["detail"]


def test_health_counts_come_from_the_committed_manifests(client: TestClient):
    corpora = client.get("/api/health").json()["corpora"]
    assert {c["source"] for c in corpora} == {"healthcare_gov", "medicare_ncd", "medicare_pubs"}
    assert sum(c["documents"] for c in corpora) > 2000, corpora
    assert sum(c["chunks"] for c in corpora) > 6000, corpora


# ---------------------------------------------------------------- 2. chat -------------------


def test_chat_returns_two_reference_citations(client: TestClient):
    body = client.post("/api/chat", json={"message": "What is a deductible?"}).json()
    assert body["abstained"] is False
    assert [c["source_type"] for c in body["citations"]] == ["reference", "reference"]
    assert body["citations"][0]["url"] and body["citations"][1]["url"] is None, (
        "the two citations differ in shape on purpose, so CitationCard renders both branches"
    )
    assert body["citations"][0]["score"] is not None
    assert body["citations"][1]["score"] is None


def test_chat_trace_covers_every_kind(client: TestClient):
    trace = client.post("/api/chat", json={"message": "What is a deductible?"}).json()["trace"]
    kinds = [step["kind"] for step in trace]
    assert set(kinds) == {"plan", "tool_call", "tool_result", "synthesis"}, (
        f"the trace panel has an unrendered case: {kinds}"
    )
    bare = [s for s in trace if s["duration_ms"] is None and s["tokens"] is None]
    assert bare, "at least one step must leave every optional field unset"


def test_chat_claims_are_verbatim_substrings_of_the_answer(client: TestClient):
    body = client.post("/api/chat", json={"message": "What is a deductible?"}).json()
    for claim in body["claims"]:
        assert claim["text"] in body["answer"], (
            f"claim text is not a verbatim substring, which breaks Phase 4 highlighting: "
            f"{claim['text']!r}"
        )


def test_chat_markers_all_resolve(client: TestClient):
    body = client.post("/api/chat", json={"message": "What is a deductible?"}).json()
    known = {c["id"] for c in body["citations"]}
    markers = set(MARKER_RE.findall(body["answer"]))
    assert markers and markers <= known, f"orphan markers {markers - known}"


def test_chat_abstention_has_no_citations_but_keeps_its_trace(client: TestClient):
    body = client.post("/api/chat", json={"message": "please abstain"}).json()
    assert body["abstained"] is True
    assert body["citations"] == []
    assert body["claims"] == []
    assert not MARKER_RE.findall(body["answer"])
    assert body["trace"], "the trace is how you debug a wrong abstention; it must survive one"


def test_chat_all_lanes_variant_covers_every_source_type(client: TestClient):
    body = client.post("/api/chat", json={"message": "show me all lanes"}).json()
    assert [c["source_type"] for c in body["citations"]] == [
        "reference",
        "structured_api",
        "web",
    ], "the badge palette cannot be verified against data that never carries those lanes"


def test_chat_is_deterministic(client: TestClient):
    payload = {"message": "What is a deductible?", "plan_year": 2026}
    first = client.post("/api/chat", json=payload).json()
    second = client.post("/api/chat", json=payload).json()
    assert first == second


@pytest.mark.parametrize(
    "payload",
    [
        {"message": ""},
        {"message": "x" * 4001},
        {"message": "ok", "plan_year": 1999},
        {"message": "ok", "plan_year": 2999},
    ],
)
def test_chat_rejects_malformed_requests(client: TestClient, payload: dict):
    assert client.post("/api/chat", json=payload).status_code == 422


# ---------------------------------------------------------------- 3. streaming --------------


def test_stream_emits_the_documented_event_sequence(client: TestClient):
    response = client.post("/api/chat/stream", json={"message": "What is a deductible?"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse(response.text)
    types = [e["type"] for e in events]
    assert types[0] == "start"
    assert types[-1] == "done"
    assert types.count("step") == 4
    assert types.count("citation") == 2
    assert types.count("token") > 5, "the answer must arrive in pieces, not one blob"


def test_stream_done_payload_equals_the_non_streaming_response(client: TestClient):
    """Still two independent code paths for the stub, so equality is still assertable here. The
    agent has one path (`answer_question` drains `stream_answer`), asserted structurally in
    `tests/test_agent_stream.py`."""
    payload = {"message": "What is a deductible?", "plan_year": 2026}
    plain = client.post("/api/chat", json=payload).json()
    events = _parse_sse(client.post("/api/chat/stream", json=payload).text)
    done = next(e for e in events if e["type"] == "done")
    assert done["response"] == plain, (
        "the two paths must not drift — the whole point of `done` being authoritative"
    )


def test_stream_tokens_reconstruct_the_answer(client: TestClient):
    events = _parse_sse(client.post("/api/chat/stream", json={"message": "deductible?"}).text)
    streamed = "".join(e["delta"] for e in events if e["type"] == "token").strip()
    done = next(e for e in events if e["type"] == "done")
    assert streamed == done["response"]["answer"]


# ---------------------------------------------------------------- 3b. the agent path --------


def test_the_agent_answers_over_http(agent_client: TestClient):
    body = agent_client.post("/api/chat", json={"message": "What is a deductible?"}).json()
    assert body["abstained"] is False
    assert body["citations"], "an answer with no sources should never reach the browser"
    assert body["usage"]["model"], "the run must record which model produced it"
    assert set(MARKER_RE.findall(body["answer"])) <= {c["id"] for c in body["citations"]}


def test_the_agent_stream_uses_the_same_grammar_as_the_stub(agent_client: TestClient):
    """The UI has not changed since Phase 0 and must not need to. This is what that rests on."""
    response = agent_client.post("/api/chat/stream", json={"message": "What is a deductible?"})
    assert response.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse(response.text)
    types = [e["type"] for e in events]
    assert types[0] == "start"
    assert types[-1] == "done"
    assert "step" in types and "token" in types and "citation" in types

    done = next(e for e in events if e["type"] == "done")
    streamed = "".join(e["delta"] for e in events if e["type"] == "token")
    assert streamed == done["response"]["answer"]


def test_chat_is_503_when_the_corpus_was_never_chunked(no_corpus_client: TestClient):
    """Deliberately not a fallback to the stub: canned output must never be mistakable for a real
    answer, and a silently degraded answer is worse than an error that names the fix."""
    for path in ("/api/chat", "/api/chat/stream"):
        response = no_corpus_client.post(path, json={"message": "What is a deductible?"})
        assert response.status_code == 503, path
        assert "make chunk" in response.json()["detail"], path


def test_chat_is_503_when_the_plan_data_was_never_downloaded(no_plan_data_client: TestClient):
    """The third of the three refusals, and the newest: same shape as the missing corpus, naming
    its own command. Degrading to a reference-only answer would be the subtler version of falling
    back to the stub — real prose, measured against lanes nobody chose, with nothing saying so."""
    for path in ("/api/chat", "/api/chat/stream"):
        response = no_plan_data_client.post(path, json={"message": "deductible on plan X?"})
        assert response.status_code == 503, path
        assert "make puf" in response.json()["detail"], path


def test_an_agent_failure_arrives_as_an_error_frame(
    agent_kit, structured_store, monkeypatch: pytest.MonkeyPatch
):
    """A `StreamingResponse` has already sent its 200 by the time the first tool runs, so a later
    failure cannot become a status code — it would truncate the body and the browser would report a
    network error for what was really a step limit or a rate limit. `useChat.ts` already renders
    `ErrorEvent`; this is what feeds it."""
    _use_fixture_stores(monkeypatch, agent_kit, structured=structured_store)
    # A script that never produces a final answer, so the run trips its tool-call ceiling.
    with (
        build_agent(structured=True).override(model=agent_kit.script(agent_kit.SEARCH)),
        TestClient(create_app(dist_dir=Path("/nonexistent-dist"), stub=False)) as client,
    ):
        response = client.post("/api/chat/stream", json={"message": "loop forever"})

    assert response.status_code == 200
    events = _parse_sse(response.text)
    assert events[-1]["type"] == "error"
    assert events[-1]["code"] == "internal"


# ---------------------------------------------------------------- 4. corpus -----------------


def test_every_stub_citation_resolves(client: TestClient):
    response = stub_answer(ChatRequest(message="What is a deductible?"))
    for citation in response.citations:
        assert citation.doc_id, citation
        got = client.get(f"/api/corpus/{citation.doc_id}")
        assert got.status_code == 200, f"{citation.doc_id} does not resolve"
        assert citation.snippet in " ".join(got.json()["text"].split()), (
            f"{citation.id}'s snippet is not verbatim in its source document"
        )


def test_corpus_document_matches_a_gold_snippet(client: TestClient, gold: GoldSet):
    question = gold.in_corpus()[0]
    assert question.expected_snippet is not None
    body = client.get(f"/api/corpus/{question.expected_doc_ids[0]}").json()
    assert " ".join(question.expected_snippet.split()) in " ".join(body["text"].split())


def test_corpus_splits_shared_fields_from_per_source_meta(client: TestClient):
    body = client.get("/api/corpus/10050_p041").json()
    assert body["source"] == "medicare_pubs"
    assert body["meta"]["page"] == 41
    assert "text" not in body["meta"]


def test_corpus_unknown_id_is_404(client: TestClient):
    assert client.get("/api/corpus/no-such-document").status_code == 404


def test_corpus_path_traversal_is_not_served(client: TestClient):
    # docs/frontend_plan.md §8: ids resolve through the index, never onto a filesystem path.
    got = client.get("/api/corpus/..%2f..%2f..%2fetc%2fpasswd")
    assert got.status_code == 404, got.text


# ---------------------------------------------------------------- 5. evals ------------------


def test_evals_questions_matches_the_loader(client: TestClient, gold: GoldSet):
    body = client.get("/api/evals/questions").json()
    assert body["count"] == len(gold.questions)
    assert [q["id"] for q in body["questions"]] == [q.id for q in gold.questions]


def test_eval_run_completes_and_is_readable(client: TestClient, tmp_path: Path, monkeypatch):
    monkeypatch.setattr("health_coverage_navigator.evals.runner.EVAL_RUNS_DIR", tmp_path)

    started = client.post("/api/evals/runs")
    assert started.status_code == 202
    run_id = started.json()["run_id"]

    events = _parse_sse(client.get(f"/api/evals/runs/{run_id}/stream").text)
    types = [e["type"] for e in events]
    assert types[0] == "started"
    assert types[-1] == "finished", f"run did not finish: {types[-1]}"
    # Read from the loader rather than hardcoded: the set grows with the phases (35 at Phase 1b,
    # 40 once Phase 1-c added its structured questions), and a literal here would make adding a
    # gold question look like an API regression.
    expected = len(load_gold_set().questions)
    assert types.count("progress") == expected, "one progress event per gold question"
    # The dashboard renders a counter from these, so they have to arrive in order and exactly once
    # each. The route pins `max_concurrency=1` for this reason; without it the runner's semaphore
    # would let completions interleave and the counter would jump around.
    assert [e["completed"] for e in events if e["type"] == "progress"] == list(
        range(1, expected + 1)
    )

    run = events[-1]["run"]
    assert run["runner"] == "stub", "a metric measured against canned answers must say so"
    assert run["n_questions"] == expected
    assert set(run["metrics"]) >= {"recall@5", "mrr", "abstention_accuracy"}


def test_eval_stream_for_an_unknown_run_is_404(client: TestClient):
    assert client.get("/api/evals/runs/run_nope/stream").status_code == 404


def test_eval_run_detail_for_an_unknown_run_is_404(client: TestClient):
    assert client.get("/api/evals/runs/run_nope").status_code == 404


# ---------------------------------------------------------------- 6. static + SPA -----------


def _dist(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>SPA</title>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("export {};", encoding="utf-8")
    return dist


def test_spa_fallback_serves_index_for_a_client_route(tmp_path: Path):
    with TestClient(create_app(dist_dir=_dist(tmp_path))) as client:
        got = client.get("/evals")
        assert got.status_code == 200
        assert "<title>SPA</title>" in got.text, "React Router deep links must not 404"


def test_static_mount_does_not_shadow_the_api(tmp_path: Path):
    with TestClient(create_app(dist_dir=_dist(tmp_path))) as client:
        assert client.get("/api/health").json()["status"] == "ok"


@pytest.mark.parametrize("path", ["/assets/nope.js", "/assets/", "/assets"])
def test_missing_asset_is_404_not_index(tmp_path: Path, path: str):
    with TestClient(create_app(dist_dir=_dist(tmp_path))) as client:
        got = client.get(path)
        assert got.status_code == 404, (
            f"{path}: a missing build artifact must not be masked by index.html — the browser "
            f"would report a module parse error instead of a missing file"
        )


def test_missing_dist_explains_itself(tmp_path: Path):
    with TestClient(create_app(dist_dir=tmp_path / "never-built")) as client:
        got = client.get("/")
        assert got.status_code == 503
        assert "make dev" in got.text and "make serve" in got.text


def test_response_model_round_trips(client: TestClient):
    ChatResponse.model_validate(client.post("/api/chat", json={"message": "hello"}).json())
