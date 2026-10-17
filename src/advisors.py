"""The three retirement AIs.

1. Researcher  — turns raw data into a clean fact sheet (and reads the news).
2. Strategist  — creative: proposes this Friday's buy plan and one bigger idea.
3. Judge       — QA inspector: checks the Strategist against the facts and the rules,
                 marks each item OK / RISKY / WRONG, and decides the final plan.

Hard safety rules (src/retire.check_plan) run after the Judge and can't be overridden.
Each agent falls back to plain rules when no free AI key is configured.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from .llm import LLM

SAFE_COLS = ["name", "sector", "score", "price", "pct_b", "ema10", "bb_lower", "bb_upper", "cagr_5y",
             "ret_1y", "rev_growth", "margin", "roe", "div_yield", "payout", "fwd_pe", "debt_eq",
             "volatility", "max_drawdown", "your_weight", "earnings_date", "earnings_before_buy",
             "ex_dividend_date", "ex_div_before_buy", "sentiment", "news"]


def _records(table: pd.DataFrame, n: int) -> list[dict]:
    view = table[[c for c in SAFE_COLS if c in table.columns]].head(n)
    view = view.astype(object).where(pd.notna(view), None)
    out = []
    for t, r in view.iterrows():
        d = {"ticker": t}
        for k, v in r.items():
            if isinstance(v, (float, np.floating)):
                v = round(float(v), 4)
            elif isinstance(v, np.bool_):
                v = bool(v)
            d[k] = v
        out.append(d)
    return out


# ───────────────────────── 1. Researcher ─────────────────────────

RESEARCHER_SYSTEM = """You are a careful financial researcher. You do NOT give advice.
For each stock you get numbers and recent headlines. Summarize what the headlines actually say about
the business in one plain-English sentence a beginner understands, and flag anything that could hurt
a long-term owner (lawsuits, recalls, dividend cuts, guidance cuts, big debt, accounting issues).
If "reviewer_feedback" or "manager_focus" is included, follow it.
Respond with JSON only: {"TICKER": {"summary": str, "red_flag": bool, "flag_reason": str}, ...}"""


class Researcher:
    def __init__(self, llm: LLM):
        self.llm = llm

    def fact_sheet(self, p: dict, plan: dict, table: pd.DataFrame, feedback: dict | None = None,
                   focus: str = "") -> dict:
        feedback = feedback or {}
        cands = _records(table, 8)
        with_news = {c["ticker"]: {"headlines": c.get("news") or [], "sentiment": c.get("sentiment")}
                     for c in cands if c.get("news")}
        if with_news and feedback.get("for_researcher"):
            with_news["reviewer_feedback"] = feedback["for_researcher"]
        if with_news and focus:
            with_news["manager_focus"] = focus
        notes = self.llm.ask_json(RESEARCHER_SYSTEM, json.dumps(with_news)) if with_news else None
        if notes:
            notes.pop("reviewer_feedback", None)
            notes.pop("manager_focus", None)
        for c in cands:
            n = (notes or {}).get(c["ticker"]) or {}
            if n:
                c["news_summary"] = n.get("summary")
                c["red_flag"] = bool(n.get("red_flag"))
                c["flag_reason"] = n.get("flag_reason")
            elif c.get("news"):
                s = c.get("sentiment") or 0
                c["news_summary"] = f"Headline tone {'positive' if s > 0.15 else 'negative' if s < -0.15 else 'neutral'}."
                c["red_flag"] = s < -0.3
                c["flag_reason"] = "Mostly negative headlines" if s < -0.3 else ""
        for c in cands:
            if c.get("red_flag") and c["ticker"] in table.index:
                table.at[c["ticker"], "red_flag"] = True
        return {
            "buy_date": plan["buy_date"], "weekly_budget": p["weekly_budget"],
            "years_to_retirement": p["years_to_retirement"],
            "target_mix": {k: round(v, 3) for k, v in plan["targets"].items()},
            "current_mix": {k: round(v, 3) for k, v in plan["current"].items()} or "starting fresh",
            "rules_only_plan": plan["items"],
            "satellite_candidates": cands,
            "skipped_this_week": plan["skipped"],
            "reviewer_feedback": feedback,
            "source": "AI researcher" if notes else "rule-based researcher",
        }


# ───────────────────────── 2. Strategist ─────────────────────────

STRATEGIST_SYSTEM = """You are a creative but disciplined retirement strategist for a beginner investing
a small weekly amount for {years} years. Philosophy: "people still need soap" — own durable essentials
businesses, but the core index funds do most of the work.

