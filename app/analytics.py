"""Deterministic analytics: one parameterized SQL query per question type.

Every number shown to the user comes from here, never from the LLM.

Safety: user/LLM values (region names, months) are passed as SQL
parameters (?), never pasted into the SQL string. Column names cannot be
parameters, so they are looked up in a fixed whitelist (DIMENSION_COLUMNS).
"""
import sqlite3
import textwrap

from app.schemas import Intent, QueryResult

# Whitelist: the ONLY column names that can ever appear in generated SQL.
DIMENSION_COLUMNS = {
    "product": "product",
    "category": "category",
    "region": "region",
    "sales_channel": "sales_channel",
}
METRIC_SQL = {
    "revenue": "ROUND(SUM(net_revenue), 2)",
    "quantity": "SUM(quantity)",
}


def _where(intent: Intent) -> tuple[str, list]:
    clauses, params = [], []
    for dim, value in intent.filters.items():
        clauses.append(f"{DIMENSION_COLUMNS[dim]} = ? COLLATE NOCASE")
        params.append(value)
    if intent.month:
        clauses.append("month = ?")
        params.append(intent.month)
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


def _run(conn: sqlite3.Connection, sql: str, params: list) -> QueryResult:
    cursor = conn.execute(sql, params)
    columns = [d[0] for d in cursor.description]
    rows = [list(r) for r in cursor.fetchall()]
    return QueryResult(sql=textwrap.dedent(sql).strip(), params=params, columns=columns, rows=rows)


def total_revenue(conn, intent: Intent) -> QueryResult:
    where, params = _where(intent)
    sql = f"""
        SELECT {METRIC_SQL['revenue']} AS total_revenue,
               SUM(quantity)           AS units_sold,
               COUNT(*)                AS orders
        FROM revenue_lines{where}"""
    return _run(conn, sql, params)


def revenue_by(conn, intent: Intent) -> QueryResult:
    col = DIMENSION_COLUMNS[intent.dimension or "product"]
    metric = METRIC_SQL[intent.metric]
    order = "DESC" if intent.order == "desc" else "ASC"
    where, params = _where(intent)
    sql = f"""
        SELECT {col}, {metric} AS {intent.metric}, COUNT(*) AS orders
        FROM revenue_lines{where}
        GROUP BY {col}
        ORDER BY {intent.metric} {order}, {col}"""
    return _run(conn, sql, params)


def monthly_trend(conn, intent: Intent) -> QueryResult:
    where, params = _where(intent)
    # LAG() = value from the previous row -> month-over-month change in SQL itself
    sql = f"""
        SELECT month,
               ROUND(SUM(net_revenue), 2) AS revenue,
               ROUND(SUM(net_revenue) - LAG(SUM(net_revenue)) OVER (ORDER BY month), 2)
                   AS change_from_previous
        FROM revenue_lines{where}
        GROUP BY month
        ORDER BY month"""
    return _run(conn, sql, params)


def compare_months(conn, intent: Intent) -> QueryResult:
    first, second = intent.months[0], intent.months[1]
    filtered = intent.model_copy(update={"month": None})
    where, params = _where(filtered)
    extra = where.replace(" WHERE ", " AND ") if where else ""
    sql = f"""
        SELECT month, ROUND(SUM(net_revenue), 2) AS revenue, COUNT(*) AS orders
        FROM revenue_lines
        WHERE month IN (?, ?){extra}
        GROUP BY month
        ORDER BY month"""
    result = _run(conn, sql, [first, second, *params])
    # A month with no sales returns no row -> add it explicitly with 0
    found = {r[0] for r in result.rows}
    for m in (first, second):
        if m not in found:
            result.rows.append([m, 0, 0])
    order = {first: 0, second: 1}
    result.rows.sort(key=lambda r: order[r[0]])
    return result


def available_values(conn) -> dict[str, list[str]]:
    """Distinct values per dimension + months present. Used to validate AI output."""
    values = {
        dim: [r[0] for r in conn.execute(f"SELECT DISTINCT {col} FROM sales ORDER BY 1")]
        for dim, col in DIMENSION_COLUMNS.items()
    }
    values["months"] = [r[0] for r in conn.execute("SELECT DISTINCT month FROM revenue_lines ORDER BY 1")]
    return values


QUERIES = {
    "total_revenue": total_revenue,
    "revenue_by": revenue_by,
    "monthly_trend": monthly_trend,
    "compare_months": compare_months,
}
