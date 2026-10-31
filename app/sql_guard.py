"""Validation and safe execution for LLM-generated SQL (text-to-SQL).

Used only for questions that do not fit the built-in question types.
Four independent layers, so one mistake cannot let a bad query through:

1. Text checks   - exactly one statement, must start with SELECT/WITH,
                   no comments, no write/DDL keywords. Gives clear error messages.
2. Authorizer    - SQLite asks our callback before every action; we allow only
                   SELECT, reading the `sales` table / `revenue_lines` view, and
                   functions. Everything else (INSERT, DROP, PRAGMA, ATTACH,
                   reading other tables) is denied by the database engine.
3. Read-only DB  - the connection is opened with mode=ro, so writes are impossible.
4. Limits        - results capped at MAX_ROWS; a progress handler aborts slow queries.
"""
import re
import sqlite3

from app.schemas import QueryResult

MAX_ROWS = 200
MAX_VM_STEPS = 5_000_000  # rough cap on query work (stops runaway queries)
ALLOWED_TABLES = {"sales", "revenue_lines"}
FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|attach|detach|pragma|vacuum|"
    r"reindex|truncate|grant|load_extension)\b",
    re.IGNORECASE,
)


class UnsafeSQLError(ValueError):
    """The SQL failed validation and was not executed."""


def validate_sql(sql: str) -> str:
    if not sql or not sql.strip():
        raise UnsafeSQLError("empty SQL")
    cleaned = sql.strip().rstrip(";").strip()
    if ";" in cleaned:
        raise UnsafeSQLError("only one SQL statement is allowed")
    if "--" in cleaned or "/*" in cleaned:
        raise UnsafeSQLError("SQL comments are not allowed")
    if not re.match(r"^(select|with)\b", cleaned, re.IGNORECASE):
        raise UnsafeSQLError("only SELECT queries are allowed")
    bad = FORBIDDEN.search(cleaned)
    if bad:
        raise UnsafeSQLError(f"forbidden keyword: {bad.group(0).upper()}")
    return cleaned


def _authorizer(action, arg1, arg2, db_name, trigger_or_view):
    if action == sqlite3.SQLITE_SELECT:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_READ:
        # arg1 = table name. Reads that happen inside our own view are fine.
        if (arg1 or "").lower() in ALLOWED_TABLES or trigger_or_view == "revenue_lines":
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION:
        return sqlite3.SQLITE_DENY if (arg2 or "").lower() == "load_extension" else sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY  # INSERT, UPDATE, DELETE, DROP, PRAGMA, ATTACH, RECURSIVE ...


def execute_safe_sql(conn: sqlite3.Connection, sql: str) -> QueryResult:
    cleaned = validate_sql(sql)
    wrapped = f"SELECT * FROM ({cleaned}) LIMIT {MAX_ROWS}"
    steps = {"n": 0}

    def progress():
        steps["n"] += 1000
        return 1 if steps["n"] > MAX_VM_STEPS else 0  # non-zero = abort

    conn.set_authorizer(_authorizer)
    conn.set_progress_handler(progress, 1000)
    try:
        cursor = conn.execute(wrapped)
        columns = [d[0] for d in cursor.description]
        rows = [list(r) for r in cursor.fetchall()]
    except sqlite3.DatabaseError as e:
        raise UnsafeSQLError(f"query rejected by database: {e}") from e
    finally:
        conn.set_authorizer(None)
        conn.set_progress_handler(None, 0)
    return QueryResult(sql=cleaned, columns=columns, rows=rows)
