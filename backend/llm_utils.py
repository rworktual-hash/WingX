import asyncio
import os
import random

import logger as log

DEFAULT_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "4"))
DEFAULT_BASE_DELAY = float(os.getenv("LLM_RETRY_BASE_DELAY", "1.0"))
DEFAULT_MAX_DELAY = float(os.getenv("LLM_RETRY_MAX_DELAY", "8.0"))

RETRYABLE_SNIPPETS = [
    "429",
    "resource exhausted",
    "rate limit",
    "quota",
    "too many requests",
    "unavailable",
    "temporarily unavailable",
    "timed out",
    "timeout",
    "deadline exceeded",
    "internal error",
    "service unavailable",
    "connection reset",
    "connection aborted",
    "afc",
    "concurrent",
    "try again",
]


def is_retryable_llm_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(snippet in message for snippet in RETRYABLE_SNIPPETS)


async def generate_content_with_retry(
    *,
    client,
    model: str,
    contents,
    config: dict | None = None,
    log_tag: str = "LLM",
    action: str = "Gemini call",
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
):
    attempt = 0
    while True:
        attempt += 1
        try:
            if attempt > 1:
                log.info(log_tag, f"{action} — retry attempt {attempt}/{max_retries + 1}")
            return await asyncio.to_thread(
                client.models.generate_content,
                model=model,
                contents=contents,
                config=config,
            )
        except Exception as exc:
            retryable = is_retryable_llm_error(exc)
            if (not retryable) or attempt > max_retries:
                log.error(log_tag, f"{action} failed after {attempt} attempt(s) — {exc}")
                raise

            delay = min(max_delay, base_delay * (2 ** (attempt - 1))) + random.uniform(0, 0.35)
            log.warn(
                log_tag,
                f"{action} transient failure — retrying in {delay:.1f}s "
                f"(attempt {attempt}/{max_retries + 1}): {exc}"
            )
            await asyncio.sleep(delay)
