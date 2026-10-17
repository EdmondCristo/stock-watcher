"""Runs the Sunday retirement report and the Friday pre-market check, and builds their emails."""
from __future__ import annotations

import html
from datetime import datetime
from zoneinfo import ZoneInfo

from . import data, news
from . import retire as R
from . import history as H
from .advisors import Judge, Researcher, Strategist
from .team import Manager, debate, memory_for, save_memory, update_memory
from .reviewer import Reviewer, feedback_for_agents, followed_totals, grade_last_week, project
from .emailer import send
from .indicators import add_indicators
from .llm import LLM

NY = ZoneInfo("America/New_York")
e = html.escape
BADGE = {"OK": ("✅", "#15803d"), "RISKY": ("⚠️", "#a16207"), "WRONG": ("❌", "#b91c1c"),
         "GO": ("✅", "#15803d"), "WAIT": ("⏸️", "#a16207"), "CHANGED": ("🔄", "#1d4ed8")}


def _enrich(items: list[dict], p: dict, table, prices: dict) -> list[dict]:
    cf = p["core_funds"]
    labels = {cf["us_stocks"].upper(): "Core: US stock market", cf["intl_stocks"].upper(): "Core: international stocks",
              cf["bonds"].upper(): "Core: bonds"}
    out = []
    for i in items:
        t = str(i["ticker"]).upper()
        d = {"ticker": t, "amount": int(round(float(i["amount"]))), "reason": i.get("reason", "")}
        if t in labels:
            d.update(role="core", label=labels[t], price_at_plan=prices.get(t))
        else:
            row = table.loc[t]
            d.update(role="satellite", label=f"Satellite: {row.get('name') or t}", **R.buy_range(row))
        out.append(d)
    return out


def _prices(tickers, table, demo: bool) -> dict:
    """Latest close for every ticker we need (researched stocks, core funds, anything bought before)."""
    out = {t: round(float(table.loc[t, "price"]), 2) for t in table.index}
    for t in {str(x).upper() for x in tickers} - set(out):
        try:
            raw = data.demo_history(t, crash_end=False) if demo else data.get_history(t, 1)
            out[t] = round(float(raw["Close"].iloc[-1]), 2)
        except Exception as ex:
            print(f"[prices] {t}: {ex}")
    return out


# ───────────────────────── Sunday ─────────────────────────

def run_sunday(cfg: dict, demo: bool = False, dry_run: bool = False, save: bool | None = None) -> int:
    save = (not demo) if save is None else save
    p = R.load_portfolio()
    llm = LLM(cfg.get("llm", {}))
    today = datetime.now(NY).date()
    buy = R.next_buy_date(today, p.get("buy_day", "Friday"))
    table = R.research_satellites(cfg["consultant"]["universe"], p, today, buy, demo=demo)

    # ── 0. Reviewer: grade last week, project 24 years, send feedback ──
    records = H.load_records()
    last = records[-1] if records else None
    ever_bought = {i["ticker"] for r in records for i in r.get("final", [])}
    ever_bought |= {str(s.get("ticker", "")).upper() for r in records for s in r.get("strategist_plan", [])}
    prices = _prices(set(p["core_funds"].values()) | ever_bought, table, demo)
    grade = grade_last_week(last, prices) if last else None
    totals = followed_totals(records, prices)
    projection = project(p, totals["worth_now"], p["years_to_retirement"])
    review = Reviewer(llm).review(last, grade, totals, projection, H.load_lessons())
    review["date"] = str(today)
    if save:
        H.save_lesson(review)
    lessons = H.load_lessons() if save else H.load_lessons() + [review]

    # Reviewer as coach: update the team's long-term memory
    memory, memory_update = update_memory(llm, review, lessons, str(today))
    if save:
        save_memory(memory)
    feedback = feedback_for_agents(lessons)
    for who in ("researcher", "strategist", "judge"):
        feedback[f"for_{who}"] = memory_for(memory, who) + feedback.get(f"for_{who}", [])

    # ── Manager AI hands out this week's tasks ──
    manager = Manager(llm)
    tasks = manager.assign(p, review, memory, list(table.index[:8]))
    for t in tasks.get("extra_news", []):
        if t in table.index and not table.at[t, "news"]:
            arts = R._demo_news(t) if demo else news.fetch(t, None, 6, 7)
            table.at[t, "news"] = [a.title for a in arts[:4]]
            table.at[t, "sentiment"] = news.aggregate_sentiment(arts)

    # ── Researcher → Strategist ⇄ Judge ──
    plan = R.base_plan(p, table, buy)
    facts = Researcher(llm).fact_sheet(p, plan, table, feedback, tasks.get("researcher", ""))
    if "red_flag" in table.columns and table["red_flag"].fillna(False).any():
        plan = R.base_plan(p, table, buy)          # re-pick if the Researcher flagged the first choice
        facts["rules_only_plan"] = plan["items"]
        facts["skipped_this_week"] = plan["skipped"]

    strategist, judge = Strategist(llm), Judge(llm)
    strategy = strategist.propose(facts, p, today.isocalendar()[1], tasks.get("strategist", ""))
    strategy, verdict, transcript = debate(llm, strategist, judge, facts, strategy,
                                           lambda pl: R.check_plan(pl, p, table), tasks.get("judge", ""), manager)

    final = verdict.get("approved_plan") or plan["items"]
    if R.check_plan(final, p, table):              # hard rules always win
        final = plan["items"]
        verdict["overall"] = (verdict.get("overall", "") + " The approved plan still broke a safety rule, "
                              "so the rules-only plan is used.").strip()
    reasons = {str(i["ticker"]).upper(): i.get("reason", "") for i in strategy["plan"]}
    for i in final:
        i.setdefault("reason", reasons.get(str(i["ticker"]).upper()) or next(
            (b["reason"] for b in plan["items"] if b["ticker"] == i["ticker"]), ""))
    for t in {str(s.get("ticker", "")).upper() for s in strategy["plan"]} - set(prices):
        prices.update(_prices([t], table, demo))
    final = _enrich(final, p, table, prices)

    wrap = manager.wrap_up({"buy_day": f"{buy:%A}", "budget": p["weekly_budget"], "final_plan": final,
                            "debate_rounds": len(transcript), "debate_outcome": transcript[-1].get("outcome"),
                            "judge_overall": verdict.get("overall"), "reviewer_summary": review.get("summary"),
                            "n_candidates": len(table)})
    team = {"tasks": tasks, "transcript": transcript, "memory": memory, "memory_update": memory_update, "wrap": wrap}
    body = render_sunday(p, plan, facts, strategy, verdict, final, table, buy, review, grade, totals, projection, team)
    if save:
        R.save_plan({"created": datetime.now(NY).isoformat(), "buy_date": str(buy), "budget": p["weekly_budget"],
                     "core_us": p["core_funds"]["us_stocks"], "items": final})
        verdict_by_t = {str(v.get("ticker")).upper(): v.get("verdict") for v in verdict.get("verdicts", [])}
        H.save_record({"date": str(today), "buy_date": str(buy), "budget": p["weekly_budget"],
                       "core_us": p["core_funds"]["us_stocks"], "final": final,
                       "strategist_plan": strategy["plan"], "strategist_summary": strategy.get("summary"),
                       "big_idea": strategy.get("big_idea"), "judge_verdicts": verdict.get("verdicts"),
                       "judge_overall": verdict.get("overall"),
                       "judge_response_to_reviewer": verdict.get("response_to_reviewer"),
                       "review": review, "totals_before": totals, "manager_tasks": tasks,
                       "debate": transcript, "memory_update": memory_update, "manager_summary": wrap, "projection": projection,
                       "prices_at_plan": prices})
        H.append_scorecard([{"week": str(today), "buy_date": str(buy), "ticker": i["ticker"], "role": i["role"],
                             "amount": i["amount"], "price_at_plan": i.get("price_at_plan"),
                             "judge_verdict": verdict_by_t.get(i["ticker"], "OK")} for i in final])
        H.save_report(str(today), body)

    send(f"🧼 Retirement plan for {buy:%a %b %d}: ${p['weekly_budget']}", body,
         cfg["alerts"].get("email_to") or None, dry_run=dry_run or demo)
    return 0


