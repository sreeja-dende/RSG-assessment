"""Orchestration: question -> intent -> SQL -> answer.

The answer sentence is built from a template using the query results,
so every number in it is exactly what the database returned.
"""
import logging
from datetime import datetime

from app import analytics
from app.db import get_readonly_connection
from app.interpreter import interpret
from app.schemas import AskResponse, Intent, QueryResult
from app.sql_guard import UnsafeSQLError, execute_safe_sql

log = logging.getLogger("service")

SUPPORTED_HELP = ("Try questions like: 'What is the total revenue?', 'Which product generated the "
                  "highest revenue?', 'What is the monthly revenue trend?', 'Which region generated "
                  "the highest sales?', 'How did revenue change between April and May?'")


# ---------- Formatting ----------

def inr(amount) -> str:
    """Indian number format: 1439200 -> ₹14,39,200."""
    amount = float(amount or 0)
    sign = "-" if amount < 0 else ""
    whole, frac = f"{abs(amount):.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups) + "," + tail
    return f"{sign}₹{whole}" + ("" if frac == "00" else f".{frac}")


def month_label(ym: str) -> str:
    return datetime.strptime(ym, "%Y-%m").strftime("%B %Y")


def pct(new, old) -> str:
    if not old:
        return "n/a"
    return f"{(new - old) / old * 100:+.1f}%"


def scope_text(intent: Intent) -> str:
    parts = [f"{dim.replace('_', ' ')} {value}" for dim, value in intent.filters.items()]
    if intent.month:
        parts.append(month_label(intent.month))
    return f" for {', '.join(parts)}" if parts else ""


# ---------- Answer builders ----------

def answer_total(intent: Intent, r: QueryResult) -> str:
    total, units, orders = r.rows[0]
    if not orders:
        return f"There are no completed sales{scope_text(intent)}."
    return (f"Total revenue{scope_text(intent)} is {inr(total)} "
            f"from {orders} completed orders ({units} units).")


def answer_revenue_by(intent: Intent, r: QueryResult) -> str:
    if not r.rows:
        return f"There are no completed sales{scope_text(intent)}."
    label = intent.dimension.replace("_", " ")
    best_value = r.rows[0][1]
    leaders = [row[0] for row in r.rows if row[1] == best_value]
    word = "lowest" if intent.order == "asc" else "highest"
    total = sum(row[1] for row in r.rows)
    if intent.metric == "quantity":
        amount = f"{best_value} units"
        word = "fewest units sold" if intent.order == "asc" else "most units sold"
    else:
        amount = f"{inr(best_value)} ({best_value / total * 100:.1f}% of the total)" if total else inr(best_value)
        word = f"{word} revenue"
    who = " and ".join(leaders) + (" (tied)" if len(leaders) > 1 else "")
    return f"The {label} with the {word}{scope_text(intent)} is {who}: {amount}."


def answer_trend(intent: Intent, r: QueryResult) -> str:
    if not r.rows:
        return f"There are no completed sales{scope_text(intent)}."
    if len(r.rows) == 1:
        return f"Only one month has data{scope_text(intent)}: {month_label(r.rows[0][0])} with {inr(r.rows[0][1])}."
    first, last = r.rows[0], r.rows[-1]
    peak = max(r.rows, key=lambda row: row[1])
    low = min(r.rows, key=lambda row: row[1])
    changes = [row for row in r.rows if row[2] is not None]
    biggest_drop = min(changes, key=lambda row: row[2])
    biggest_rise = max(changes, key=lambda row: row[2])
    text = (f"Monthly revenue{scope_text(intent)} from {month_label(first[0])} to {month_label(last[0])}: "
            f"it peaked in {month_label(peak[0])} at {inr(peak[1])} and was lowest in "
            f"{month_label(low[0])} at {inr(low[1])}. ")
    if biggest_rise[2] > 0:
        text += f"The biggest rise was into {month_label(biggest_rise[0])} ({inr(biggest_rise[2])}). "
    if biggest_drop[2] < 0:
        text += f"The biggest drop was into {month_label(biggest_drop[0])} ({inr(biggest_drop[2])}). "
    text += f"Overall change from first to last month: {pct(last[1], first[1])}."
    return text


def answer_compare(intent: Intent, r: QueryResult) -> str:
    (m1, rev1, _), (m2, rev2, _) = r.rows[0], r.rows[1]
    diff = rev2 - rev1
    if diff == 0:
        direction = "stayed the same"
    else:
        direction = "increased" if diff > 0 else "decreased"
    text = (f"Revenue{scope_text(intent.model_copy(update={'month': None}))} {direction} from "
            f"{inr(rev1)} in {month_label(m1)} to {inr(rev2)} in {month_label(m2)}")
    if diff:
        text += f", a change of {'+' if diff > 0 else ''}{inr(diff)}"
        text += f" ({pct(rev2, rev1)})" if rev1 else " (no sales in the first month, so no % change)"
    return text + "."


def answer_custom(r: QueryResult) -> str:
    if not r.rows:
        return "The query returned no rows."
    if len(r.rows) == 1 and len(r.columns) == 1:
        return f"The answer is {r.rows[0][0]} ({r.columns[0]})."
    return f"The query returned {len(r.rows)} row(s); see the table below."


BUILDERS = {
    "total_revenue": answer_total,
    "revenue_by": answer_revenue_by,
    "monthly_trend": answer_trend,
    "compare_months": answer_compare,
}


# ---------- Main entry ----------

def answer_question(question: str, db_path: str | None = None) -> AskResponse:
    conn = get_readonly_connection(db_path)
    try:
        values = analytics.available_values(conn)
        intent, source, warnings = interpret(question, values)

        if intent.type == "unsupported":
            return AskResponse(question=question, status="unsupported", intent=intent,
                               interpreted_by=source, warnings=warnings,
                               answer=f"Sorry, I can't answer that. {intent.reason or ''} {SUPPORTED_HELP}".strip())

        if intent.type == "custom_sql":
            try:
                result = execute_safe_sql(conn, intent.sql)
            except UnsafeSQLError as e:
                log.warning("Rejected AI-generated SQL: %s | %s", e, intent.sql)
                return AskResponse(question=question, status="unsupported", intent=intent,
                                   interpreted_by=source, warnings=warnings + [f"Generated SQL was rejected: {e}"],
                                   answer=f"Sorry, I couldn't answer that safely. {SUPPORTED_HELP}")
            warnings.append("This answer used AI-generated SQL (validated, read-only). Check the SQL shown.")
            return AskResponse(question=question, status="answered", intent=intent, interpreted_by=source,
                               answer=answer_custom(result), supporting_data=result, warnings=warnings)

        result = analytics.QUERIES[intent.type](conn, intent)
        answer = BUILDERS[intent.type](intent, result)
        excluded = conn.execute("SELECT COUNT(*) FROM sales WHERE status != 'Completed'").fetchone()[0]
        rejected = conn.execute("SELECT COUNT(*) FROM rejected_rows").fetchone()[0]
        if excluded or rejected:
            warnings.append(f"Revenue counts completed orders only, net of discounts. Excluded: "
                            f"{excluded} non-completed order(s) and {rejected} invalid CSV row(s) "
                            f"(see /api/data-quality).")
        return AskResponse(question=question, status="answered", answer=answer, intent=intent,
                           interpreted_by=source, supporting_data=result, warnings=warnings)
    finally:
        conn.close()
