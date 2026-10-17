"""Runs the Sunday retirement report and the Friday pre-market check, and builds their emails."""
from __future__ import annotations

import html
from datetime import datetime
from zoneinfo import ZoneInfo

from . import data, news
from . import retire as R
from .advisors import Judge, Researcher, Strategist
from .emailer import send
from .indicators import add_indicators
from .llm import LLM

NY = ZoneInfo("America/New_York")
e = html.escape
BADGE = {"OK": ("✅", "#15803d"), "RISKY": ("⚠️", "#a16207"), "WRONG": ("❌", "#b91c1c"),
         "GO": ("✅", "#15803d"), "WAIT": ("⏸️", "#a16207"), "CHANGED": ("🔄", "#1d4ed8")}


def _enrich(items: list[dict], p: dict, table) -> list[dict]:
    cf = p["core_funds"]
    labels = {cf["us_stocks"].upper(): "Core: US stock market", cf["intl_stocks"].upper(): "Core: international stocks",
              cf["bonds"].upper(): "Core: bonds"}
    out = []
    for i in items:
        t = str(i["ticker"]).upper()
        d = {"ticker": t, "amount": int(round(float(i["amount"]))), "reason": i.get("reason", "")}
        if t in labels:
            d.update(role="core", label=labels[t])
        else:
            row = table.loc[t]
            d.update(role="satellite", label=f"Satellite: {row.get('name') or t}", **R.buy_range(row))
        out.append(d)
    return out


# ───────────────────────── Sunday ─────────────────────────

def run_sunday(cfg: dict, demo: bool = False, dry_run: bool = False) -> int:
    p = R.load_portfolio()
    llm = LLM(cfg.get("llm", {}))
    today = datetime.now(NY).date()
    buy = R.next_buy_date(today, p.get("buy_day", "Friday"))
    table = R.research_satellites(cfg["consultant"]["universe"], p, today, buy, demo=demo)

    plan = R.base_plan(p, table, buy)
    facts = Researcher(llm).fact_sheet(p, plan, table)
    if "red_flag" in table.columns and table["red_flag"].fillna(False).any():
        plan = R.base_plan(p, table, buy)          # re-pick if the Researcher flagged the first choice
        facts["rules_only_plan"] = plan["items"]
        facts["skipped_this_week"] = plan["skipped"]

    strategy = Strategist(llm).propose(facts, p, today.isocalendar()[1])
    hard = R.check_plan(strategy["plan"], p, table)
    verdict = Judge(llm).review(facts, strategy, hard)

    final = verdict.get("approved_plan") or plan["items"]
    if R.check_plan(final, p, table):              # hard rules always win
        final = plan["items"]
        verdict["overall"] = (verdict.get("overall", "") + " The approved plan still broke a safety rule, "
                              "so the rules-only plan is used.").strip()
    reasons = {str(i["ticker"]).upper(): i.get("reason", "") for i in strategy["plan"]}
    for i in final:
        i.setdefault("reason", reasons.get(str(i["ticker"]).upper()) or next(
            (b["reason"] for b in plan["items"] if b["ticker"] == i["ticker"]), ""))
    final = _enrich(final, p, table)

    saved = {"created": datetime.now(NY).isoformat(), "buy_date": str(buy), "budget": p["weekly_budget"],
             "core_us": p["core_funds"]["us_stocks"], "items": final}
    if not demo:
        R.save_plan(saved)

    body = render_sunday(p, plan, facts, strategy, verdict, final, table, buy)
    send(f"🧼 Retirement plan for {buy:%a %b %d}: ${p['weekly_budget']}", body,
         cfg["alerts"].get("email_to") or None, dry_run=dry_run or demo)
    return 0


def _pct(v):
    return "—" if v is None or v != v else f"{v:.0%}"


