import json
import time

import pytest

from app.services.llm import LLMError, LLMOutputError, parse_output
from tests.conftest import FakeLLM

Q = {"question": "Can I book a cab on Saturday?"}


def ask(client, headers, body=Q, user="acme_employee"):
    return client.post("/questions", json=body, headers=headers[user])


def raw(answerable=True, answer="ok", ids=None):
    return json.dumps({"answerable": answerable, "answer": answer, "supporting_chunk_ids": ids})


def test_grounded_answer_has_citation_from_stored_metadata(client, headers, container):
    r = ask(client, headers)
    body = r.json()
    assert r.status_code == 200 and body["status"] == "answered" and body["policy_version"] == "v1"
    assert body["citations"] == [{
        "document": "acme_transport_policy_v1.md", "section": "6. Weekend and Holiday Travel",
        "chunk_id": "acme-v1-05-weekend-and-holiday-travel"}]
    assert body["meta"]["llm_calls"] == 1 and body["request_id"].startswith("req_")


def test_citation_text_invented_by_model_is_ignored(client, headers, container):
    container.llm.responder = lambda q, ev: raw(True, "See Northstar policy section 99, page 4",
                                                [ev[0].chunk_id])
    c = ask(client, headers).json()["citations"][0]
    assert c["document"] == "acme_transport_policy_v1.md" and c["section"].startswith("6.")


def test_no_relevant_evidence_means_not_covered_with_zero_llm_calls(client, headers, container):
    r = ask(client, headers, {"question": "quantum chromodynamics banana smoothie"})
    body = r.json()
    assert r.status_code == 200 and body["status"] == "not_covered" and body["citations"] == []
    assert "transport desk" in body["answer"]
    assert container.llm.calls == [] and body["meta"]["llm_calls"] == 0


def test_model_says_not_answerable_gives_not_covered(client, headers, container):
    container.llm.responder = lambda q, ev: raw(False, "", [])
    body = ask(client, headers).json()
    assert body["status"] == "not_covered" and "transport desk" in body["answer"] and body["citations"] == []


@pytest.mark.parametrize("bad_output", [
    "this is not json",
    "",
    "[]",
    json.dumps({"answer": "no answerable flag", "supporting_chunk_ids": ["x"]}),
    json.dumps({"answerable": "yes", "answer": "a", "supporting_chunk_ids": ["x"]}),
    json.dumps({"answerable": True, "answer": "", "supporting_chunk_ids": ["x"]}),
    json.dumps({"answerable": True, "answer": "a" * 5000, "supporting_chunk_ids": ["x"]}),
    json.dumps({"answerable": True, "answer": "no citations", "supporting_chunk_ids": []}),
    json.dumps({"answerable": True, "answer": "bad ids type", "supporting_chunk_ids": "abc"}),
    None,
    123,
])
def test_malformed_model_output_fails_closed(client, headers, container, bad_output):
    container.llm.responder = lambda q, ev: bad_output
    r = ask(client, headers)
    body = r.json()
    assert r.status_code == 503 and body["status"] == "retry" and body["citations"] == []
    assert "try again" in body["answer"].lower()


def test_invented_chunk_id_is_rejected(client, headers, container):
    container.llm.responder = lambda q, ev: raw(True, "made up", ["northstar-v1-01-night-shift-and-late-drops"])
    body = ask(client, headers).json()
    assert body["status"] == "retry" and body["citations"] == []


def test_more_than_three_ids_is_rejected_and_never_cited():
    with pytest.raises(LLMOutputError):
        parse_output(raw(True, "x", ["a", "b", "c", "d"]), {"a", "b", "c", "d"})


def test_duplicate_ids_are_collapsed():
    assert parse_output(raw(True, "x", ["a", "a"]), {"a"}).supporting_chunk_ids == ["a"]


def test_json_in_code_fence_is_tolerated():
    out = parse_output("```json\n" + raw(True, "x", ["a"]) + "\n```", {"a"})
    assert out.answerable and out.answer == "x"


