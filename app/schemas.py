"""Data shapes shared across the app (Pydantic validates them).

`Intent` is the contract between the AI and the database:
the AI (or the keyword rules) must produce a valid Intent, and only
an Intent can trigger a query. Anything that does not fit is rejected.
"""
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

Dimension = Literal["product", "category", "region", "sales_channel"]
IntentType = Literal[
    "total_revenue",    # "What is the total revenue?"
    "revenue_by",       # "Which product / region generated the highest revenue?"
    "monthly_trend",    # "What is the monthly revenue trend?"
    "compare_months",   # "How did revenue change between March and April?"
    "custom_sql",       # anything else the LLM can answer with a validated SELECT
    "unsupported",      # not answerable from this data
]
MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class Intent(BaseModel):
    type: IntentType
    metric: Literal["revenue", "quantity"] = "revenue"
    dimension: Dimension | None = None
    order: Literal["desc", "asc"] = "desc"          # desc = highest first
    months: list[str] = Field(default_factory=list)  # YYYY-MM, used by compare_months
    month: str | None = None                         # optional single-month filter
    filters: dict[Dimension, str] = Field(default_factory=dict)  # e.g. {"region": "North"}
    sql: str | None = None                           # only for custom_sql
    reason: str | None = None                        # why unsupported

    @field_validator("months")
    @classmethod
    def check_months(cls, v):
        for m in v:
            if not MONTH_RE.match(m):
                raise ValueError(f"month '{m}' must be YYYY-MM")
        return v

    @field_validator("month")
    @classmethod
    def check_month(cls, v):
        if v is not None and not MONTH_RE.match(v):
            raise ValueError(f"month '{v}' must be YYYY-MM")
        return v


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)

    @field_validator("question")
    @classmethod
    def not_blank(cls, v):
        if not v.strip():
            raise ValueError("question must not be blank")
        return v.strip()


class QueryResult(BaseModel):
    sql: str
    params: list[Any] = Field(default_factory=list)
    columns: list[str]
    rows: list[list[Any]]


class AskResponse(BaseModel):
    question: str
    status: Literal["answered", "unsupported"]
    answer: str
    intent: Intent
    interpreted_by: Literal["llm", "rules"]
    supporting_data: QueryResult | None = None
    warnings: list[str] = Field(default_factory=list)
