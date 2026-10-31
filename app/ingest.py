"""Load the sales CSV into SQLite, validating every row.

Run on its own:   python -m app.ingest
It also runs automatically when the API starts.

Validation rules (a row failing any rule goes to `rejected_rows` with the reason):
- transaction_id present and not a duplicate
- order_date in DD-MM-YYYY format (03-01-2026 means 3 January, not 1 March)
- product, category, region, sales_channel, status present
- quantity is a whole number greater than 0
- unit_price is a number greater than 0
- discount_amount is a number >= 0 and not larger than the order value
  (a blank discount is treated as 0)

Returned orders are VALID data, so they are stored. They are excluded from
revenue by the `revenue_lines` view, not deleted.
"""
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from app.config import settings
from app.db import SCHEMA, get_connection

log = logging.getLogger("ingest")

EXPECTED_COLUMNS = [
    "Transaction_Id", "Order_date", "Product", "Category", "Region",
    "Sales_channel", "Quantity", "Unit_price", "Discount_amount", "Status",
]
TEXT_COLUMNS = ["Product", "Category", "Region", "Sales_channel", "Status"]


@dataclass
class IngestReport:
    total_rows: int = 0
    loaded_rows: int = 0
    rejected: list[dict] = field(default_factory=list)


def _to_number(value):
    """Return a float, or None if the value is blank or not numeric."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(number) else number


def validate_row(row: dict, seen_ids: set) -> tuple[dict | None, str | None]:
    """Return (clean_row, None) if valid, or (None, reason) if not."""
    txn = (row.get("Transaction_Id") or "").strip()
    if not txn:
        return None, "missing transaction id"
    if txn in seen_ids:
        return None, f"duplicate transaction id {txn}"

    try:
        order_date = pd.to_datetime(row.get("Order_date", "").strip(), format="%d-%m-%Y")
    except (ValueError, TypeError):
        return None, f"invalid order date '{row.get('Order_date')}' (expected DD-MM-YYYY)"

    texts = {}
    for col in TEXT_COLUMNS:
        value = (row.get(col) or "").strip()
        if not value:
            return None, f"missing {col.lower()}"
        texts[col] = value

    quantity = _to_number(row.get("Quantity"))
    if quantity is None or quantity != int(quantity):
        return None, f"invalid quantity '{row.get('Quantity')}'"
    if quantity <= 0:
        return None, f"quantity must be greater than 0 (got {int(quantity)})"

    unit_price = _to_number(row.get("Unit_price"))
    if unit_price is None:
        return None, "missing or invalid unit price"
    if unit_price <= 0:
        return None, f"unit price must be greater than 0 (got {unit_price})"

    raw_discount = row.get("Discount_amount")
    discount = 0.0 if raw_discount is None or not str(raw_discount).strip() else _to_number(raw_discount)
    if discount is None or discount < 0:
        return None, f"invalid discount '{raw_discount}'"
    if discount > quantity * unit_price:
        return None, "discount is larger than the order value"

    return {
        "transaction_id": txn,
        "order_date": order_date.strftime("%Y-%m-%d"),
        "product": texts["Product"],
        "category": texts["Category"],
        "region": texts["Region"],
        "sales_channel": texts["Sales_channel"],
        "quantity": int(quantity),
        "unit_price": unit_price,
        "discount_amount": discount,
        "status": texts["Status"].title(),  # "completed" -> "Completed"
    }, None


def load_csv(csv_path: str | None = None, db_path: str | None = None) -> IngestReport:
    csv_path = csv_path or settings.CSV_PATH
    db_path = db_path or settings.DB_PATH
    if not Path(csv_path).exists():
        raise FileNotFoundError(f"CSV not found: {csv_path}")

    # Read everything as text first, so WE decide what counts as valid
    # instead of pandas silently converting bad values.
    df = pd.read_csv(csv_path, dtype=str, keep_default_na=False, skipinitialspace=True)
    df.columns = [c.strip() for c in df.columns]
    missing = [c for c in EXPECTED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"CSV is missing columns: {missing}")

    report = IngestReport(total_rows=len(df))
    clean_rows, seen_ids = [], set()
    for index, row in enumerate(df.to_dict(orient="records")):
        clean, reason = validate_row(row, seen_ids)
        if clean:
            clean_rows.append(clean)
            seen_ids.add(clean["transaction_id"])
        else:
            report.rejected.append({"source_line": index + 2, "raw": row, "reason": reason})

    # Rebuild from scratch in ONE transaction: the database always matches
    # the CSV exactly, and a failure halfway leaves the old data untouched.
    conn = get_connection(db_path)
    try:
        with conn:
            conn.executescript("DROP VIEW IF EXISTS revenue_lines;"
                               "DROP TABLE IF EXISTS sales;"
                               "DROP TABLE IF EXISTS rejected_rows;")
            conn.executescript(SCHEMA)
            conn.executemany(
                """INSERT INTO sales VALUES (:transaction_id, :order_date, :product, :category,
                   :region, :sales_channel, :quantity, :unit_price, :discount_amount, :status)""",
                clean_rows,
            )
            conn.executemany(
                "INSERT INTO rejected_rows (source_line, raw_data, reason) VALUES (?, ?, ?)",
                [(r["source_line"], json.dumps(r["raw"]), r["reason"]) for r in report.rejected],
            )
    finally:
        conn.close()

    report.loaded_rows = len(clean_rows)
    log.info("Loaded %d of %d rows, rejected %d", report.loaded_rows, report.total_rows,
             len(report.rejected))
    for r in report.rejected:
        log.warning("Rejected CSV line %d: %s", r["source_line"], r["reason"])
    return report


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    result = load_csv()
    print(f"Loaded {result.loaded_rows} of {result.total_rows} rows into {settings.DB_PATH}")
    for r in result.rejected:
        print(f"  rejected line {r['source_line']}: {r['reason']}")
