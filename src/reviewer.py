"""4th AI — the Reviewer (coach).

Every Sunday, before the other three AIs start, the Reviewer:
1. Grades last week's plan: how each purchase did since it was planned.
2. Checks the Judge's calls: when the Judge rejected a Strategist idea, did that help or hurt?
3. Adds up "if you had followed the Judge every week": money put in vs. what it's worth now.
4. Projects that habit 24 years forward (low / middle / high scenarios, and in today's dollars).
5. Writes feedback for the Researcher, Strategist and Judge — which they read this week.
"""
from __future__ import annotations

import json

from .llm import LLM
from .retire import bond_share

# ───────────────────────── projection ─────────────────────────

DEFAULT_ASSUMPTIONS = {"us": 0.07, "intl": 0.065, "bonds": 0.04, "satellite": 0.07,
                       "scenario_spread": 0.025, "inflation": 0.025, "yearly_budget_increase": 0.0}


def blended_return(years_left: float, p: dict, a: dict) -> float:
    b = bond_share(years_left)
    stocks = 1 - b
    sat = min(p["satellite_share"], stocks)
    core = stocks - sat
    us = core * p["us_share_of_stocks"]
    return us * a["us"] + (core - us) * a["intl"] + b * a["bonds"] + sat * a["satellite"]


def project(p: dict, start_value: float, years: float) -> dict:
    """Year-by-year simulation that follows the glide path (safer mix as retirement nears)."""
    a = {**DEFAULT_ASSUMPTIONS, **(p.get("projection") or {})}
    yrs = max(1, int(round(years)))
    out = {}
    for name, shift in (("low", -a["scenario_spread"]), ("middle", 0.0), ("high", a["scenario_spread"])):
        value, put_in, yearly = start_value, 0.0, p["weekly_budget"] * 52
        for t in range(yrs):
            r = blended_return(years - t, p, a) + shift
            value = value * (1 + r) + yearly * (1 + r / 2)   # contributions spread through the year
            put_in += yearly
            yearly *= 1 + a["yearly_budget_increase"]
        out[name] = {"value": round(value), "todays_dollars": round(value / (1 + a["inflation"]) ** yrs),
                     "avg_return": round(blended_return(years / 2, p, a) + shift, 4)}
    out["you_put_in_future"] = round(put_in)
    out["years"] = yrs
    out["assumptions"] = a
    return out


# ───────────────────────── grading ─────────────────────────

def _ret(now: float | None, then: float | None) -> float | None:
    if not now or not then:
        return None
    return now / then - 1


def grade_last_week(last: dict, prices_now: dict) -> dict:
    items = []
    for i in last.get("final", []):
        r = _ret(prices_now.get(i["ticker"]), i.get("price_at_plan"))
        items.append({"ticker": i["ticker"], "role": i["role"], "amount": i["amount"],
                      "return": None if r is None else round(r, 4),
                      "gain": None if r is None else round(i["amount"] * r, 2)})
    gains = [x["gain"] for x in items if x["gain"] is not None]
    week_gain = round(sum(gains), 2) if gains else None

    # Judge calls: Strategist tickers the Judge removed vs. what was bought instead.
    final_t = {i["ticker"] for i in last.get("final", [])}
    then = last.get("prices_at_plan", {})
    bought_sat = [i for i in last.get("final", []) if i["role"] == "satellite"]
    calls = []
    for s in last.get("strategist_plan", []):
        t = str(s.get("ticker", "")).upper()
        if t in final_t:
            continue
        rej = _ret(prices_now.get(t), then.get(t))
        alt = bought_sat[0]["ticker"] if bought_sat else last.get("core_us")
        alt_r = _ret(prices_now.get(alt), then.get(alt))
        if rej is None or alt_r is None:
            continue
        diff = alt_r - rej
        calls.append({"rejected": t, "instead": alt, "rejected_return": round(rej, 4),
                      "instead_return": round(alt_r, 4),
                      "outcome": "helped" if diff > 0.01 else "hurt" if diff < -0.01 else "no real difference"})
    return {"week_of": last.get("buy_date"), "items": items, "week_gain": week_gain, "judge_calls": calls}


def followed_totals(records: list[dict], prices_now: dict) -> dict:
    """If you'd bought exactly the Judge's final plan every week, using each week's planned prices."""
    put_in, value, weeks = 0.0, 0.0, 0
    for rec in records:
        weeks += 1
        for i in rec.get("final", []):
            put_in += i["amount"]
            p0, p1 = i.get("price_at_plan"), prices_now.get(i["ticker"])
            value += i["amount"] * (p1 / p0) if p0 and p1 else i["amount"]
    return {"weeks": weeks, "put_in": round(put_in, 2), "worth_now": round(value, 2),
            "gain": round(value - put_in, 2), "gain_pct": round(value / put_in - 1, 4) if put_in else 0.0}


