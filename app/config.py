"""Central settings, read from environment variables (.env file).

Why: keeps secrets (API keys) out of code, and lets tests point at a
temporary database or force the offline LLM mode without code changes.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent


class Settings:
    CSV_PATH: str = os.getenv("CSV_PATH", str(ROOT / "data" / "sales.csv"))
    DB_PATH: str = os.getenv("DB_PATH", str(ROOT / "data" / "sales.db"))

    # "mock" = no AI key needed: questions are interpreted by keyword rules.
    # "gemini" / "openai" = an LLM interprets the question (rules are the fallback).
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "mock").lower()
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    LLM_TIMEOUT_SECONDS: float = float(os.getenv("LLM_TIMEOUT_SECONDS", "20"))
    LLM_MAX_RETRIES: int = int(os.getenv("LLM_MAX_RETRIES", "1"))

    # Allow the LLM to write SQL for questions outside the built-in types.
    ENABLE_TEXT_TO_SQL: bool = os.getenv("ENABLE_TEXT_TO_SQL", "true").lower() == "true"


settings = Settings()
