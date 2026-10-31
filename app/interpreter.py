"""Turn a natural-language question into a validated Intent.

Two interpreters, same output shape:
- interpret_with_llm(): the LLM reads the question and returns Intent JSON.
- interpret_with_rules(): keyword rules. Used when no LLM is configured, or
  as an automatic fallback when the LLM fails or returns something invalid.

The LLM never sees or produces the numbers in the answer. It only chooses
WHICH query to run and with WHICH parameters, and even those are checked
against the real values in the database (validate_intent) before use.
"""
import json
import logging
import re
from datetime import date

from pydantic import ValidationError

from app.config import settings
from app.llm import LLMError, generate, llm_enabled
from app.schemas import Intent

log = logging.getLogger("interpreter")

MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
MONTH_NAME_RE = re.compile(r"\b(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\b(?:\s*,?\s*(\d{4}))?")
ISO_MONTH_RE = re.compile(r"\b(\d{4})-(\d{1,2})\b")

BUSINESS_WORDS = re.compile(r"\b(revenue|sales?|sold|sell|selling|units?|quantity|orders?|earn\w*|income|turnover|performance|perform\w*)\b")
NO_DATA_FOR = {  # things this dataset cannot answer, with an honest reason
    r"\b(profit|margin|cost|costs|expense|expenses)\b": "The data has no cost information, so profit or margin cannot be calculated.",
    r"\b(customer|customers|client|clients)\b": "The data has no customer information.",
    r"\b(forecast|predict|prediction|next month|next year|future)\b": "Forecasting is not supported; the assistant only reports what the data shows.",
}
WRITE_WORDS = re.compile(r"\b(delete|drop|remove|truncate|insert|update|alter|wipe|erase|modify|overwrite)\b")
# Words that may follow "for/of/in/from" without being a product/region name
SAFE_AFTER_PREPOSITION = {
    "the", "a", "an", "all", "each", "every", "this", "that", "our", "my", "total", "overall",
    "revenue", "sales", "sale", "units", "unit", "orders", "order", "month", "months", "monthly",
    "year", "period", "data", "product", "products", "region", "regions", "category", "categories",
    "channel", "channels", "sales_channel", "terms", "between", "two", "last", "first", "recent",
    "latest", "previous", "time", "q1", "q2", "h1", "quantity", "trend", "general", "it", "them",
}
DIMENSION_WORDS = {
    "product": r"\b(products?|items?)\b",
    "category": r"\b(categor(y|ies))\b",
    "region": r"\b(regions?|areas?|zones?)\b",
    "sales_channel": r"\b(channels?|online or retail|retail or online)\b",
}


# ---------- Shared helpers ----------

def _shift_month(ym: str, delta: int) -> str:
    year, month = map(int, ym.split("-"))
    index = year * 12 + (month - 1) + delta
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def find_months(question: str, available_months: list[str]) -> list[str]:
    """Find months in the question, in the order mentioned, as YYYY-MM.

    A month without a year ('March') gets the latest year in the data that has
    that month, otherwise the latest year in the data.
    """
    q = question.lower()
    found: list[tuple[int, str]] = []
    latest_year = int(available_months[-1][:4]) if available_months else date.today().year
    for m in ISO_MONTH_RE.finditer(q):
        if 1 <= int(m.group(2)) <= 12:
            found.append((m.start(), f"{int(m.group(1)):04d}-{int(m.group(2)):02d}"))
    for m in MONTH_NAME_RE.finditer(q):
        month_num = MONTHS[m.group(1)]
        if m.group(2):
            ym = f"{m.group(2)}-{month_num:02d}"
        else:
            matches = [a for a in available_months if int(a[5:]) == month_num]
            ym = matches[-1] if matches else f"{latest_year}-{month_num:02d}"
        found.append((m.start(), ym))
    result = []
    for _, ym in sorted(found):
        if ym not in result:
            result.append(ym)
    return result


def find_filters(question: str, values: dict[str, list[str]]) -> dict[str, str]:
    """Spot exact names from the data (e.g. 'North', 'Monitor') in the question."""
    q = question.lower()
    filters = {}
    for dim in ("product", "category", "region", "sales_channel"):
        for value in sorted(values.get(dim, []), key=len, reverse=True):
            if re.search(r"\b" + re.escape(value.lower()) + r"s?\b", q):
                filters[dim] = value
                break
    return filters


