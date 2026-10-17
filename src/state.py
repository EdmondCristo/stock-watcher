"""Remembers which alerts were already sent so you don't get spammed.
Stored in state/alerts.json, which the GitHub workflow commits back to the repo."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

PATH = Path(__file__).resolve().parent.parent / "state" / "alerts.json"


def load() -> dict:
    try:
        return json.loads(PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save(state: dict) -> None:
    PATH.parent.mkdir(exist_ok=True)
    PATH.write_text(json.dumps(state, indent=2, sort_keys=True))


def should_alert(state: dict, key: str, cooldown_hours: float) -> bool:
    last = state.get(key)
    if not last:
        return True
    return datetime.now(timezone.utc) - datetime.fromisoformat(last) > timedelta(hours=cooldown_hours)


def mark(state: dict, key: str) -> None:
    state[key] = datetime.now(timezone.utc).isoformat()
