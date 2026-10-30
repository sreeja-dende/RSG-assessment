# Study Guide — read tonight, keep open tomorrow

## A. Tonight's checklist (on YOUR laptop)
| # | Do this | Check it worked |
|---|---|---|
| 1 | Install **Python 3.11+** (tick "Add to PATH" on Windows) | `python --version` |
| 2 | Install **Git**, and log in to **GitHub** in the browser | `git --version` |
| 3 | Install **VS Code** + Python extension | opens |
| 4 | Get a free **Gemini API key**: aistudio.google.com → Get API key | key copied |
| 5 | Unzip this project, open terminal inside it | `ls` shows app/ tests/ |
| 6 | `python -m venv .venv` then activate (Windows: `.venv\Scripts\activate`) | prompt shows (.venv) |
| 7 | `pip install -r requirements.txt` | no red errors |
| 8 | `pytest -v` | **22 passed** |
| 9 | Copy `.env.example` → `.env`, set `LLM_PROVIDER=gemini` and paste key | file saved |
| 10 | `uvicorn app.main:app --reload` → open http://127.0.0.1:8000/docs | Swagger page loads |
| 11 | In Swagger: POST /documents, then POST /ask | real Gemini answer appears |
| 12 | Create an empty GitHub repo (`rsg-assessment`), keep it private until you submit | repo URL ready |

If step 8 or 11 fails, send me the exact error text.

## B. What each file does (say this in the interview)
| File | One-line explanation |
|---|---|
| `config.py` | Reads settings from `.env` so keys never live in code |
| `db.py` | Database connection; one session per request, always closed |
| `models.py` | Three tables: documents → chunks (one-to-many, cascade delete), query_logs |
| `schemas.py` | Pydantic validates input → bad input gets 422, not a crash |
| `llm.py` | One `generate()` for any provider; timeout, retry with backoff, mock mode |
| `rag.py` | Chunk text with overlap, rank chunks with BM25, build a grounded prompt |
| `agent.py` | ReAct loop: LLM picks a tool as JSON, we run it, feed back result, max 5 steps |
| `main.py` | API routes + global error handlers |
| `tests/` | 22 tests; LLM is mocked so tests are free, fast, offline |

## C. Design decisions you must be able to defend
- **Why SQLite?** Zero setup, fine for an assessment. Swap `DATABASE_URL` for Postgres in production — no code change because of SQLAlchemy.
- **Why separate chunks table?** Retrieval works on small pieces; also lets us cite exactly which piece the answer came from.
- **Why log queries?** Traceability: we can see what the AI answered and which sources it used.
- **Why skip the LLM when nothing is retrieved?** Saves money and prevents made-up answers.
- **Why not `eval()` in the calculator?** LLM output is untrusted; `eval` could run any code. We parse with `ast` and allow only math.
- **Why a step limit in the agent?** Without it, a confused LLM could loop forever and burn API credits.
- **Why mock the LLM in tests?** Tests must be deterministic, free, and run without internet.
- **Why temperature 0.2?** Lower randomness → more consistent, factual answers.

## D. Concepts (short answers)
**Generative AI** — models that generate new content (text, images) by predicting the next token.
**LLM** — a large neural network trained on text. Key terms: token, context window, temperature, hallucination.
**RAG** — retrieve relevant text, put it in the prompt, LLM answers from it. Fixes outdated/private knowledge and reduces hallucination.
**Vector RAG** — turn chunks into embeddings (number vectors), find nearest by meaning. Catches synonyms. Needs embedding model + vector DB (FAISS, Chroma, pgvector).
**Vectorless RAG** — retrieve without embeddings: BM25 keyword scoring (what we use), SQL queries, or letting the LLM navigate a document's table of contents (tree search, e.g. PageIndex). Cheaper, explainable, great for structured docs; weaker on synonyms.
**Hybrid** — BM25 + vectors combined; common in production.
**Agentic AI** — the LLM plans, chooses tools, observes results, and loops until done (ReAct = Reason + Act).
**Tool / function calling** — the LLM outputs a structured request (JSON) for a function; your code runs it.
**LangChain** — Python framework with ready-made chains, retrievers, agents, tool wrappers.
**Google ADK** — Google's Agent Development Kit: define agents with tools, supports multi-agent setups, works well with Gemini.
**Dify** — open-source low-code platform to build LLM apps and RAG/agent workflows visually.
*Why I didn't use a framework:* the brief says none required; writing the loop myself shows I understand what frameworks do underneath.

## E. Likely interview "modify it live" requests — practise each once
1. Add a field (e.g. `category`) to documents → models.py + schemas.py + delete app.db.
2. Add a new agent tool → write a function, add one line in `build_tools`.
3. Change chunk size → `chunk_text(max_words=...)` and re-run tests.
4. Add a search filter by document id → add param to `/ask`, filter in `_retrieve`.
5. Add a test → copy an existing one and change the input/expected output.

## F. Tomorrow's flow with me
1. 9 AM: paste me the assignment exactly as sent (and any attachments).
2. I break it into requirements + a plan, and we adapt this starter.
3. At every stage I tell you **what** we did and **why**, in interview language.
4. You run it on your laptop after each stage (catches problems early).
5. Final: README filled honestly, tests green, push to GitHub, submit.
