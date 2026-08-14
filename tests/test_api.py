"""End-to-end checks on the HTTP surface, against the Phase 0 stub.

There is no agent yet, so what is testable here is the *contract* and the *plumbing*: that the
stub exercises every UI branch, that the streaming and non-streaming paths cannot drift apart,
that citation drill-down resolves against the real corpus, and that the static mount does not eat
the API. That last one is the reason `create_app` takes a `dist_dir` — it is an order-dependent
failure whose only symptom is the wrong response body.

Structural invariants of the models themselves (dangling citation ids, orphan markers) are
enforced by the pydantic validator in `api/models.py` and tested in `tests/test_contract.py`.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from health_coverage_navigator.api.app import create_app
from health_coverage_navigator.api.models import MARKER_RE, ChatRequest, ChatResponse
from health_coverage_navigator.api.stub import stub_answer
from health_coverage_navigator.evals.loader import load_gold_set
from health_coverage_navigator.evals.models import GoldSet


@pytest.fixture(scope="session")
def client():
    with TestClient(create_app(dist_dir=Path("/nonexistent-dist"))) as c:
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
    assert body["stub"] is True, "Phase 0 answers are canned; the UI must be able to say so"

    lanes = {lane["source_type"]: lane for lane in body["lanes"]}
    assert set(lanes) == {"reference", "structured_api", "web"}, (
        "the three-lane vocabulary exists from Phase 0 even though two lanes are empty"
    )
    assert lanes["reference"]["configured"] is True
    assert lanes["structured_api"]["configured"] is False
    assert lanes["web"]["configured"] is False


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
    assert types.count("progress") == 35, "one progress event per gold question"

    run = events[-1]["run"]
    assert run["runner"] == "stub", "a metric measured against canned answers must say so"
    assert run["n_questions"] == 35
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
