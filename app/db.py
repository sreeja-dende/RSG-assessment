"""SQLite connection helpers.

Two kinds of connection:
- get_connection(): normal read/write, used only by the ingestion step.
- get_readonly_connection(): opened with mode=ro, so the database engine
  itself refuses any write. All question answering uses this one.
"""
import sqlite3
from pathlib import Path

from app.config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS sales (
    transaction_id   TEXT PRIMARY KEY,
    order_date       TEXT NOT NULL,          -- ISO format YYYY-MM-DD
    product          TEXT NOT NULL,
    category         TEXT NOT NULL,
    region           TEXT NOT NULL,
    sales_channel    TEXT NOT NULL,
    quantity         INTEGER NOT NULL CHECK (quantity > 0),
    unit_price       REAL    NOT NULL CHECK (unit_price > 0),
    discount_amount  REAL    NOT NULL DEFAULT 0 CHECK (discount_amount >= 0),
    status           TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sales_date    ON sales(order_date);
CREATE INDEX IF NOT EXISTS idx_sales_product ON sales(product);
CREATE INDEX IF NOT EXISTS idx_sales_region  ON sales(region);

-- Rows that failed validation are kept (not silently dropped) with the reason.
CREATE TABLE IF NOT EXISTS rejected_rows (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_line INTEGER NOT NULL,            -- line number in the CSV file
    raw_data    TEXT    NOT NULL,            -- the original row as JSON
    reason      TEXT    NOT NULL
);

-- One single definition of "revenue" that every query uses:
-- completed orders only, net of discount.
CREATE VIEW IF NOT EXISTS revenue_lines AS
SELECT
    transaction_id,
    order_date,
    strftime('%Y-%m', order_date)              AS month,
    product, category, region, sales_channel,
    quantity, unit_price, discount_amount,
    quantity * unit_price                      AS gross_revenue,
    quantity * unit_price - discount_amount    AS net_revenue
FROM sales
WHERE status = 'Completed';
"""


def get_connection(path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or settings.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_readonly_connection(path: str | None = None) -> sqlite3.Connection:
    db_file = Path(path or settings.DB_PATH).resolve()
    if not db_file.exists():
        raise FileNotFoundError(f"Database not found at {db_file}. Run: python -m app.ingest")
    conn = sqlite3.connect(f"{db_file.as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn
