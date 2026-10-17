"""Team coordination: the Manager AI, the Strategist ↔ Judge debate, and the shared team memory.

Manager AI     — reads the Reviewer's report, decides what each AI should focus on this week,
                 asks for extra news checks, approves (or refuses) extra debate rounds, and writes
                 the summary at the top of your email.
Debate         — Strategist proposes → Judge critiques → Strategist fixes → ... up to 4 rounds.
                 Rounds 5-10 happen only if the Judge says it's close AND problems are shrinking AND
                 the Manager approves. Hard safety rules are checked every round.
Team memory    — long-term lessons the Reviewer keeps (state/history/team_memory.json). Every AI reads
                 the lessons addressed to it, every week, until the Reviewer retires them.
"""
from __future__ import annotations

import json
from datetime import date

from . import history as H
from .llm import LLM

NORMAL_ROUNDS, MAX_ROUNDS = 4, 10
AGENTS = ("researcher", "strategist", "judge", "reviewer", "manager")


# ───────────────────────── team memory ─────────────────────────

def load_memory() -> list[dict]:
    try:
        return json.loads((H.DIR / "team_memory.json").read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def apply_memory_updates(memory: list[dict], updates: dict | None, today: str) -> list[dict]:
    """Reviewer adds, reinforces, or retires long-term lessons. Max 12 active lessons."""
    updates = updates or {}
    retire = {str(x) for x in updates.get("retire", [])}
    reinforce = {str(x) for x in updates.get("reinforce", [])}
    mem = [m for m in memory if m["id"] not in retire]
    for m in mem:
        if m["id"] in reinforce:
            m["times_confirmed"] = m.get("times_confirmed", 1) + 1
            m["last_confirmed"] = today
    next_id = max([int(m["id"].lstrip("L")) for m in memory] or [0]) + 1
    for a in updates.get("add", [])[:3]:
        if a.get("for") in AGENTS and a.get("lesson"):
            mem.append({"id": f"L{next_id}", "for": a["for"], "lesson": a["lesson"], "created": today,
                        "times_confirmed": 1, "last_confirmed": today})
            next_id += 1
    mem.sort(key=lambda m: (m.get("times_confirmed", 1), m.get("last_confirmed", "")), reverse=True)
    return mem[:12]


def save_memory(memory: list[dict]) -> None:
    H._d()
    (H.DIR / "team_memory.json").write_text(json.dumps(memory, indent=2))


def memory_for(memory: list[dict], agent: str) -> list[str]:
    return [m["lesson"] for m in memory if m["for"] in (agent, "all")]


MEMORY_SYSTEM = """You are the Reviewer, also the keeper of the team's LONG-TERM memory.
Weekly notes fade; long-term lessons stay until retired. Given the current lessons, this week's review,
and past weekly notes, decide:
- add: a NEW lesson only if a problem has shown up at least twice, or a single mistake was serious
  (a rule broken, a fact misread). Address it to one agent: researcher, strategist, judge, manager, or all.
- reinforce: ids of existing lessons that this week confirmed again.
- retire: ids of lessons that are outdated or proved wrong.
Be conservative — a short list of good lessons beats a long list of noise.
Respond with JSON only: {"add": [{"for": str, "lesson": str}], "reinforce": [ids], "retire": [ids]}"""


def update_memory(llm: LLM, review: dict, lessons: list[dict], today: str) -> tuple[list[dict], dict]:
    memory = load_memory()
    if review.get("first_week"):
        return memory, {}
    res = llm.ask_json(MEMORY_SYSTEM, json.dumps(
        {"current_lessons": memory, "this_week_review": review, "past_weekly_notes": lessons[-6:]}, default=str))
    if not res:  # rule fallback: promote a weekly instruction once it's been given 3 weeks in a row
        res = {"add": []}
        recent = [l for l in lessons if not l.get("first_week")][-3:]
        existing = {m["lesson"] for m in memory}
        for who in ("researcher", "strategist", "judge"):
            notes = [l.get(f"for_{who}") for l in recent]
            if len(notes) == 3 and notes[0] and len(set(notes)) == 1 and notes[0] not in existing:
                res["add"].append({"for": who, "lesson": notes[0]})
    return apply_memory_updates(memory, res, today), res


# ───────────────────────── Manager AI ─────────────────────────

MANAGER_SYSTEM = """You are the Manager AI of a small retirement-advice team for a beginner investor
(weekly budget, {years} years to retirement, "people still need soap" essentials philosophy).
Team: Researcher (facts and news), Strategist (creative plan), Judge (strict QA), Reviewer (grades
last week, keeps team memory). You do not pick stocks yourself — you hand out tasks.

From the Reviewer's report, team memory, and this week's candidate list, write this week's assignments:
- one specific focus for the Researcher, Strategist and Judge (plain English, one sentence each)
- extra_news: up to 3 tickers from the candidate list that deserve a closer news check
- why: one sentence explaining your priorities this week
Respond with JSON only:
{{"researcher": str, "strategist": str, "judge": str, "extra_news": [str], "why": str}}"""

EXTEND_SYSTEM = """You are the Manager AI. The Strategist and Judge have debated {rounds} rounds without
fully agreeing. Normal limit is 4; you may allow more (hard cap 10) ONLY if it's absolutely necessary:
the remaining problems are serious AND each round is clearly making progress. Otherwise stop — the safe
rules-only choices will fill any gaps. Respond with JSON only: {{"extend": bool, "reason": str}}"""

WRAPUP_SYSTEM = """You are the Manager AI. Write the short summary that goes at the top of the investor's
email. Plain English for a beginner, 3-4 sentences: what the team did this week, what was decided, any
disagreement and how it was settled, and the one thing the investor should do on buy day.
Respond with JSON only: {"summary": str}"""


class Manager:
    def __init__(self, llm: LLM):
        self.llm = llm

    def assign(self, p: dict, review: dict, memory: list[dict], candidates: list[str]) -> dict:
        res = self.llm.ask_json(MANAGER_SYSTEM.format(years=p["years_to_retirement"]), json.dumps(
            {"reviewer_report": {k: review.get(k) for k in ("summary", "judge_errors", "for_researcher",
                                                            "for_strategist", "for_judge")},
             "team_memory": memory, "candidates": candidates}, default=str))
        if res and "strategist" in res:
            res["extra_news"] = [t for t in res.get("extra_news", []) if t in candidates][:3]
            res["source"] = "AI manager"
            return res
        return {"researcher": "Check news and earnings dates for the top candidates.",
                "strategist": "Keep the core steady; pick the satellite stock with the best score and no red flags.",
                "judge": "Check the budget, the 5% limit, and anything bought right before earnings.",
                "extra_news": [], "why": "Standard week.",
                "source": "rule-based manager (add a free GEMINI_API_KEY for AI management)"}

    def allow_extension(self, rounds: int, debate: list[dict]) -> dict:
        problems = [d["n_problems"] for d in debate]
        improving = len(problems) >= 2 and problems[-1] < problems[-2]
        if rounds >= MAX_ROUNDS or not improving:
            return {"extend": False, "reason": "Hard cap reached." if rounds >= MAX_ROUNDS
                    else "Debate isn't making progress, so stopping at the normal limit."}
        res = self.llm.ask_json(EXTEND_SYSTEM.format(rounds=rounds), json.dumps(debate[-3:], default=str))
        if res and "extend" in res:
            return {"extend": bool(res["extend"]), "reason": res.get("reason", "")}
        return {"extend": False, "reason": "No AI manager available to approve extra rounds."}

    def wrap_up(self, context: dict) -> str:
        res = self.llm.ask_json(WRAPUP_SYSTEM, json.dumps(context, default=str))
        if res and res.get("summary"):
            return res["summary"]
        n = context.get("debate_rounds", 1)
        return (f"The team reviewed last week, researched {context.get('n_candidates', 0)} essentials stocks, and "
                f"settled this week's plan after {n} round{'s' if n != 1 else ''} of review. "
                f"On {context.get('buy_day', 'Friday')}, follow the buy list below and check the morning email first.")


# ───────────────────────── Strategist ↔ Judge debate ─────────────────────────

REVISE_SYSTEM = """You are the Strategist. The Judge reviewed your plan and found problems. Fix ONLY what
the Judge and the automatic rule check flagged; keep everything that was OK. Same rules as before: total
must equal the weekly budget exactly, whole dollars, core funds bought every week. If you disagree with a
Judge point, keep your choice but explain why in one sentence.
Respond with JSON only:
{"plan": [{"ticker": str, "amount": int, "reason": str}], "changes": "what you changed and why",
 "disagree": "anything you're pushing back on, or empty", "summary": str, "big_idea": str}"""


def debate(llm: LLM, strategist, judge, facts: dict, strategy: dict, check, focus_judge: str = "",
           manager: Manager | None = None) -> tuple[dict, dict, list[dict]]:
    """Returns (final strategy, final verdict, transcript)."""
    transcript, limit, rnd = [], NORMAL_ROUNDS, 0
    verdict = {}
    while True:
        rnd += 1
        hard = check(strategy["plan"])
        verdict = judge.review(facts, strategy, hard, transcript, focus_judge)
        bad = [v for v in verdict.get("verdicts", []) if str(v.get("verdict")).upper() != "OK"]
        n_problems = len(bad) + len(hard)
        entry = {"round": rnd, "plan": strategy["plan"], "strategist_note": strategy.get("changes") or strategy.get("summary"),
                 "disagree": strategy.get("disagree", ""), "judge_problems": bad, "rule_problems": hard,
                 "n_problems": n_problems, "judge_says": verdict.get("overall", "")}
        transcript.append(entry)
        if (verdict.get("all_ok") and not hard) or n_problems == 0:
            entry["outcome"] = "agreed"
            break
        if not llm.available:                     # rule-based team can't revise — stop after one round
            entry["outcome"] = "rules used"
            break
        if rnd >= limit:
            if limit < MAX_ROUNDS and verdict.get("continue") and manager:
                ok = manager.allow_extension(rnd, transcript)
                entry["manager_extension"] = ok
                if ok["extend"]:
                    limit = min(MAX_ROUNDS, limit + 2)   # extend 2 rounds at a time, never past 10
            if rnd >= limit:
                entry["outcome"] = "stopped at limit"
                break
        revised = llm.ask_json(REVISE_SYSTEM, json.dumps(
            {"facts": facts, "your_last_plan": strategy, "judge_verdicts": verdict.get("verdicts"),
             "judge_overall": verdict.get("overall"), "rule_problems": hard}, default=str))
        if not revised or not isinstance(revised.get("plan"), list) or not revised["plan"]:
            entry["outcome"] = "strategist couldn't revise"
            break
        strategy = {**strategy, **revised, "source": strategy.get("source", "AI strategist")}
    return strategy, verdict, transcript