def _money(v):
    return f"${v:,.0f}"


def render_review(review, grade, totals, projection, verdict) -> str:
    pr = projection
    proj = (f"<table style='border-collapse:collapse;font-size:13px' border='1' cellpadding='5'>"
            f"<tr><th></th><th>Low</th><th>Middle</th><th>High</th></tr>"
            f"<tr><td>Worth at retirement</td><td>{_money(pr['low']['value'])}</td><td><b>{_money(pr['middle']['value'])}</b></td>"
            f"<td>{_money(pr['high']['value'])}</td></tr>"
            f"<tr><td>In today's dollars</td><td>{_money(pr['low']['todays_dollars'])}</td>"
            f"<td><b>{_money(pr['middle']['todays_dollars'])}</b></td><td>{_money(pr['high']['todays_dollars'])}</td></tr>"
            f"<tr><td>Avg yearly growth assumed</td><td>{pr['low']['avg_return']:.1%}</td><td>{pr['middle']['avg_return']:.1%}</td>"
            f"<td>{pr['high']['avg_return']:.1%}</td></tr></table>"
            f"<p style='font-size:12px;color:#555;margin:4px 0'>If you invest your weekly amount for {pr['years']} more years "
            f"(about {_money(pr['you_put_in_future'])} more of your own money) and follow the Judge's plan. "
            f"\"Today's dollars\" removes {pr['assumptions']['inflation']:.1%}/yr inflation so you can compare with prices now. "
            f"Estimates, not promises.</p>")
    rows = ""
    for i in (grade or {}).get("items", []):
        r, g = i["return"], i["gain"]
        r_txt = "—" if r is None else f"{r:+.1%}"
        g_txt = "—" if g is None else f"${g:+.2f}"
        colour = "#15803d" if (r or 0) >= 0 else "#b91c1c"
        rows += (f"<tr><td>{e(i['ticker'])}</td><td>${i['amount']}</td>"
                 f"<td style='color:{colour}'>{r_txt}</td><td>{g_txt}</td></tr>")
    calls = "".join(
        f"<li>Judge rejected <b>{e(c['rejected'])}</b> ({c['rejected_return']:+.1%}) and chose {e(c['instead'])} "
        f"({c['instead_return']:+.1%}): {e(c['outcome'])}.</li>" for c in (grade or {}).get("judge_calls", []))
    errs = "".join(f"<li>{e(str(x))}</li>" for x in review.get("judge_errors", []))
    so_far = (f"<p style='margin:4px 0'>Following the Judge for <b>{totals['weeks']} week(s)</b>: you'd have put in "
              f"<b>{_money(totals['put_in'])}</b>, now worth about <b>{_money(totals['worth_now'])}</b> "
              f"({totals['gain_pct']:+.1%}).</p>") if totals["weeks"] else ""
    notes = "".join(f"<li><b>To the {who}:</b> {e(str(review.get('for_' + who.lower())))}</li>"
                    for who in ("Researcher", "Strategist", "Judge") if review.get("for_" + who.lower()))
    reply = verdict.get("response_to_reviewer")
    return f"""<h3>0. Last week in review (Reviewer)</h3>
<p style="margin:4px 0">{e(review.get('summary', ''))}</p>{so_far}
{f"<table style='border-collapse:collapse;font-size:13px' border='1' cellpadding='4'><tr><th>Bought</th><th>Amount</th><th>Since plan</th><th>Gain</th></tr>{rows}</table>" if rows else ""}
{f"<ul style='font-size:13px'>{calls}</ul>" if calls else ""}
{f"<p style='margin:6px 0 2px;font-size:13px'><b>Errors the Reviewer found in the Judge:</b></p><ul style='font-size:13px'>{errs}</ul>" if errs else ""}
{f"<p style='margin:6px 0 2px;font-size:13px'><b>Feedback sent to the team:</b></p><ul style='font-size:13px'>{notes}</ul>" if notes else ""}
{f"<p style='margin:4px 0;font-size:13px;padding:6px 10px;background:#f5f5f4;border-radius:6px'><b>Judge replies:</b> {e(str(reply))}</p>" if reply else ""}
{f"<p style='font-size:12px;color:#555'>{e(str(review.get('confidence_note')))}</p>" if review.get('confidence_note') else ""}
<h4 style="margin:12px 0 4px">Projected retirement in {pr['years']} years</h4>{proj}
<p style="font-size:11px;color:#888">Reviewer: {e(str(review.get('source', '')))}</p>"""