def test_citations_never_exceed_three(client, headers, container):
    container.llm.responder = lambda q, ev: raw(True, "all of them", [e.chunk_id for e in ev])
    body = ask(client, headers, {"question": "escort saturday airport approval manager hours booking"}).json()
    assert body["status"] == "answered" and 1 <= len(body["citations"]) <= 3


def test_llm_provider_error_gives_try_again(client, headers, container):
    def boom(q, ev):
        raise LLMError("HTTP 429 quota exceeded")

    container.llm.responder = boom
    r = ask(client, headers)
    assert r.status_code == 503 and r.json()["status"] == "retry" and r.headers["retry-after"]
    assert r.json()["meta"]["llm_calls"] == 1  # exactly one attempt, no retry storm


def test_unexpected_exception_in_llm_gives_try_again(client, headers, container):
    container.llm.responder = lambda q, ev: 1 / 0
    assert ask(client, headers).json()["status"] == "retry"


def test_llm_timeout_returns_quickly_not_a_hang(make_container, client_for):
    c = make_container(llm=FakeLLM(lambda q, ev: time.sleep(3) or raw(True, "late", [ev[0].chunk_id])),
                       llm_timeout_seconds=0.2)
    client = client_for(c)
    from tests.conftest import login
    h = login(client, "acme_employee")
    t = time.monotonic()
    r = client.post("/questions", json=Q, headers=h)
    assert time.monotonic() - t < 1.5
    assert r.status_code == 503 and r.json()["status"] == "retry" and r.json()["citations"] == []


def test_retrieval_timeout_returns_quickly(make_container, client_for, monkeypatch):
    c = make_container(retrieval_timeout_seconds=0.2)
    monkeypatch.setattr(c.retriever, "retrieve", lambda *a: time.sleep(3))
    client = client_for(c)
    from tests.conftest import login
    h = login(client, "acme_employee")
    t = time.monotonic()
    r = client.post("/questions", json=Q, headers=h)
    assert time.monotonic() - t < 1.5
    assert r.status_code == 503 and r.json()["status"] == "retry"
    assert c.llm.calls == []


def test_vector_store_down_gives_try_again(client, headers, container, monkeypatch):
    monkeypatch.setattr(container.store, "query", lambda *a: (_ for _ in ()).throw(ConnectionError("chroma down")))
    r = ask(client, headers)
    assert r.status_code == 503 and r.json()["status"] == "retry" and container.llm.calls == []


def test_metadata_db_failure_gives_try_again(client, headers, container, monkeypatch):
    monkeypatch.setattr(container.db, "get_active_version", lambda c: (_ for _ in ()).throw(RuntimeError("db")))
    assert ask(client, headers).json()["status"] == "retry"


def test_company_without_active_policy_is_not_covered(make_container, client_for):
    c = make_container(seed_data=False)
    c.db.add_company("acme", "Acme")
    c.db.add_user("acme_employee", "demo-pass", "acme", "employee")
    client = client_for(c)
    from tests.conftest import login
    r = client.post("/questions", json=Q, headers=login(client, "acme_employee"))
    assert r.json()["status"] == "not_covered" and c.llm.calls == []


@pytest.mark.parametrize("body", [{}, {"question": ""}, {"question": "   "}, {"question": "x" * 501},
                                  {"question": 5}, {"q": "hi"}])
def test_bad_question_input_is_422(client, headers, body):
    assert client.post("/questions", json=body, headers=headers["acme_employee"]).status_code == 422


def test_llm_never_called_more_than_twice(client, headers, container):
    for q in ["escort", "saturday", "airport", "no-show", "booking hours"]:
        assert ask(client, headers, {"question": q}).json()["meta"]["llm_calls"] <= 2
    assert all(True for _ in container.llm.calls) and len(container.llm.calls) <= 5


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}
