"""AI integration and safety.

The real LLM is replaced by a fake `generate` function, so we can test exactly
what happens when the AI returns good JSON, bad JSON, unknown values,
destructive SQL, or fails completely.
"""
import json

import pytest

from app import interpreter
from app.config import settings
from app.db import get_readonly_connection
from app.llm import LLMError
from app.service import answer_question, inr
from app.sql_guard import UnsafeSQLError, execute_safe_sql


@pytest.fixture()
def fake_llm(monkeypatch, loaded_db):
    """Turn the LLM 'on' and control exactly what it returns."""
    monkeypatch.setattr(settings, "LLM_PROVIDER", "gemini")

    def use(reply):
        def generate(prompt, system, json_mode=False):
            if isinstance(reply, Exception):
                raise reply
            return reply if isinstance(reply, str) else json.dumps(reply)
        monkeypatch.setattr(interpreter, "generate", generate)
    return use


# ---------- LLM interpretation ----------

def test_llm_intent_is_used_and_numbers_come_from_sql(fake_llm):
    fake_llm({"type": "revenue_by", "dimension": "region", "order": "desc"})
    result = answer_question("Where did we sell best?")
    assert result.interpreted_by == "llm"
    assert result.supporting_data.rows[0][:2] == ["South", 411200.0]


def test_llm_json_inside_code_fences_is_accepted(fake_llm):
    fake_llm('```json\n{"type": "total_revenue"}\n```')
    assert answer_question("total?").supporting_data.rows[0][0] == 1439200.0


def test_llm_filter_value_not_in_data_is_refused(fake_llm):
    fake_llm({"type": "total_revenue", "filters": {"product": "Tablet"}})
    result = answer_question("revenue for tablets")
    assert result.status == "unsupported"
    assert "not a known product" in result.answer


def test_llm_value_case_is_corrected(fake_llm):
    fake_llm({"type": "total_revenue", "filters": {"region": "north"}})
    result = answer_question("north revenue")
    assert result.intent.filters == {"region": "North"}
    assert result.supporting_data.rows[0][0] == 384450.0


@pytest.mark.parametrize("bad_reply", [
    "Sure! The total revenue is 9,99,999.",                  # prose with an invented number
    '{"type": "drop_tables"}',                               # intent type not allowed
    '{"type": "compare_months", "months": ["May"]}',         # wrong month format
    LLMError("HTTP 503: model overloaded"),                  # provider down
])
def test_bad_llm_output_falls_back_to_rules(fake_llm, bad_reply):
    fake_llm(bad_reply)
    result = answer_question("What is the total revenue?")
    assert result.interpreted_by == "rules"
    assert result.supporting_data.rows[0][0] == 1439200.0    # never the invented number
    assert any("keyword rules were used" in w for w in result.warnings)


# ---------- Text-to-SQL ----------

def test_llm_custom_sql_select_runs(fake_llm):
    fake_llm({"type": "custom_sql",
              "sql": "SELECT sales_channel, AVG(quantity) AS avg_qty FROM revenue_lines GROUP BY sales_channel"})
    result = answer_question("average units per order by channel?")
    assert result.status == "answered"
    assert result.supporting_data.columns == ["sales_channel", "avg_qty"]
    assert any("AI-generated SQL" in w for w in result.warnings)


def test_llm_destructive_sql_is_blocked_and_data_survives(fake_llm):
    fake_llm({"type": "custom_sql", "sql": "DELETE FROM sales"})
    result = answer_question("clean up the data")
    assert result.status == "unsupported"
    assert any("rejected" in w for w in result.warnings)
    with get_readonly_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0] == 49


@pytest.mark.parametrize("sql", [
    "DELETE FROM sales",
    "DROP TABLE sales",
    "UPDATE sales SET quantity = 0",
    "SELECT 1; DROP TABLE sales",
    "SELECT * FROM sales -- sneaky comment",
    "PRAGMA table_info(sales)",
    "ATTACH DATABASE 'x.db' AS x",
    "SELECT * FROM rejected_rows",                       # table not allowed
    "SELECT name FROM sqlite_master",                    # schema snooping
    "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r) SELECT n FROM r",
    "",
])
def test_sql_guard_blocks_unsafe_queries(loaded_db, sql):
    with get_readonly_connection() as conn:
        with pytest.raises(UnsafeSQLError):
            execute_safe_sql(conn, sql)


def test_sql_guard_allows_reads_and_caps_rows(loaded_db):
    with get_readonly_connection() as conn:
        result = execute_safe_sql(conn, "SELECT * FROM sales;")
        assert len(result.rows) == 49
        result = execute_safe_sql(conn, "WITH t AS (SELECT region FROM revenue_lines) SELECT COUNT(*) FROM t")
        assert result.rows == [[48]]


# ---------- Formatting ----------

@pytest.mark.parametrize("value, text", [
    (1439200, "₹14,39,200"), (473000, "₹4,73,000"), (999, "₹999"), (-107250, "-₹1,07,250"), (1234.5, "₹1,234.50"),
])
def test_indian_currency_format(value, text):
    assert inr(value) == text