def render_sunday(p, plan, facts, strategy, verdict, final, table, buy) -> str:
    tgt, cur = plan["targets"], plan["current"]
    mix_rows = "".join(
        f"<tr><td>{e(k if k != 'SATELLITE' else 'Individual stocks')}</td><td>{_pct(v)}</td><td>{_pct(cur.get(k)) if cur else '—'}</td></tr>"
        for k, v in tgt.items())
    buy_rows = ""
    for i in final:
        rng = (f"<br><span style='font-size:12px;color:#555'>Buy if ≤ ${i['buy_up_to']:,.2f} on {buy:%A} "
               f"(was ${i['price_at_plan']:,.2f}). Great price below ${i['great_below']:,.2f}.</span>"
               if i["role"] == "satellite" else "")
        buy_rows += (f"<tr><td style='padding:6px 8px'><b>{e(i['ticker'])}</b><br><span style='font-size:12px;color:#555'>"
                     f"{e(i['label'])}</span></td><td style='padding:6px 8px;font-size:16px'><b>${i['amount']}</b></td>"
                     f"<td style='padding:6px 8px;font-size:13px'>{e(i.get('reason', ''))}{rng}</td></tr>")
    verdict_rows = "".join(
        f"<li>{BADGE.get(str(v.get('verdict')).upper(), ('•', '#555'))[0]} <b>{e(str(v.get('ticker')))}</b>: "
        f"{e(str(v.get('reason', '')))}</li>" for v in verdict.get("verdicts", []))
    iv = verdict.get("idea_verdict") or {}
    cand_rows = "".join(
        f"<tr><td>{e(c['ticker'])}</td><td>{c['score']:+.2f}</td><td>{_pct(c.get('cagr_5y'))}</td>"
        f"<td>{_pct(c.get('div_yield'))}</td><td>{c.get('pct_b', 0):.2f}</td>"
        f"<td style='font-size:12px'>{e(c.get('news_summary') or '')}"
        f"{' 🚩 ' + e(c.get('flag_reason') or '') if c.get('red_flag') else ''}"
        f"{' 📅 earnings ' + e(str(c.get('earnings_date'))) if c.get('earnings_before_buy') else ''}</td></tr>"
        for c in facts["satellite_candidates"][:6])
    skipped = "".join(f"<li>{e(s['ticker'])}: {e(s['why'])}</li>" for s in facts.get("skipped_this_week", []))
    return f"""<div style="font-family:Arial,sans-serif;max-width:720px">
<h2 style="margin-bottom:4px">Your plan for {buy:%A, %B %d}</h2>
<p style="margin-top:0;color:#555">${p['weekly_budget']} this week · {p['years_to_retirement']} years to retirement.
You'll get a quick go/wait check that morning before the market opens.</p>
<h3>1. What to buy on {buy:%A}</h3>
<table style="border-collapse:collapse;width:100%" border="1" cellpadding="0">{buy_rows}</table>
<h3>2. The Judge's review</h3>
<p style="margin:4px 0">{e(str(verdict.get('overall', '')))}</p><ul style="font-size:13px">{verdict_rows}</ul>
<h3>3. The Strategist's thinking</h3>
<p style="margin:4px 0">{e(strategy.get('summary', ''))}</p>
<p style="margin:8px 0;padding:8px 12px;background:#f5f5f4;border-radius:6px"><b>💡 Big idea:</b> {e(strategy.get('big_idea', ''))}<br>
<span style="font-size:12px">Judge: {BADGE.get(str(iv.get('verdict')).upper(), ('•', ''))[0]} {e(str(iv.get('reason', '')))}</span></p>
<h3>4. Your target mix</h3>
<table style="border-collapse:collapse;font-size:13px" border="1" cellpadding="4">
<tr><th>Part</th><th>Target</th><th>You now</th></tr>{mix_rows}</table>
<p style="font-size:12px;color:#555">Bonds rise slowly from ~10% now to ~50% at retirement, so your plan gets safer over time.</p>
<h3>5. Researcher's notes: essentials stocks this week</h3>
<table style="border-collapse:collapse;font-size:12px" border="1" cellpadding="4">
<tr><th>Ticker</th><th>Score</th><th>5y growth</th><th>Dividend</th><th>%B</th><th>News</th></tr>{cand_rows}</table>
{f"<p style='font-size:12px'>Skipped this week:</p><ul style='font-size:12px'>{skipped}</ul>" if skipped else ""}
<p style="font-size:11px;color:#888">Researcher: {e(facts['source'])} · Strategist: {e(strategy['source'])} · Judge: {e(verdict['source'])}.
Educational tool, not financial advice. You make the final decision and place any trades yourself.</p></div>"""


# ───────────────────────── Friday ─────────────────────────

