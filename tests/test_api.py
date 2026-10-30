"""API tests: happy paths, validation errors, not-found, and LLM failure."""
from app import main
from app.llm import LLMError

DOC = {
    "title": "HR Policy",
    "content": "Employees get 24 days of paid leave per year. "
               "Refunds for travel are processed within 7 working days.",
}


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_create_and_get_document(client):
    r = client.post("/documents", json=DOC)
    assert r.status_code == 201
    body = r.json()
    assert body["title"] == "HR Policy" and body["chunk_count"] == 1

    assert client.get(f"/documents/{body['id']}").status_code == 200
    assert len(client.get("/documents").json()) == 1


def test_create_document_validation(client):
    assert client.post("/documents", json={"title": "", "content": "x"}).status_code == 422
    assert client.post("/documents", json={"title": "t"}).status_code == 422
    assert client.post("/documents", json={"title": "t", "content": "   "}).status_code == 422


def test_get_and_delete_missing_document(client):
    assert client.get("/documents/999").status_code == 404
    assert client.delete("/documents/999").status_code == 404


def test_delete_cascades_chunks(client):
    doc_id = client.post("/documents", json=DOC).json()["id"]
    assert client.delete(f"/documents/{doc_id}").status_code == 204
    r = client.post("/ask", json={"question": "paid leave days"})
    assert r.json()["sources"] == []  # chunks gone with the document


def test_ask_returns_sources_and_logs(client):
    client.post("/documents", json=DOC)
    r = client.post("/ask", json={"question": "How many days of paid leave?"})
    assert r.status_code == 200
    body = r.json()
    assert body["sources"] and body["mode"] == "rag"
    assert client.get("/logs").json()[0]["question"] == "How many days of paid leave?"


def test_ask_without_matching_docs_skips_llm(client, monkeypatch):
    def should_not_be_called(*a, **k):
        raise AssertionError("LLM called with no context")
    monkeypatch.setattr(main, "generate", should_not_be_called)
    r = client.post("/ask", json={"question": "quantum physics?"})
    assert r.json()["answer"] == "I don't know based on the documents."


def test_ask_validation(client):
    assert client.post("/ask", json={"question": "hi"}).status_code == 422  # too short
    assert client.post("/ask", json={"question": "valid q", "top_k": 50}).status_code == 422


def test_llm_failure_returns_503(client, monkeypatch):
    client.post("/documents", json=DOC)

    def fail(*a, **k):
        raise LLMError("timeout")
    monkeypatch.setattr(main, "generate", fail)
    r = client.post("/ask", json={"question": "paid leave days?"})
    assert r.status_code == 503


def test_agent_endpoint(client):
    r = client.post("/agent", json={"question": "What is 2+2?"})
    assert r.status_code == 200
    assert "answer" in r.json() and "steps" in r.json()
