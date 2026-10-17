"""Retirement planner: target mix (glide path), weekly allocation, satellite research, Friday buy ranges."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yaml

from . import data, news
from .agents import fundamentals, price_stats, score_universe
from .indicators import add_indicators

ROOT = Path(__file__).resolve().parent.parent
PLAN_PATH = ROOT / "state" / "plan.json"
WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4}


def load_portfolio() -> dict:
    p = yaml.safe_load((ROOT / "portfolio.yaml").read_text())
    if p.get("retirement_year"):   # counts down automatically each year
        p["years_to_retirement"] = max(0, int(p["retirement_year"]) - date.today().year)
    return p


def next_buy_date(today: date, buy_day: str) -> date:
    wd = WEEKDAYS.get(str(buy_day).lower(), 4)
    days = (wd - today.weekday()) % 7
    return today + timedelta(days=days or 7)


# ───────────────────────── target mix ─────────────────────────

def bond_share(years: float) -> float:
    """Glide path: 10% bonds when 25+ years out, rising steadily to 50% at retirement."""
    y = max(0.0, min(25.0, float(years)))
    return 0.10 + 0.40 * (1 - y / 25)


def targets(p: dict) -> dict[str, float]:
    b = bond_share(p["years_to_retirement"])
    stocks = 1 - b
    sat = min(p["satellite_share"], stocks)
    core = stocks - sat
    us = core * p["us_share_of_stocks"]
    cf = p["core_funds"]
    return {cf["us_stocks"]: us, cf["intl_stocks"]: core - us, cf["bonds"]: b, "SATELLITE": sat}


def current_mix(p: dict) -> dict[str, float]:
    h = {k.upper(): float(v) for k, v in (p.get("holdings") or {}).items()}
    total = sum(h.values())
    if total <= 0:
        return {}
    core = {v.upper() for v in p["core_funds"].values()}
    mix = {k: v / total for k, v in h.items() if k in core}
    mix["SATELLITE"] = sum(v for k, v in h.items() if k not in core) / total
    return mix


def single_stock_weights(p: dict) -> dict[str, float]:
    h = {k.upper(): float(v) for k, v in (p.get("holdings") or {}).items()}
    total = sum(h.values()) or 1
    core = {v.upper() for v in p["core_funds"].values()}
    return {k: v / total for k, v in h.items() if k not in core}


def allocate(budget: float, tgt: dict, cur: dict) -> dict[str, int]:
    """Split this week's money. Underweight parts get a bit extra, overweight parts a bit less,
    so new money slowly pulls you back to target — no selling needed."""
    raw = {k: max(0.0, t + (t - cur.get(k, t))) for k, t in tgt.items()}
    s = sum(raw.values()) or 1
    exact = {k: budget * v / s for k, v in raw.items()}
    out = {k: int(v) for k, v in exact.items()}
    left = int(round(budget)) - sum(out.values())
    for k in sorted(exact, key=lambda k: exact[k] - out[k], reverse=True)[:max(left, 0)]:
        out[k] += 1
    return out


# ───────────────────────── research ─────────────────────────

def _events(ticker: str, today: date, buy: date) -> dict:
    """Earnings and ex-dividend dates that fall between now and buy day."""
    ev = {"earnings_date": None, "ex_dividend_date": None, "earnings_before_buy": False,
          "ex_div_before_buy": False}
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        cal = t.calendar or {}
        ed = cal.get("Earnings Date") if isinstance(cal, dict) else None
        if ed:
            d = pd.Timestamp(ed[0] if isinstance(ed, (list, tuple)) else ed).date()
            ev["earnings_date"] = str(d)
            ev["earnings_before_buy"] = today <= d <= buy
        xd = cal.get("Ex-Dividend Date") if isinstance(cal, dict) else None
        if xd:
            d = pd.Timestamp(xd).date()
            ev["ex_dividend_date"] = str(d)
            ev["ex_div_before_buy"] = today <= d < buy
    except Exception:
        pass
    return ev


def research_satellites(universe: list[str], p: dict, today: date, buy: date, demo: bool = False,
                        news_for_top: int = 4) -> pd.DataFrame:
    rows = []
    weights = single_stock_weights(p)
    for t in universe:
        try:
            raw = data.demo_history(t, crash_end=False) if demo else data.get_history(t, 5)
            df = add_indicators(raw, 10, 20, 2.0)
            info = {} if demo else data.get_info(t)
            last = df.iloc[-1]
            rows.append({
                "ticker": t, **fundamentals(info), **price_stats(df["Close"]),
                "price": float(last["Close"]), "pct_b": float(last["pct_b"]),
                "bb_lower": float(last["bb_lower"]), "bb_mid": float(last["bb_mid"]),
                "bb_upper": float(last["bb_upper"]), "ema10": float(last["ema"]),
                "your_weight": weights.get(t.upper(), 0.0),
                **({} if demo else _events(t, today, buy)),
            })
        except Exception as ex:
            print(f"[research] {t} skipped: {ex}")
    if not rows:
        raise RuntimeError("No satellite data loaded")
    table = score_universe(rows)
    table["news"] = None
    table["sentiment"] = 0.0
    for t in list(table.index[:news_for_top]):
        arts = _demo_news(t) if demo else news.fetch(t, table.loc[t, "name"] if "name" in table else None, 6, 7)
        table.at[t, "news"] = [a.title for a in arts[:4]]
        table.at[t, "sentiment"] = news.aggregate_sentiment(arts)
    return table


def _demo_news(t):
    now = datetime.now(timezone.utc)
    arts = [news.Article(f"{t} raises dividend for 30th straight year", "Demo", "", now),
            news.Article(f"{t} sales steady as shoppers stick with staples", "Demo", "", now)]
    for a in arts:
        a.sentiment = news._score(a.title)
    return arts


def eligible(row: pd.Series, p: dict) -> tuple[bool, str]:
    if row.get("your_weight", 0) >= p["max_single_stock"]:
        return False, f"already {row['your_weight']:.0%} of your portfolio (limit {p['max_single_stock']:.0%})"
    if row.get("pct_b", 0.5) > 0.9:
        return False, "price stretched near the top Bollinger Band"
    if row.get("earnings_before_buy"):
        return False, f"earnings report on {row.get('earnings_date')}, before buy day"
    if row.get("red_flag") is True:
        return False, "the Researcher flagged worrying news"
    return True, ""


def buy_range(row: pd.Series) -> dict:
    price = float(row["price"])
    return {"buy_up_to": round(price * 1.03, 2),           # skip if it jumps >3% before Friday
            "great_below": round(float(row["bb_lower"]), 2),  # bonus: at/below lower band
            "price_at_plan": round(price, 2)}


def base_plan(p: dict, table: pd.DataFrame, buy: date) -> dict:
    """The plain, rules-only plan the AIs start from."""
    tgt, cur = targets(p), current_mix(p)
    alloc = allocate(p["weekly_budget"], tgt, cur)
    cf = p["core_funds"]
    labels = {cf["us_stocks"]: "Core: US stock market", cf["intl_stocks"]: "Core: international stocks",
              cf["bonds"]: "Core: bonds"}
    items = [{"ticker": k, "amount": v, "role": "core", "label": labels[k],
              "reason": "Steady weekly buying (dollar-cost averaging) — no timing needed for index funds."}
             for k, v in alloc.items() if k != "SATELLITE" and v > 0]
    sat_amt = alloc.get("SATELLITE", 0)
    skipped = []
    if sat_amt > 0:
        pick = None
        for t, row in table.iterrows():
            ok, why = eligible(row, p)
            if ok:
                pick = (t, row)
                break
            skipped.append({"ticker": t, "why": why})
        if pick:
            t, row = pick
            items.append({"ticker": t, "amount": sat_amt, "role": "satellite",
                          "label": f"Satellite: {row.get('name') or t}",
                          "reason": f"Top-ranked essentials stock this week (score {row['score']:+.2f}).",
                          **buy_range(row)})
        else:  # nothing eligible → put it in the US core fund
            items[0]["amount"] += sat_amt
    return {"buy_date": str(buy), "budget": p["weekly_budget"], "targets": tgt, "current": cur,
            "items": items, "skipped": skipped[:5]}


# ───────────────────────── hard safety rules ─────────────────────────

def check_plan(plan_items: list[dict], p: dict, table: pd.DataFrame) -> list[dict]:
    """Rules no AI can override. Returns problems (empty list = plan passes)."""
    problems = []
    core = {v.upper() for v in p["core_funds"].values()}
    total = sum(float(i.get("amount", 0)) for i in plan_items)
    if abs(total - p["weekly_budget"]) > 1:
        problems.append({"ticker": "ALL", "verdict": "WRONG",
                         "reason": f"Plan spends ${total:.0f}, budget is ${p['weekly_budget']}."})
    sat_total = 0.0
    for i in plan_items:
        t = str(i.get("ticker", "")).upper()
        amt = float(i.get("amount", 0))
        if amt < 1:
            problems.append({"ticker": t, "verdict": "WRONG", "reason": "Amount under $1."})
        if t in core:
            continue
        sat_total += amt
        if t not in table.index:
            problems.append({"ticker": t, "verdict": "WRONG", "reason": "Not on your researched essentials list."})
            continue
        ok, why = eligible(table.loc[t], p)
        if not ok:
            problems.append({"ticker": t, "verdict": "RISKY", "reason": why})
    if sat_total > p["weekly_budget"] * p["satellite_share"] * 2 + 1:
        problems.append({"ticker": "SATELLITE", "verdict": "WRONG",
                         "reason": f"${sat_total:.0f} in individual stocks is more than double your satellite share."})
    return problems


def save_plan(plan: dict) -> None:
    PLAN_PATH.parent.mkdir(exist_ok=True)
    PLAN_PATH.write_text(json.dumps(plan, indent=2, default=str))


def load_plan() -> dict | None:
    try:
        return json.loads(PLAN_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None