def _pct(v):
    return "—" if v is None or v != v else f"{v:.0%}"


def render_team_top(team) -> str:
    t = team["tasks"]
    return f"""<div style="padding:10px 14px;background:#eef2ff;border-radius:8px;margin:8px 0">
<b>Manager's summary:</b> {e(team['wrap'])}
<p style="font-size:12px;margin:8px 0 2px"><b>This week's assignments</b> ({e(t.get('source', ''))}): {e(t.get('why', ''))}</p>
<ul style="font-size:12px;margin:2px 0">
<li><b>Reviewer:</b> grade last week and update team memory</li>
<li><b>Researcher:</b> {e(t.get('researcher', ''))}{(' Extra news check: ' + e(', '.join(t['extra_news']))) if t.get('extra_news') else ''}</li>
<li><b>Strategist:</b> {e(t.get('strategist', ''))}</li>
<li><b>Judge:</b> {e(t.get('judge', ''))}</li></ul></div>"""


def render_debate(team) -> str:
    rows = ""
    for d in team["transcript"]:
        probs = [f"{p.get('ticker')}: {p.get('reason')}" for p in d["judge_problems"] + d["rule_problems"]]
        ext = d.get("manager_extension")
        rows += (f"<li><b>Round {d['round']}</b> — Strategist: {e(str(d.get('strategist_note') or ''))}"
                 f"{(' <i>(pushed back: ' + e(d['disagree']) + ')</i>') if d.get('disagree') else ''}<br>"
                 f"Judge: {e(str(d.get('judge_says') or ''))} "
                 f"{('<span style=color:#b91c1c>' + e('; '.join(probs)) + '</span>') if probs else '✅ no problems'}"
                 f"{('<br>Manager on extra rounds: ' + ('approved' if ext['extend'] else 'declined') + ' — ' + e(ext['reason'])) if ext else ''}"
                 f"{('<br><b>Outcome: ' + e(d['outcome']) + '</b>') if d.get('outcome') else ''}</li>")
    mem = "".join(f"<li>[{e(m['for'])}] {e(m['lesson'])} <span style='color:#888'>(confirmed {m.get('times_confirmed', 1)}×)</span></li>"
                  for m in team["memory"])
    return (f"<h3>2b. Strategist ⇄ Judge debate ({len(team['transcript'])} round"
            f"{'s' if len(team['transcript']) != 1 else ''}, limit 4, max 10)</h3><ol style='font-size:13px;list-style:none;padding-left:0'>{rows}</ol>"
            + (f"<p style='font-size:13px;margin:6px 0 2px'><b>Team memory</b> (long-term lessons the Reviewer keeps):</p>"
               f"<ul style='font-size:12px'>{mem}</ul>" if mem else ""))


def render_sunday(p, plan, facts, strategy, verdict, final, table, buy, review=None, grade=None, totals=None,
                  projection=None, team=None) -> str:
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
{render_team_top(team) if team else ""}
{render_review(review, grade, totals, projection, verdict) if review else ""}
<h3>1. What to buy on {buy:%A}</h3>
<table style="border-collapse:collapse;width:100%" border="1" cellpadding="0">{buy_rows}</table>
<h3>2. The Judge's review</h3>
<p style="margin:4px 0">{e(str(verdict.get('overall', '')))}</p><ul style="font-size:13px">{verdict_rows}</ul>
{render_debate(team) if team else ""}
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
