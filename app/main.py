"""FastAPI app: HTTP API + the web page.

Endpoints
  GET  /                  -> simple web UI (app/static/index.html)
  GET  /health            -> is the app up, how many rows are loaded
  POST /api/ask           -> {"question": "..."} -> answer + supporting data
  GET  /api/data-quality  -> rows rejected while loading the CSV, and why
  GET  /api/examples      -> example questions
  GET  /docs              -> interactive API docs (Swagger, built into FastAPI)
"""
import json
import logging
from contextlib import asynccontextmanager, closing
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse

from app.db import get_readonly_connection
from app.ingest import load_csv
from app.schemas import AskRequest, AskResponse
from app.service import answer_question

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")
STATIC = Path(__file__).parent / "static"

EXAMPLES = [
    "What is the total revenue?",
    "Which product generated the highest revenue?",
    "What is the monthly revenue trend?",
    "Which region generated the highest sales?",
    "How did revenue change between April and May?",
    "Which product sold the most units?",
    "What is our profit margin?",
]


@asynccontextmanager
async def lifespan(_app: FastAPI):
    report = load_csv()  # rebuild the DB from the CSV on every start
    log.info("Startup: %d rows loaded, %d rejected", report.loaded_rows, len(report.rejected))
    yield


app = FastAPI(title="Mini AI Business Intelligence Assistant", version="1.0.0", lifespan=lifespan)


# ---------- Error handling: always clean JSON, never a stack trace ----------

@app.exception_handler(RequestValidationError)
async def validation_error(_req: Request, exc: RequestValidationError):
    messages = [f"{'.'.join(str(p) for p in e['loc'][1:]) or 'body'}: {e['msg']}" for e in exc.errors()]
    return JSONResponse(status_code=422, content={"error": "invalid_request", "details": messages})


@app.exception_handler(FileNotFoundError)
async def missing_data(_req: Request, exc: FileNotFoundError):
    log.error("Missing data: %s", exc)
    return JSONResponse(status_code=503, content={"error": "data_unavailable", "details": [str(exc)]})


@app.exception_handler(Exception)
async def unexpected_error(_req: Request, exc: Exception):
    log.exception("Unhandled error")
    return JSONResponse(status_code=500, content={"error": "internal_error",
                                                  "details": ["Something went wrong. Check the server log."]})


# ---------- Routes ----------

@app.get("/", include_in_schema=False)
def ui():
    return FileResponse(STATIC / "index.html")


@app.get("/health")
def health():
    with closing(get_readonly_connection()) as conn:  # closing() really closes it
        rows = conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0]
    return {"status": "ok", "rows_loaded": rows}


@app.post("/api/ask", response_model=AskResponse)
def ask(payload: AskRequest):
    log.info("Question: %s", payload.question)
    response = answer_question(payload.question)
    log.info("-> %s via %s: %s", response.intent.type, response.interpreted_by, response.answer[:120])
    return response


@app.get("/api/data-quality")
def data_quality():
    conn = get_readonly_connection()
    try:
        rejected = [{"csv_line": r["source_line"], "reason": r["reason"], "row": json.loads(r["raw_data"])}
                    for r in conn.execute("SELECT * FROM rejected_rows ORDER BY source_line")]
        statuses = {r[0]: r[1] for r in conn.execute("SELECT status, COUNT(*) FROM sales GROUP BY status")}
        loaded = conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0]
    finally:
        conn.close()
    return {"rows_loaded": loaded, "rows_rejected": len(rejected), "orders_by_status": statuses,
            "revenue_rule": "Completed orders only; revenue = quantity x unit_price - discount_amount",
            "rejected_rows": rejected}


@app.get("/api/examples")
def examples():
    return {"examples": EXAMPLES}
