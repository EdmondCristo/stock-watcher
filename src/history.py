"""Weekly history: one record per Sunday, a running scorecard, saved report pages, and Reviewer lessons.
Everything lives in state/history/ so the existing workflow saves it automatically."""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIR = Path(os.getenv("HISTORY_DIR", ROOT / "state" / "history"))


def _d() -> Path:
    DIR.mkdir(parents=True, exist_ok=True)
    return DIR


def load_records() -> list[dict]:
    if not DIR.exists():
        return []
    recs = []
    for f in sorted(DIR.glob("*-plan.json")):
        try:
            recs.append(json.loads(f.read_text()))
        except json.JSONDecodeError:
            pass
    return recs


def save_record(rec: dict) -> None:
    (_d() / f"{rec['date']}-plan.json").write_text(json.dumps(rec, indent=2, default=str))


def save_report(date: str, html: str) -> None:
    (_d() / f"{date}-report.html").write_text(f"<!doctype html><meta charset='utf-8'><title>Plan {date}</title>{html}")


SCORE_COLS = ["week", "buy_date", "ticker", "role", "amount", "price_at_plan", "judge_verdict"]


def append_scorecard(rows: list[dict]) -> None:
    f = _d() / "scorecard.csv"
    new = not f.exists()
    with f.open("a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=SCORE_COLS, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerows(rows)


def load_lessons() -> list[dict]:
    try:
        return json.loads((DIR / "lessons.json").read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_lesson(lesson: dict, keep: int = 12) -> None:
    lessons = [l for l in load_lessons() if l.get("date") != lesson.get("date")] + [lesson]
    (_d() / "lessons.json").write_text(json.dumps(lessons[-keep:], indent=2, default=str))
