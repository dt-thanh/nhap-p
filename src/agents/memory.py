"""Small persistent conversation store for the read-only Agent."""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

_ROOT = Path(os.getenv("AGENT_SESSION_DIR", "data/agent_sessions"))

def new_session_id() -> str:
    return str(uuid.uuid4())

def _path(session_id: str) -> Path:
    value = uuid.UUID(session_id)
    _ROOT.mkdir(parents=True, exist_ok=True)
    return _ROOT / f"{value}.json"

def history(session_id: str | None) -> list[dict[str, str]]:
    if not session_id:
        return []
    try:
        data = json.loads(_path(session_id).read_text(encoding="utf-8"))
        return data[-12:] if isinstance(data, list) else []
    except (ValueError, OSError, json.JSONDecodeError):
        return []

def append(session_id: str, user: str, assistant: str) -> None:
    messages = history(session_id)
    messages.extend([{"role": "user", "content": user}, {"role": "assistant", "content": assistant}])
    _path(session_id).write_text(json.dumps(messages[-12:], ensure_ascii=False), encoding="utf-8")