def find_unknown_name(question: str, values: dict[str, list[str]]) -> str | None:
    """Return a word like 'tablets' in 'revenue for tablets' that is not in the data.

    Without this check, an unknown name would be ignored and the user would get
    the overall total: a confident answer to a different question.
    """
    q = question.lower()
    known = set(SAFE_AFTER_PREPOSITION) | set(MONTHS)
    for dim in ("product", "category", "region", "sales_channel"):
        for value in values.get(dim, []):
            known.update(re.findall(r"[a-z0-9\-]+", value.lower()))
    for m in re.finditer(r"\b(?:for|of|in|from)\s+([a-z][a-z0-9\-]*)", q):
        word = m.group(1)
        singular = word[:-1] if word.endswith("s") else word
        if word not in known and singular not in known:
            return word
    return None


def complete_compare(intent: Intent, available_months: list[str], warnings: list[str]) -> Intent:
    """compare_months needs exactly two months; fill in sensible defaults and say so."""
    months = intent.months[:2]
    if len(months) == 1:
        months = [_shift_month(months[0], -1), months[0]]
        warnings.append(f"Only one month given; compared it with the previous month ({months[0]}).")
    elif not months:
        if len(available_months) < 2:
            return Intent(type="unsupported", reason="Not enough months of data to compare.")
        months = available_months[-2:]
        warnings.append(f"No months specified; compared the two most recent months ({months[0]} and {months[1]}).")
    return intent.model_copy(update={"months": months})


def validate_intent(intent: Intent, values: dict[str, list[str]], warnings: list[str]) -> Intent:
    """Check the intent against the REAL data. Fix case, drop unknown values, warn."""
    clean_filters = {}
    for dim, value in intent.filters.items():
        canonical = {v.lower(): v for v in values.get(dim, [])}
        if str(value).lower() in canonical:
            clean_filters[dim] = canonical[str(value).lower()]
        else:
            # An unknown filter must not be silently ignored: that would answer a different question
            return Intent(type="unsupported",
                          reason=f"'{value}' is not a known {dim.replace('_', ' ')}. "
                                 f"Known values: {', '.join(values.get(dim, []))}.")
    intent = intent.model_copy(update={"filters": clean_filters})

    if intent.type == "revenue_by" and not intent.dimension:
        intent = intent.model_copy(update={"dimension": "product"})
        warnings.append("No grouping given; grouped by product.")
    if intent.type == "revenue_by" and intent.dimension in intent.filters:
        # "Which region ... in North?" -> grouping by a filtered column is pointless
        intent.filters.pop(intent.dimension)
    if intent.type == "compare_months":
        intent = complete_compare(intent, values["months"], warnings)
    months_to_check = list(intent.months) + ([intent.month] if intent.month else [])
    for m in months_to_check:
        if m not in values["months"]:
            warnings.append(f"There are no completed sales for {m} in the data "
                            f"(data covers {values['months'][0]} to {values['months'][-1]}).")
    return intent


# ---------- Rule-based interpreter ----------

def interpret_with_rules(question: str, values: dict[str, list[str]]) -> Intent:
    q = question.lower()

    if WRITE_WORDS.search(q):
        return Intent(type="unsupported", reason="I can only read and report on the data, not change it.")
    for pattern, reason in NO_DATA_FOR.items():
        if re.search(pattern, q):
            return Intent(type="unsupported", reason=reason)
    if not BUSINESS_WORDS.search(q):
        return Intent(type="unsupported",
                      reason="This does not look like a question about the sales data.")

    unknown = find_unknown_name(question, values)
    if unknown:
        return Intent(type="unsupported",
                      reason=f"I don't recognise '{unknown}' in the data. Products: "
                             f"{', '.join(values['product'])}. Regions: {', '.join(values['region'])}.")

    months = find_months(question, values["months"])
    filters = find_filters(question, values)
    metric = "quantity" if re.search(r"\b(units?|quantity|how many|most sold|sold the most)\b", q) else "revenue"
    ascending = bool(re.search(r"\b(lowest|least|worst|bottom|minimum|min|weakest|smallest|fewest)\b", q))

    if re.search(r"\b(trend|monthly|month by month|month-by-month|each month|per month|over time|by month)\b", q) \
            or (re.search(r"\bmonths?\b", q) and re.search(r"\b(highest|best|peak|top|most|lowest|worst)\b", q)):
        return Intent(type="monthly_trend", filters=filters)

    if re.search(r"\b(change|changed|compare|comparison|difference|differ|vs|versus|grow|growth|increase|decrease|drop|rise)\b", q):
        return Intent(type="compare_months", months=months, filters=filters)

    for dim, pattern in DIMENSION_WORDS.items():
        if re.search(pattern, q):
            return Intent(type="revenue_by", dimension=dim, metric=metric,
                          order="asc" if ascending else "desc",
                          filters=filters, month=months[0] if months else None)

    return Intent(type="total_revenue", filters=filters, month=months[0] if months else None)


