"""End-to-end through the API: valid questions, invalid questions, edge cases.

Expected numbers were calculated independently with pandas from the CSV
(48 valid completed orders, revenue = quantity * unit_price - discount).
"""
import pytest

from tests.conftest import ask

# ---------- The five questions from the assignment ----------


def test_total_revenue(client):
    data = ask(client, "What is the total revenue?")
    assert data["status"] == "answered"
    assert data["supporting_data"]["rows"][0] == [1439200.0, 633, 48]
    assert "₹14,39,200" in data["answer"]


def test_highest_revenue_product(client):
    data = ask(client, "Which product generated the highest revenue?")
    rows = data["supporting_data"]["rows"]
    assert rows[0][:2] == ["Monitor", 473000.0]
    assert len(rows) == 7                       # full ranking shown as supporting data
    assert "Monitor" in data["answer"]


def test_monthly_trend(client):
    data = ask(client, "What is the monthly revenue trend?")
    rows = data["supporting_data"]["rows"]
    assert [r[0] for r in rows] == ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06"]
    assert rows[4][1] == 318500.0               # May is the peak
    assert rows[5][2] == -107250.0              # June change vs May
    assert "May 2026" in data["answer"]


def test_highest_sales_region(client):
    data = ask(client, "Which region generated the highest sales?")
    assert data["intent"]["dimension"] == "region"
    assert data["supporting_data"]["rows"][0][:2] == ["South", 411200.0]


def test_compare_two_named_months(client):
    data = ask(client, "How did revenue change between April and May?")
    assert data["supporting_data"]["rows"] == [["2026-04", 240700.0, 8], ["2026-05", 318500.0, 8]]
    assert "increased" in data["answer"] and "+32.3%" in data["answer"]


# ---------- More valid questions ----------


@pytest.mark.parametrize("question, expected", [
    ("total revenue in North for March", 41400.0),
    ("What was the revenue for Furniture?", 433100.0),
    ("revenue from online sales", 746750.0),
    ("revenue of the wireless mouse", 148200.0),   # case-insensitive match
])
def test_filtered_totals(client, question, expected):
    assert ask(client, question)["supporting_data"]["rows"][0][0] == expected


def test_lowest_and_units(client):
    assert ask(client, "Which region had the lowest revenue?")["supporting_data"]["rows"][0][0] == "West"
    data = ask(client, "Which product sold the most units?")
    assert data["intent"]["metric"] == "quantity"
    assert data["supporting_data"]["rows"][0][:2] == ["Wireless Mouse", 228]


# ---------- Invalid / unsupported questions ----------


@pytest.mark.parametrize("question, reason_part", [
    ("What is our profit margin?", "no cost information"),
    ("What is the weather today?", "not look like a question about the sales data"),
    ("Delete all sales records", "only read"),
    ("What is the total revenue for Tablets?", "don't recognise 'tablets'"),
    ("Which customers bought the most?", "no customer information"),
])
def test_unsupported_questions_are_refused_not_guessed(client, question, reason_part):
    data = ask(client, question)
    assert data["status"] == "unsupported"
    assert reason_part in data["answer"]
    assert data["supporting_data"] is None


@pytest.mark.parametrize("body", [{}, {"question": ""}, {"question": "   "}, {"question": "x" * 501}, {"q": "hi there"}])
def test_invalid_requests_return_422(client, body):
    response = client.post("/api/ask", json=body)
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_request"


def test_malformed_json_returns_422(client):
    response = client.post("/api/ask", content="not json", headers={"Content-Type": "application/json"})
    assert response.status_code == 422


# ---------- Edge cases ----------


def test_compare_without_months_uses_latest_two_and_says_so(client):
    data = ask(client, "How did revenue change between two months?")
    assert data["intent"]["months"] == ["2026-05", "2026-06"]
    assert any("No months specified" in w for w in data["warnings"])


def test_compare_with_one_month_uses_previous_month(client):
    data = ask(client, "How did revenue change in March?")
    assert data["intent"]["months"] == ["2026-02", "2026-03"]


def test_month_with_no_data_is_zero_with_warning(client):
    data = ask(client, "Compare revenue between June and July")
    assert data["supporting_data"]["rows"][1] == ["2026-07", 0, 0]
    assert any("no completed sales for 2026-07" in w for w in data["warnings"])


def test_filter_with_no_matching_sales(client):
    data = ask(client, "total revenue for Desk in January")  # Desk sold in Jan only in South: exists
    assert data["supporting_data"]["rows"][0][2] == 1
    data = ask(client, "total revenue for Monitor in South in January")
    assert data["status"] == "answered"
    assert "no completed sales" in data["answer"].lower()


def test_answers_disclose_excluded_records(client):
    data = ask(client, "What is the total revenue?")
    assert any("1 non-completed order" in w and "2 invalid CSV row" in w for w in data["warnings"])


def test_data_quality_endpoint(client):
    data = client.get("/api/data-quality").json()
    assert data["rows_rejected"] == 2
    assert data["orders_by_status"] == {"Completed": 48, "Returned": 1}


def test_ui_and_health(client):
    assert client.get("/").status_code == 200
    assert client.get("/health").json() == {"status": "ok", "rows_loaded": 49}
