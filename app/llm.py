"""One small wrapper around the LLM provider.

- The rest of the app calls `generate(prompt, system, json_mode)` and does not
  care which provider is behind it.
- Timeouts and retries live in one place.
- The API key is sent in a header, never in the URL (URLs end up in logs).
- Every failure becomes one exception type, LLMError, so callers can fall back.
"""
import logging
import time

import httpx

from app.config import settings

log = logging.getLogger("llm")


class LLMError(Exception):
    """The LLM could not give a usable answer."""


def llm_enabled() -> bool:
    return settings.LLM_PROVIDER in ("gemini", "openai")


def _call_gemini(prompt: str, system: str, json_mode: bool) -> str:
    if not settings.GEMINI_API_KEY:
        raise LLMError("GEMINI_API_KEY is not set")
    config = {"temperature": 0}
    if json_mode:
        config["responseMimeType"] = "application/json"
    r = httpx.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{settings.GEMINI_MODEL}:generateContent",
        headers={"x-goog-api-key": settings.GEMINI_API_KEY},
        json={
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": config,
        },
        timeout=settings.LLM_TIMEOUT_SECONDS,
    )
    r.raise_for_status()
    data = r.json()
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as e:
        raise LLMError(f"Unexpected Gemini response: {str(data)[:300]}") from e


def _call_openai(prompt: str, system: str, json_mode: bool) -> str:
    if not settings.OPENAI_API_KEY:
        raise LLMError("OPENAI_API_KEY is not set")
    body = {
        "model": settings.OPENAI_MODEL,
        "temperature": 0,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    r = httpx.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}"},
        json=body,
        timeout=settings.LLM_TIMEOUT_SECONDS,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


_PROVIDERS = {"gemini": _call_gemini, "openai": _call_openai}


def generate(prompt: str, system: str, json_mode: bool = False) -> str:
    provider = _PROVIDERS.get(settings.LLM_PROVIDER)
    if provider is None:
        raise LLMError(f"LLM provider '{settings.LLM_PROVIDER}' is not enabled")

    last_error: Exception | None = None
    for attempt in range(settings.LLM_MAX_RETRIES + 1):
        try:
            text = provider(prompt, system, json_mode)
            if not text or not text.strip():
                raise LLMError("LLM returned an empty response")
            return text.strip()
        except httpx.HTTPStatusError as e:
            # keep the provider's own message, e.g. "API key not valid" / "model not found"
            last_error = LLMError(f"HTTP {e.response.status_code}: {e.response.text[:300]}")
            if e.response.status_code < 500 and e.response.status_code != 429:
                break  # bad key / bad request will not fix itself by retrying
        except (httpx.TimeoutException, httpx.TransportError, LLMError) as e:
            last_error = e
        if attempt < settings.LLM_MAX_RETRIES:
            time.sleep(min(2 ** attempt, 4))  # backoff: 1s, 2s, 4s
    log.error("LLM call failed: %s", last_error)
    raise LLMError(str(last_error))