# ---------- LLM interpreter ----------

def _system_prompt(values: dict[str, list[str]]) -> str:
    sql_part = (
        '\n- "custom_sql": ONLY if no other type fits but the question can be answered from the data. '
        'Put ONE SQLite SELECT statement in "sql", reading only from the view revenue_lines('
        "transaction_id, order_date TEXT 'YYYY-MM-DD', month TEXT 'YYYY-MM', product, category, region, "
        "sales_channel, quantity, unit_price, discount_amount, gross_revenue, net_revenue). "
        "revenue_lines already contains only completed orders. Never modify data."
    ) if settings.ENABLE_TEXT_TO_SQL else ""
    return f"""You convert a business user's question about sales data into a JSON intent.
You do NOT answer the question and you never output numbers; a database computes the answer.

Return ONLY a JSON object with these fields:
- "type": one of
  - "total_revenue": total revenue (optionally filtered)
  - "revenue_by": rank groups, e.g. which product/region has the highest or lowest revenue
  - "monthly_trend": revenue per month, trend over time, best/worst month
  - "compare_months": how revenue changed between two months
  - "unsupported": the data cannot answer it (put the reason in "reason"){sql_part}
- "metric": "revenue" (default; "sales" means revenue) or "quantity" (units sold)
- "dimension": for revenue_by: "product", "category", "region" or "sales_channel"
- "order": "desc" for highest/top/best, "asc" for lowest/worst
- "months": for compare_months, list of months as "YYYY-MM" in the order asked
- "month": optional single month filter "YYYY-MM"
- "filters": optional object, e.g. {{"region": "North"}}. Values MUST come from the lists below.
- "reason": only for unsupported

Data facts:
- products: {values['product']}
- categories: {values['category']}
- regions: {values['region']}
- sales channels: {values['sales_channel']}
- months with data: {values['months']}
- The data has NO costs, profit, or customer details. Questions needing them are "unsupported".
- A month named without a year means the year in the data above."""


def interpret_with_llm(question: str, values: dict[str, list[str]]) -> Intent:
    raw = generate(f"Question: {question}", system=_system_prompt(values), json_mode=True)
    match = re.search(r"\{.*\}", raw, re.DOTALL)  # tolerate ```json fences or extra text
    if not match:
        raise LLMError(f"LLM did not return JSON: {raw[:200]}")
    try:
        data = json.loads(match.group(0))
        data = {k: v for k, v in data.items() if v is not None}  # drop explicit nulls
        intent = Intent.model_validate(data)
    except (json.JSONDecodeError, ValidationError) as e:
        raise LLMError(f"LLM returned an invalid intent: {e}") from e
    if intent.type == "custom_sql" and (not settings.ENABLE_TEXT_TO_SQL or not intent.sql):
        raise LLMError("LLM chose custom_sql without usable SQL")
    return intent


# ---------- Entry point ----------

def interpret(question: str, values: dict[str, list[str]]) -> tuple[Intent, str, list[str]]:
    """Return (validated intent, 'llm' or 'rules', warnings)."""
    warnings: list[str] = []
    source = "rules"
    if llm_enabled():
        try:
            intent = interpret_with_llm(question, values)
            source = "llm"
        except LLMError as e:
            log.warning("LLM interpretation failed, using rules: %s", e)
            warnings.append("AI interpretation was unavailable, so keyword rules were used.")
            intent = interpret_with_rules(question, values)
    else:
        intent = interpret_with_rules(question, values)

    if intent.type not in ("unsupported", "custom_sql"):
        intent = validate_intent(intent, values, warnings)
    return intent, source, warnings