def run_friday(cfg: dict, demo: bool = False, dry_run: bool = False) -> int:
    plan = R.load_plan()
    if demo and not plan:
        plan = {"buy_date": str(datetime.now(NY).date()), "budget": 75, "core_us": "VTI", "items": [
            {"ticker": "VTI", "amount": 44, "role": "core", "label": "Core: US stock market"},
            {"ticker": "VXUS", "amount": 11, "role": "core", "label": "Core: international stocks"},
            {"ticker": "BND", "amount": 9, "role": "core", "label": "Core: bonds"},
            {"ticker": "CL", "amount": 11, "role": "satellite", "label": "Satellite: CL",
             "buy_up_to": 1.0, "great_below": 0.5, "price_at_plan": 0.97}]}
    if not plan:
        print("No saved plan yet — run the Sunday report first.")
        return 0
    checks, results = {}, []
    for i in plan["items"]:
        if i["role"] != "satellite":
            results.append({**i, "decision": "GO", "why": "Index fund — buy every week."})
            continue
        try:
            raw = data.demo_history(i["ticker"]) if demo else data.get_history(i["ticker"], 1)
            price = float(raw["Close"].iloc[-1])
            if demo:
                i["buy_up_to"], i["great_below"] = round(price * 1.01, 2), round(price * 0.9, 2)
            arts = [] if demo else news.fetch(i["ticker"], None, 6, 5)
            sent = news.aggregate_sentiment(arts)
        except Exception as ex:
            results.append({**i, "decision": "WAIT", "why": f"Couldn't get today's price ({ex})."})
            continue
        if price > i["buy_up_to"]:
            dec, why = "WAIT", f"Price ${price:,.2f} is above your buy-up-to ${i['buy_up_to']:,.2f}."
        elif sent < -0.3:
            dec, why = "WAIT", "This week's news turned clearly negative."
        else:
            bonus = " 🎯 It's at a great price." if price <= i["great_below"] else ""
            dec, why = "GO", f"Price ${price:,.2f} is within range and news looks okay.{bonus}"
        checks[i["ticker"]] = {"plan": i, "price_today": price, "headlines": [a.title for a in arts[:5]],
                               "rule_decision": dec}
        results.append({**i, "decision": dec, "why": why, "price_today": price})

    ai = Judge(LLM(cfg.get("llm", {}))).friday(checks) if checks else None
    for r in results:
        a = (ai or {}).get(r["ticker"])
        if a and r["decision"] == "GO" and str(a.get("decision")).upper() == "WAIT":   # AI may only make it safer
            r["decision"], r["why"] = "WAIT", f"Judge: {a.get('reason', '')}"
        elif a and r["decision"] == "GO":
            r["why"] += f" Judge: {a.get('reason', '')}"

    moved = sum(r["amount"] for r in results if r["role"] == "satellite" and r["decision"] == "WAIT")
    if moved:
        for r in results:
            if r["ticker"] == plan["core_us"]:
                r["amount"] += moved
                r["decision"] = "CHANGED"
                r["why"] = f"+${moved} moved here from the satellite stock you're skipping this week."
    rows = "".join(
        f"<tr><td style='padding:6px 8px;font-size:18px'>{BADGE[r['decision']][0]}</td>"
        f"<td style='padding:6px 8px'><b>{e(r['ticker'])}</b><br><span style='font-size:12px;color:#555'>{e(r['label'])}</span></td>"
        f"<td style='padding:6px 8px;font-size:16px'>{'skip' if r['role'] == 'satellite' and r['decision'] == 'WAIT' else '$' + str(r['amount'])}</td>"
        f"<td style='padding:6px 8px;font-size:13px;color:{BADGE[r['decision']][1]}'>{e(r['why'])}</td></tr>"
        for r in results)
    total = sum(r["amount"] for r in results if not (r["role"] == "satellite" and r["decision"] == "WAIT"))
    body = f"""<div style="font-family:Arial,sans-serif;max-width:720px">
<h2>Buy-day check · {datetime.now(NY):%A %b %d}</h2>
<p>Final list for today (${total} total). ✅ go · ⏸️ skip this week · 🔄 changed</p>
<table style="border-collapse:collapse;width:100%" border="1">{rows}</table>
<p style="font-size:11px;color:#888">Educational tool, not financial advice. Place trades yourself in your brokerage app.</p></div>"""
    send(f"✅ Buy-day check: ${total} today", body, cfg["alerts"].get("email_to") or None, dry_run=dry_run or demo)
    return 0