# ───────────────────────── the Reviewer agent ─────────────────────────

REVIEWER_SYSTEM = """You are the Reviewer: a calm, honest coach who reviews last week's retirement advice
and helps the other three AIs (Researcher, Strategist, Judge) improve.

You get: last week's facts summary, the Strategist's proposal, the Judge's verdicts and final plan,
what each price did since, whether each Judge rejection helped or hurt, running totals, the 24-year
projection, and your past lessons.

Rules for being fair:
- One week of price moves is mostly random noise. Do NOT call advice wrong just because a price fell.
- Call something an ERROR only if the reasoning was wrong given what was known then (a fact misread,
  a rule ignored, news missed), or if the same problem shows up across several weeks of lessons.
- Be specific and short. Speak to each AI directly. Plain English a beginner can read.

Respond with JSON only:
{"summary": "2-3 sentences for the investor: how last week's plan did and what it means",
 "judge_errors": ["specific error the Judge made, if any"],
 "for_researcher": "one concrete instruction",
 "for_strategist": "one concrete instruction",
 "for_judge": "one concrete instruction",
 "confidence_note": "one sentence on how much to trust these conclusions given how little data there is"}"""


class Reviewer:
    def __init__(self, llm: LLM):
        self.llm = llm

    def review(self, last: dict | None, grade: dict | None, totals: dict, projection: dict,
               past_lessons: list[dict]) -> dict:
        if not last:
            return {"source": "Reviewer", "first_week": True,
                    "summary": "This is your first week, so there's nothing to review yet. "
                               "Starting next Sunday I'll grade each week's plan.",
                    "judge_errors": [], "for_researcher": "", "for_strategist": "", "for_judge": "",
                    "confidence_note": ""}
        payload = {"last_week": {k: last.get(k) for k in
                                 ("buy_date", "budget", "strategist_plan", "strategist_summary",
                                  "judge_verdicts", "judge_overall", "final")},
                   "what_happened": grade, "running_totals": totals,
                   "projection_middle": projection.get("middle"), "past_lessons": past_lessons[-4:]}
        res = self.llm.ask_json(REVIEWER_SYSTEM, json.dumps(payload, default=str))
        if res and "summary" in res:
            res["source"] = "AI reviewer"
            return res
        return self._rules(grade, totals, len(past_lessons))

    @staticmethod
    def _rules(grade: dict, totals: dict, n_lessons: int) -> dict:
        g = grade.get("week_gain")
        summary = (f"Last week's plan is {'up' if (g or 0) >= 0 else 'down'} about ${abs(g or 0):.2f} so far. "
                   if g is not None else "Couldn't price last week's buys. ")
        summary += (f"Following the Judge for {totals['weeks']} week(s): you'd have put in ${totals['put_in']:,.0f}, "
                    f"now worth about ${totals['worth_now']:,.0f}.")
        hurt = [c for c in grade.get("judge_calls", []) if c["outcome"] == "hurt"]
        errors = [f"Rejected {c['rejected']} ({c['rejected_return']:+.1%}) for {c['instead']} "
                  f"({c['instead_return']:+.1%}) — check whether the reason still holds." for c in hurt]
        early = totals["weeks"] < 4
        return {"source": "rule-based reviewer (add a free GEMINI_API_KEY for AI coaching)",
                "summary": summary, "judge_errors": errors if not early else [],
                "for_researcher": "Keep flagging earnings dates and negative company news.",
                "for_strategist": "Stay diversified; avoid picking the same stock many weeks in a row.",
                "for_judge": ("Too early to judge your calls — need at least 4 weeks." if early else
                              ("Re-check the rejection reasons above." if errors else "Your calls look reasonable.")),
                "confidence_note": "One week of results is mostly luck; patterns over months matter more."}


def feedback_for_agents(lessons: list[dict]) -> dict:
    """The most recent lessons, condensed for the other AIs' prompts."""
    recent = [l for l in lessons if not l.get("first_week")][-3:]
    return {"for_researcher": [l.get("for_researcher") for l in recent if l.get("for_researcher")],
            "for_strategist": [l.get("for_strategist") for l in recent if l.get("for_strategist")],
            "for_judge": [l.get("for_judge") for l in recent if l.get("for_judge")],
            "judge_errors": [e for l in recent for e in (l.get("judge_errors") or [])]}
