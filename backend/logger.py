"""
logger.py  —  Structured logger for Worktual AI backend

  • Prints timestamped, leveled lines to stdout → visible in Render logs
  • Keeps an in-memory ring buffer (last MAX_ENTRIES entries)
  • Emits entries that can be forwarded as SSE `log` events to the UI
"""

import datetime
from collections import deque
from typing import Optional

MAX_ENTRIES = 500   # ring-buffer size

# ── In-memory log buffer ──────────────────────────────────────────
_log_buffer: deque = deque(maxlen=MAX_ENTRIES)


def _now() -> str:
    return datetime.datetime.utcnow().strftime("%H:%M:%S.%f")[:-3]  # HH:MM:SS.mmm


def _emit(level: str, tag: str, message: str, extra: Optional[dict] = None) -> dict:
    entry = {
        "ts":      _now(),
        "level":   level,
        "tag":     tag,
        "message": message,
    }
    if extra:
        entry["extra"] = extra

    # Render-friendly stdout line
    tag_padded = f"[{tag}]".ljust(18)
    print(f"[{entry['ts']}] {level:<5} {tag_padded} {message}", flush=True)

    _log_buffer.append(entry)
    return entry


# ── Public API ────────────────────────────────────────────────────

def info(tag: str, message: str, extra: Optional[dict] = None) -> dict:
    return _emit("INFO ", tag, message, extra)

def warn(tag: str, message: str, extra: Optional[dict] = None) -> dict:
    return _emit("WARN ", tag, message, extra)

def error(tag: str, message: str, extra: Optional[dict] = None) -> dict:
    return _emit("ERROR", tag, message, extra)

def success(tag: str, message: str, extra: Optional[dict] = None) -> dict:
    return _emit("OK   ", tag, message, extra)

def debug(tag: str, message: str, extra: Optional[dict] = None) -> dict:
    return _emit("DEBUG", tag, message, extra)


def get_recent(n: int = 200) -> list:
    """Return the last n log entries from the ring buffer."""
    entries = list(_log_buffer)
    return entries[-n:] if len(entries) > n else entries


def clear():
    _log_buffer.clear()