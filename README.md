# <Project Name> — RSG Technical Assessment

> Replace this template tomorrow with the real assignment details.

## 1. Problem
<One paragraph: what the assignment asked for, in my own words.>

## 2. What I built
- REST API (FastAPI) with SQLite database (SQLAlchemy ORM)
- AI feature: <RAG over documents / agent with tools / ...>
- Tests with pytest (22+ tests, LLM mocked so tests run offline)

## 3. Setup and run
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    Mac/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # add your GEMINI_API_KEY, set LLM_PROVIDER=gemini
uvicorn app.main:app --reload
```
Open http://127.0.0.1:8000/docs to try every endpoint.

Run tests: `pytest -v`

## 4. Architecture
```
Client -> FastAPI routes (main.py) -> validation (schemas.py)
                 |                         
                 +-> DB (db.py, models.py: documents, chunks, query_logs)
                 +-> Retrieval (rag.py: chunking + BM25)
                 +-> LLM wrapper (llm.py: provider switch, timeout, retry)
                 +-> Agent loop (agent.py: tools, JSON actions, step limit)
```

## 5. Database design
| Table | Purpose | Key columns |
|---|---|---|
| documents | source content | id, title, content, created_at |
| chunks | searchable pieces | id, document_id (FK, cascade delete), position, text |
| query_logs | audit trail of AI answers | id, question, answer, source_chunk_ids, mode |

## 6. API
| Method | Path | Purpose |
|---|---|---|
| GET | /health | liveness |
| POST | /documents | add + chunk a document |
| GET | /documents, /documents/{id} | read |
| DELETE | /documents/{id} | delete (cascades chunks) |
| POST | /ask | RAG answer with cited sources |
| POST | /agent | agent answer with step trace |
| GET | /logs | recent Q&A history |

## 7. How I used AI
- **In the product:** <where the LLM is used, why, how output is constrained (JSON, context-only prompt)>
- **Safeguards:** answer only from retrieved context; skip LLM when nothing is retrieved; timeouts and retries; safe calculator instead of eval(); agent step limit.
- **While coding:** <honest note, e.g. "used an AI assistant for boilerplate; I reviewed and can explain every line">

## 8. Testing and error handling
- Unit tests for chunking, BM25, agent loop (bad JSON, unknown tool, crashing tool, step limit)
- API tests for success, 422 validation, 404 not found, 503 LLM failure
- Global handlers return clean JSON errors, never stack traces

## 9. What works / what doesn't
| Feature | Status | Notes |
|---|---|---|
| <feature> | ✅ Works | |
| <feature> | ⚠️ Partial | <why> |
| <feature> | ❌ Not done | <why, and how I would do it> |

## 10. With more time
- Hybrid retrieval (BM25 + embeddings) for synonym matching
- Auth, rate limiting, async LLM calls, Docker, CI pipeline
