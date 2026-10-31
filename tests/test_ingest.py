"""Data loading: invalid rows are rejected with a reason, valid rows stored correctly."""
import pytest

from app.db import get_readonly_connection
from app.ingest import load_csv, validate_row

GOOD = {
    "Transaction_Id": "T1", "Order_date": "03-01-2026", "Product": "Desk", "Category": "Furniture",
    "Region": "North", "Sales_channel": "Online", "Quantity": "2", "Unit_price": "100",
    "Discount_amount": "10", "Status": "Completed",
}


def test_supplied_csv_loads_and_rejects_bad_rows(loaded_db):
    assert loaded_db.total_rows == 51
    assert loaded_db.loaded_rows == 49
    reasons = {r["raw"]["Transaction_Id"]: r["reason"] for r in loaded_db.rejected}
    assert set(reasons) == {"TXN0034", "TXN0051"}
    assert "quantity" in reasons["TXN0034"]      # quantity 0
    assert "unit price" in reasons["TXN0051"]    # missing price


def test_dates_are_day_first_and_returned_orders_excluded_from_revenue(loaded_db):
    with get_readonly_connection() as conn:
        # 03-01-2026 must be 3 January, not 1 March
        assert conn.execute("SELECT order_date FROM sales WHERE transaction_id='TXN0001'").fetchone()[0] == "2026-01-03"
        # the returned order is stored...
        assert conn.execute("SELECT status FROM sales WHERE transaction_id='TXN0033'").fetchone()[0] == "Returned"
        # ...but is not counted as revenue
        assert conn.execute("SELECT COUNT(*) FROM revenue_lines WHERE transaction_id='TXN0033'").fetchone()[0] == 0
        assert conn.execute("SELECT SUM(net_revenue) FROM revenue_lines").fetchone()[0] == 1439200


def test_valid_row_is_cleaned():
    row, reason = validate_row({**GOOD, "Status": " completed ", "Discount_amount": ""}, set())
    assert reason is None
    assert row["order_date"] == "2026-01-03"
    assert row["status"] == "Completed"
    assert row["discount_amount"] == 0  # blank discount -> 0


@pytest.mark.parametrize("change, expected_reason", [
    ({"Transaction_Id": ""}, "missing transaction id"),
    ({"Order_date": "2026-01-03"}, "invalid order date"),
    ({"Order_date": "31-02-2026"}, "invalid order date"),
    ({"Region": "  "}, "missing region"),
    ({"Quantity": "abc"}, "invalid quantity"),
    ({"Quantity": "2.5"}, "invalid quantity"),
    ({"Quantity": "-1"}, "quantity must be greater than 0"),
    ({"Unit_price": "0"}, "unit price must be greater than 0"),
    ({"Discount_amount": "-5"}, "invalid discount"),
    ({"Discount_amount": "500"}, "discount is larger than the order value"),
])
def test_invalid_rows_are_rejected(change, expected_reason):
    row, reason = validate_row({**GOOD, **change}, set())
    assert row is None
    assert expected_reason in reason


def test_duplicate_transaction_id_rejected():
    _, reason = validate_row(GOOD, {"T1"})
    assert "duplicate" in reason


def test_csv_with_missing_columns_fails_clearly(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("Transaction_Id,Product\nT1,Desk\n")
    with pytest.raises(ValueError, match="missing columns"):
        load_csv(str(bad), str(tmp_path / "x.db"))
