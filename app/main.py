"""FastAPI app: the HTTP API.

Endpoints
  GET    /health                 -> is the app up?
  POST   /documents              -> add a document (auto-chunked)
  GET    /documents              -> list documents
  GET    /documents/{id}         -> one document
  DELETE /documents/{id}         -> delete (chunks deleted by cascade)
  POST   /ask                    -> RAG answer with sources
  POST   /agent                  -> agent answer with the steps it took
  GET    /logs                   -> recent question/answer history

Open http://127.0.0.1:8000/docs for interactive Swagger UI (free "UI").
"""
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import rag
from app.agent import build_tools, run_agent
from app.db import SessionLocal, get_db, init_db
from app.llm import LLMError, generate
from app.models import Chunk, Document, QueryLog
from app.schemas import (AgentResponse, AskRequest, AskResponse, DocumentCreate,
                         DocumentOut, Source)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="RSG Assessment Starter", version="1.0.0", lifespan=lifespan)


# ---------- Error handling: clean JSON errors, never a raw stack trace ----------

@app.exception_handler(LLMError)
async def llm_error_handler(_req: Request, exc: LLMError):
    log.error("LLM failure: %s", exc)
    return JSONResponse(status_code=503, content={"detail": "AI service unavailable, try again."})


@app.exception_handler(Exception)
async def unhandled_error_handler(_req: Request, exc: Exception):
    log.exception("Unhandled error")
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


# ---------- Helpers ----------

def _to_out(doc: Document, db: Session) -> DocumentOut:
    count = db.scalar(select(func.count(Chunk.id)).where(Chunk.document_id == doc.id))
    return DocumentOut(id=doc.id, title=doc.title, created_at=doc.created_at, chunk_count=count or 0)


def _retrieve(db: Session, question: str, top_k: int) -> list[tuple[Chunk, float]]:
    rows = db.execute(select(Chunk.id, Chunk.text)).all()
    ranked = rag.bm25_search(question, [(r.id, r.text) for r in rows], top_k=top_k)
    by_id = {c.id: c for c in db.scalars(select(Chunk).where(Chunk.id.in_([i for i, _ in ranked])))}
    return [(by_id[i], s) for i, s in ranked]


# ---------- Routes ----------

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/documents", response_model=DocumentOut, status_code=201)
def create_document(payload: DocumentCreate, db: Session = Depends(get_db)):
    pieces = rag.chunk_text(payload.content)
    if not pieces:
        raise HTTPException(status_code=422, detail="Content has no words")
    doc = Document(title=payload.title.strip(), content=payload.content)
    doc.chunks = [Chunk(position=i, text=t) for i, t in enumerate(pieces)]
    db.add(doc)
    db.commit()  # one transaction: document + chunks saved together, or not at all
    db.refresh(doc)
    log.info("Created document %s with %d chunks", doc.id, len(pieces))
    return _to_out(doc, db)


@app.get("/documents", response_model=list[DocumentOut])
def list_documents(skip: int = 0, limit: int = 50, db: Session = Depends(get_db)):
    limit = max(1, min(limit, 100))
    docs = db.scalars(select(Document).order_by(Document.id).offset(max(skip, 0)).limit(limit)).all()
    return [_to_out(d, db) for d in docs]


@app.get("/documents/{doc_id}", response_model=DocumentOut)
def get_document(doc_id: int, db: Session = Depends(get_db)):
    doc = db.get(Document, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return _to_out(doc, db)


@app.delete("/documents/{doc_id}", status_code=204)
def delete_document(doc_id: int, db: Session = Depends(get_db)):
    doc = db.get(Document, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    db.delete(doc)
    db.commit()


@app.post("/ask", response_model=AskResponse)
def ask(payload: AskRequest, db: Session = Depends(get_db)):
    hits = _retrieve(db, payload.question, payload.top_k)
    if not hits:
        # Don't call the LLM with no context -> saves cost, avoids hallucination
        answer = "I don't know based on the documents."
    else:
        prompt = rag.build_prompt(payload.question, [(c.id, c.text) for c, _ in hits])
        answer = generate(prompt, system=rag.RAG_SYSTEM)

    sources = [Source(chunk_id=c.id, document_id=c.document_id, score=s, preview=c.text[:150])
               for c, s in hits]
    db.add(QueryLog(question=payload.question, answer=answer, mode="rag",
                    source_chunk_ids=",".join(str(c.id) for c, _ in hits)))
    db.commit()
    return AskResponse(answer=answer, sources=sources, mode="rag")


@app.post("/agent", response_model=AgentResponse)
def agent(payload: AskRequest):
    def search_fn(query: str) -> str:
        # Own session: tools may run outside the request's DB lifecycle
        with SessionLocal() as s:
            hits = _retrieve(s, query, 3)
            return "\n".join(f"[chunk {c.id}] {c.text[:400]}" for c, _ in hits) or "no results"

    answer, steps = run_agent(payload.question, build_tools(search_fn))
    with SessionLocal() as s:
        s.add(QueryLog(question=payload.question, answer=answer, mode="agent"))
        s.commit()
    return AgentResponse(answer=answer, steps=steps)


@app.get("/logs")
def logs(limit: int = 20, db: Session = Depends(get_db)):
    rows = db.scalars(select(QueryLog).order_by(QueryLog.id.desc()).limit(max(1, min(limit, 100)))).all()
    return [{"id": r.id, "mode": r.mode, "question": r.question, "answer": r.answer,
             "sources": r.source_chunk_ids, "at": r.created_at} for r in rows]
