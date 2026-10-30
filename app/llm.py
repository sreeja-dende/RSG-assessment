"""One small wrapper around the LLM.

Why a wrapper?
- The rest of the app calls `generate(prompt)` and never cares which provider is used.
- Timeouts + retries live in one place (reliability).
- A "mock" provider lets the app and tests run with NO API key and NO internet.
- Errors become one clean exception type (LLMError) that the API turns into a 503.
"""
import time

import httpx

from app.config import settings


class LLMError(Exception):
    """Raised when the LLM cannot give a usable answer."""


def _call_gemini(prompt: str, system: str) -> str:
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{settings.GEMINI_MODEL}:generateContent"
    )
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2},
    }
    r = httpx.post(
        url,
        params={"key": settings.GEMINI_API_KEY},
        json=body,
        timeout=settings.LLM_TIMEOUT_SECONDS,
    )
    r.raise_for_status()
    data = r.json()
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as e:
        raise LLMError(f"Unexpected Gemini response shape: {data}") from e


def _call_openai(prompt: str, system: str) -> str:
    r = httpx.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}"},
        json={
            "model": settings.OPENAI_MODEL,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        },
        timeout=settings.LLM_TIMEOUT_SECONDS,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def _call_mock(prompt: str, system: str) -> str:
    """Deterministic fake LLM for offline dev and tests.

    For agent prompts it returns a valid final-answer JSON; otherwise it echoes
    the first line of context so RAG answers are still meaningful.
    """
    if '"action"' in system:
        return '{"action": "final", "input": "Mock answer: tools not needed."}'
    if "CONTEXT:" in prompt:
        ctx = prompt.split("CONTEXT:", 1)[1].strip()
        first = ctx.splitlines()[0] if ctx else ""
        return f"(mock) Based on the context: {first[:200]}"
    return "(mock) No context provided."


_PROVIDERS = {"gemini": _call_gemini, "openai": _call_openai, "mock": _call_mock}


def generate(prompt: str, system: str = "You are a helpful assistant.") -> str:
    provider = _PROVIDERS.get(settings.LLM_PROVIDER)
    if provider is None:
        raise LLMError(f"Unknown LLM_PROVIDER '{settings.LLM_PROVIDER}'")

    last_error: Exception | None = None
    for attempt in range(settings.LLM_MAX_RETRIES + 1):
        try:
            text = provider(prompt, system)
            if not text or not text.strip():
                raise LLMError("LLM returned an empty response")
            return text.strip()
        except httpx.HTTPStatusError as e:
            last_error = e
            # 4xx (bad key, bad request) won't fix itself -> stop retrying, except 429 rate limit
            if e.response.status_code < 500 and e.response.status_code != 429:
                break
        except (httpx.TimeoutException, httpx.TransportError, LLMError) as e:
            last_error = e
        time.sleep(min(2 ** attempt, 8))  # exponential backoff: 1s, 2s, 4s...
    raise LLMError(f"LLM call failed after retries: {last_error}")
