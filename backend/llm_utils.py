import asyncio
import json
import os
import random

import logger as log
from log_writer import write_model_context_log

DEFAULT_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "4"))
DEFAULT_BASE_DELAY = float(os.getenv("LLM_RETRY_BASE_DELAY", "1.0"))
DEFAULT_MAX_DELAY = float(os.getenv("LLM_RETRY_MAX_DELAY", "8.0"))
MODEL_CONTEXT_PREVIEW_CHARS = int(os.getenv("MODEL_CONTEXT_PREVIEW_CHARS", "400"))

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


def _stringify_contents(contents) -> str:
    if isinstance(contents, str):
        return contents
    if isinstance(contents, list):
        parts = []
        for item in contents:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if "text" in item:
                    parts.append(str(item.get("text", "")))
                elif "inline_data" in item:
                    inline = item.get("inline_data") or {}
                    mime = inline.get("mime_type", "application/octet-stream")
                    data_len = len(str(inline.get("data", "") or ""))
                    parts.append(f"[inline_data mime={mime} chars={data_len}]")
                else:
                    parts.append(json.dumps(item, ensure_ascii=True))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return str(contents)


def _estimate_tokens_from_text(text: str) -> int:
    return max(1, int(round(len(text) / 4))) if text else 0


def _preview_text(text: str, limit: int = MODEL_CONTEXT_PREVIEW_CHARS) -> str:
    compact = " ".join((text or "").split())
    return compact[:limit]


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
    input_text = _stringify_contents(contents)
    input_chars = len(input_text)
    input_tokens_est = _estimate_tokens_from_text(input_text)
    while True:
        attempt += 1
        try:
            if attempt > 1:
                log.info(log_tag, f"{action} — retry attempt {attempt}/{max_retries + 1}")
            response = await asyncio.to_thread(
                client.models.generate_content,
                model=model,
                contents=contents,
                config=config,
            )
            output_text = str(getattr(response, "text", "") or "")
            output_chars = len(output_text)
            output_tokens_est = _estimate_tokens_from_text(output_text)
            log.info(
                log_tag,
                f"{action} — input chars={input_chars} est_tokens={input_tokens_est} | "
                f"output chars={output_chars} est_tokens={output_tokens_est}"
            )
            write_model_context_log(
                tag=log_tag,
                action=action,
                model=model,
                input_chars=input_chars,
                output_chars=output_chars,
                input_tokens_est=input_tokens_est,
                output_tokens_est=output_tokens_est,
                input_preview=_preview_text(input_text),
                output_preview=_preview_text(output_text),
            )
            return response
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
