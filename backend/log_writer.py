import datetime
import json
import os
from pathlib import Path

DEFAULT_LOG_FILENAME = os.getenv("FIGMA_LOG_FILENAME", "figma_debug.log")


def get_dated_log_filename(prefix: str = "Figma") -> str:
    now = datetime.datetime.now()
    return f"{prefix}_{now.strftime('%d_%m')}_logs.log"


def _log_path(filename: str | None = None) -> Path:
    log_dir = Path("logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / (filename or DEFAULT_LOG_FILENAME)


def write_log(message, filename: str | None = None):
    try:
        path = _log_path(filename)
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with path.open("a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {message}\n")
    except Exception as exc:
        print(f"Logging failed: {exc}")


def write_model_context_log(
    *,
    tag: str,
    action: str,
    model: str,
    input_chars: int,
    output_chars: int = 0,
    input_tokens_est: int = 0,
    output_tokens_est: int = 0,
    input_preview: str = "",
    output_preview: str = "",
    filename: str | None = None,
):
    payload = {
        "tag": tag,
        "action": action,
        "model": model,
        "input_chars": input_chars,
        "output_chars": output_chars,
        "input_tokens_est": input_tokens_est,
        "output_tokens_est": output_tokens_est,
        "input_preview": input_preview,
        "output_preview": output_preview,
    }
    write_log("MODEL_CONTEXT " + json.dumps(payload, ensure_ascii=True), filename=filename)