You get a fact sheet and a rules-only starting plan for the coming {buy_day}. Improve it if you can:
- You may swap the satellite stock for another candidate, or split the satellite money between two.
- You may shift a few dollars between core funds if the current mix is off target.
- The total MUST equal the weekly budget exactly. Whole dollars. Fractional shares are assumed.
- Do NOT try to time the core index funds; buy them every week.
- Avoid stocks with an earnings report before buy day or a red flag, unless you explain clearly.
- If the satellite stock pays an ex-dividend date before buy day, mention it.
Read "reviewer_feedback" in the facts (lessons from past weeks) and apply it.
Also give ONE bigger creative idea for the investor to think about (not part of this week's plan) —
e.g. a habit, an account feature, a long-term theme. Keep it practical and honest.

Respond with JSON only:
{{"plan": [{{"ticker": str, "amount": int, "reason": "one sentence"}}],
  "summary": "2-3 plain sentences explaining this week's plan",
  "big_idea": "2-3 sentences"}}"""

EVERGREEN_IDEAS = [
    "Turn on automatic dividend reinvestment (DRIP) in your brokerage so every dividend buys more shares for free.",
    "If your job offers a 401(k) match, contributing enough to get the full match is an instant, guaranteed return — check that before anything else.",
    "Set up an automatic weekly transfer to your brokerage on payday so investing happens even on busy weeks.",
    "Consider whether a Roth IRA fits you: money grows tax-free and can be withdrawn tax-free in retirement.",
    "Each year, raise your weekly amount a little (even $5) when you get a raise — over 24 years it adds up a lot.",
]


class Strategist:
    def __init__(self, llm: LLM):
        self.llm = llm

    def propose(self, facts: dict, p: dict, week_no: int, focus: str = "") -> dict:
        sys = STRATEGIST_SYSTEM.format(years=p["years_to_retirement"], buy_day=p.get("buy_day", "Friday"))
        if focus:
            sys += f"\nThe Manager asked you to focus on this week: {focus}"
        res = self.llm.ask_json(sys, json.dumps(facts, default=str))
        if res and isinstance(res.get("plan"), list) and res["plan"]:
            res["source"] = "AI strategist"
            return res
        plan = [{"ticker": i["ticker"], "amount": i["amount"], "reason": i["reason"]} for i in facts["rules_only_plan"]]
        return {"plan": plan, "source": "rule-based strategist (add a free GEMINI_API_KEY for AI ideas)",
                "summary": "Following the standard plan: most money into broad index funds, a small slice into "
                           "this week's top-ranked essentials stock.",
                "big_idea": EVERGREEN_IDEAS[week_no % len(EVERGREEN_IDEAS)]}


# ───────────────────────── 3. Judge ─────────────────────────

JUDGE_SYSTEM = """You are the Judge: a strict, fair QA reviewer for a beginner's retirement plan.
You receive the Researcher's fact sheet, the rules-only plan, and the Strategist's proposed plan and idea.
Check every item of the Strategist's plan against the facts and these rules:
- total equals the weekly budget; whole dollars
- core index funds bought every week, not timed
- no single stock above the max weight; no earnings before buy day; no red-flag stock without good reason
- reasons must match the facts (call out anything the Strategist claimed that the data doesn't support)
Also judge the big idea: is it sound and relevant for a beginner?
The Reviewer graded past weeks; its feedback is in facts.reviewer_feedback ("for_judge", "judge_errors").
Answer the Reviewer directly: say what you are correcting this week, or explain politely why you disagree.

Mark each item OK, RISKY, or WRONG with a one-sentence reason in plain English.
Then give the approved plan: keep the Strategist's items you marked OK; replace RISKY/WRONG ones
with the rules-only plan's choice. Total must still equal the budget.

Respond with JSON only:
{"verdicts": [{"ticker": str, "verdict": "OK|RISKY|WRONG", "reason": str}],
 "idea_verdict": {"verdict": "OK|RISKY|WRONG", "reason": str},
 "approved_plan": [{"ticker": str, "amount": int}],
 "overall": "2 sentences: what was right and what was wrong with the advice",
 "response_to_reviewer": "1-2 sentences",
 "all_ok": true if every item is OK and the plan can be used as-is,
 "continue": true ONLY if the plan is not OK yet but the Strategist is close and one more round would likely fix it}

This may be one round of a back-and-forth with the Strategist ("debate" in the payload shows earlier rounds).
If the Strategist fixed what you asked, say so. Don't invent new objections each round just to keep going.
"manager_focus" (if present) is what the Manager asked you to pay special attention to this week."""


class Judge:
    def __init__(self, llm: LLM):
        self.llm = llm

    def review(self, facts: dict, strategy: dict, hard_problems: list[dict], debate: list | None = None,
               focus: str = "") -> dict:
        payload = {"facts": facts, "strategist": strategy, "automatic_rule_check": hard_problems,
                   "debate": debate or [], "manager_focus": focus}
        res = self.llm.ask_json(JUDGE_SYSTEM, json.dumps(payload, default=str))
        if res and isinstance(res.get("approved_plan"), list):
            res["source"] = "AI judge"
            return res
        bad = {pr["ticker"] for pr in hard_problems}
        verdicts = []
        for i in strategy["plan"]:
            prob = next((pr for pr in hard_problems if pr["ticker"] == str(i["ticker"]).upper()), None)
            verdicts.append({"ticker": i["ticker"], "verdict": prob["verdict"] if prob else "OK",
                             "reason": prob["reason"] if prob else "Matches the facts and the rules."})
        for pr in hard_problems:
            if pr["ticker"] in ("ALL", "SATELLITE"):
                verdicts.append(pr)
        approved = strategy["plan"] if not bad else [
            {"ticker": i["ticker"], "amount": i["amount"]} for i in facts["rules_only_plan"]]
        return {"verdicts": verdicts, "approved_plan": approved, "all_ok": not bad, "continue": False,
                "idea_verdict": ({"verdict": "OK", "reason": "A standard, low-risk habit for long-term investors."}
                                 if strategy.get("big_idea") in EVERGREEN_IDEAS else
                                 {"verdict": "RISKY", "reason": "Couldn't be reviewed by the AI Judge — think it over before acting."}),
                "response_to_reviewer": ("Applying the Reviewer's notes: " + "; ".join(facts["reviewer_feedback"].get("for_judge", [])[-1:])
                                         if facts.get("reviewer_feedback", {}).get("for_judge") else ""),
                "overall": "Plan follows your rules." if not bad else
                           "Some suggestions broke your rules, so the safe rules-only plan is used instead.",
                "source": "rule-based judge (add a free GEMINI_API_KEY for AI review)"}

    FRIDAY_SYSTEM = """You are the Judge doing a final pre-market check on buy day. For each planned
satellite stock you get the plan from Sunday, today's price, and this week's headlines. Decide GO
(buy as planned), WAIT (skip this week; money goes to the core US fund), with one plain sentence why.
Respond with JSON only: {"TICKER": {"decision": "GO|WAIT", "reason": str}}"""

    def friday(self, checks: dict) -> dict | None:
        return self.llm.ask_json(self.FRIDAY_SYSTEM, json.dumps(checks, default=str))
