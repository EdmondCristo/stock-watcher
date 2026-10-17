"""Stock Watcher — Bollinger Band / EMA alerts with news-aware probabilities and AI review.

    python main.py watch               # check watchlist, email alerts (market hours only)
    python main.py watch --force       # run even if market is closed
    python main.py watch --dry-run     # print emails instead of sending
    python main.py watch --demo        # offline test with synthetic prices & headlines
    python main.py consult             # weekly 'people still need soap' consultant memo

Educational tool — not financial advice.
"""
from __future__ import annotations

import argparse
import html
import json
import sys
import traceback
from datetime import datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from src import data, news, probability, signals, state as st
from src.agents import AnalystAgent, ConsultantAgent, fundamentals, price_stats, score_universe
from src.emailer import send
from src.indicators import add_indicators
from src.llm import LLM

ROOT = Path(__file__).resolve().parent
NY = ZoneInfo("America/New_York")


def load_cfg() -> dict:
    return yaml.safe_load((ROOT / "config.yaml").read_text())


def market_open(now: datetime | None = None) -> bool:
    now = (now or datetime.now(timezone.utc)).astimezone(NY)
    return now.weekday() < 5 and time(9, 30) <= now.time() <= time(16, 5)


# ───────────────────────────── watch ─────────────────────────────

def interpret(kind: str, p_up: float, thr: float) -> tuple[str, str]:
    """Return (headline, colour) for the alert."""
    p_down = 1 - p_up
    if kind == signals.LOWER:
        if p_down >= thr:
            return f"⚠️ Falling-knife warning — {p_down:.0%} chance it keeps FALLING", "#b91c1c"
        if p_up >= thr:
            return f"🟢 Possible BUY zone — {p_up:.0%} chance it bounces up", "#15803d"
        return f"🟡 At lower band, odds unclear ({p_up:.0%} up / {p_down:.0%} down)", "#a16207"
    if kind == signals.UPPER:
        if p_down >= thr:
            return f"🔴 Possible SELL / take-profit — {p_down:.0%} chance it pulls back", "#b91c1c"
        if p_up >= thr:
            return f"🚀 At upper band but momentum strong — {p_up:.0%} chance it keeps RISING", "#15803d"
        return f"🟡 At upper band, odds unclear ({p_up:.0%} up / {p_down:.0%} down)", "#a16207"
    if p_down >= thr:
        return f"🔻 Broke below EMA — {p_down:.0%} chance of further decline", "#b91c1c"
    return f"↘️ Crossed below EMA ({p_up:.0%} up / {p_down:.0%} down)", "#a16207"


def demo_headlines(ticker: str) -> list[news.Article]:
    now = datetime.now(timezone.utc)
    titles = [f"{ticker} shares slide after company cuts full-year guidance",
              f"Analyst downgrades {ticker} citing weak demand",
              f"{ticker} announces new product line"]
    arts = [news.Article(t, "Demo", "", now) for t in titles]
    for a in arts:
        a.sentiment = news._score(a.title)
    return arts


def analyse_signal(sig, df, mask, cfg, analyst, demo, company) -> dict:
    pc, nc = cfg["probability"], cfg["news"]
    arts = demo_headlines(sig.ticker) if demo else news.fetch(sig.ticker, company, nc["max_articles"], nc["max_age_days"])
    auto_sent = news.aggregate_sentiment(arts)
    est = probability.estimate(df["Close"], mask, pc["horizon_days"], auto_sent, pc["news_weight"])
    ctx = {
        "ticker": sig.ticker, "company": company,
        "signal": {"kind": sig.kind, "description": sig.description, "date": sig.date},
        "indicators": {"price": round(sig.price, 2), "ema10": round(sig.ema, 2), "bb_upper": round(sig.bb_upper, 2),
                       "bb_mid": round(sig.bb_mid, 2), "bb_lower": round(sig.bb_lower, 2), "pct_b": round(sig.pct_b, 3)},
        "probability": {"p_up_hist": round(est.p_up_hist, 3), "n_events": est.n_events,
                        "avg_fwd_return": round(est.avg_fwd_return, 4), "p_up": round(est.p_up, 3),
                        "horizon_days": est.horizon_days},
        "news_sentiment_auto": round(auto_sent, 3),
        "headlines": [{"title": a.title, "source": a.source, "sentiment": round(a.sentiment, 2),
                       "published": a.published.isoformat() if a.published else None} for a in arts],
    }
    review = analyst.review(ctx)
    # Use the analyst's own reading of the news for the final probability.
    final_p_up = probability.news_adjust(est.p_up_hist, review["news_sentiment"], pc["news_weight"])
    headline, colour = interpret(sig.kind, final_p_up, pc["alert_threshold"])
    return {"sig": sig, "ctx": ctx, "review": review, "p_up": final_p_up, "headline": headline,
            "colour": colour, "articles": arts}


def render_alert(r: dict) -> str:
    s, rv, c = r["sig"], r["review"], r["ctx"]
    e = html.escape
    heads = "".join(
        f"<li>{'🟢' if a.sentiment > 0.2 else '🔴' if a.sentiment < -0.2 else '⚪'} "
        f"<a href='{e(a.url)}'>{e(a.title)}</a> <span style='color:#666'>— {e(a.source)}</span></li>"
        for a in r["articles"][:6]) or "<li>No recent news found.</li>"
    concerns = "".join(f"<li>{e(str(x))}</li>" for x in rv.get("concerns", [])) or "<li>None flagged.</li>"
    p = c["probability"]
    return f"""
<div style="border:1px solid #ddd;border-left:6px solid {r['colour']};border-radius:8px;padding:14px 18px;margin:16px 0;font-family:Arial,sans-serif">
  <h2 style="margin:0 0 4px">{e(s.ticker)} — ${s.price:,.2f}</h2>
  <div style="font-size:16px;font-weight:bold;color:{r['colour']}">{e(r['headline'])}</div>
  <p style="margin:8px 0;color:#444">{e(s.description)} ({e(s.date)})</p>
  <table style="border-collapse:collapse;font-size:13px">
    <tr><td style="padding:2px 10px 2px 0">Upper band</td><td>${s.bb_upper:,.2f}</td>
        <td style="padding:2px 10px 2px 24px">EMA 10</td><td>${s.ema:,.2f}</td></tr>
    <tr><td style="padding:2px 10px 2px 0">Middle band</td><td>${s.bb_mid:,.2f}</td>
        <td style="padding:2px 10px 2px 24px">%B</td><td>{s.pct_b:.2f}</td></tr>
    <tr><td style="padding:2px 10px 2px 0">Lower band</td><td>${s.bb_lower:,.2f}</td><td></td><td></td></tr>
  </table>
  <h3 style="margin:14px 0 4px;font-size:14px">Probability ({p['horizon_days']} trading days)</h3>
  <p style="margin:0;font-size:13px">History alone: <b>{p['p_up_hist']:.0%}</b> rose after {p['n_events']} similar past setups
     (avg move {p['avg_fwd_return']:+.2%}).<br>
     After news adjustment (sentiment {rv['news_sentiment']:+.2f}): <b>{r['p_up']:.0%} up / {1 - r['p_up']:.0%} down</b>.</p>
  <h3 style="margin:14px 0 4px;font-size:14px">Analyst review — {e(str(rv.get('verdict')))} · confidence {float(rv.get('confidence', 0)):.0%} · {e(str(rv.get('bias', '')))}</h3>
  <p style="margin:0;font-size:13px">{e(str(rv.get('reasoning', '')))}</p>
  <ul style="font-size:13px;margin:6px 0">{concerns}</ul>
  <p style="font-size:11px;color:#888;margin:0">Reviewed by: {e(str(rv.get('source')))}</p>
  <h3 style="margin:14px 0 4px;font-size:14px">Latest news</h3>
  <ul style="font-size:13px;margin:0">{heads}</ul>
</div>"""


def cmd_watch(args, cfg) -> int:
    if not (args.force or args.demo or market_open()):
        print("Market closed — nothing to do (use --force to run anyway).")
        return 0
    ic, ac = cfg["indicators"], cfg["alerts"]
    llm = LLM(cfg.get("llm", {}))
    analyst = AnalystAgent(llm)
    state = st.load()
    results, errors = [], []

    for t in cfg["watchlist"]:
        try:
            raw = data.demo_history(t) if args.demo else data.get_history(t, cfg["probability"]["lookback_years"])
            df = add_indicators(raw, ic["ema_period"], ic["bb_period"], ic["bb_std"])
            masks = signals.condition_masks(df, ic["band_tolerance"])
            sigs = signals.detect(t, df, ic["band_tolerance"])
            print(f"{t}: close {df['Close'].iloc[-1]:.2f}, %B {df['pct_b'].iloc[-1]:.2f}, "
                  f"EMA {df['ema'].iloc[-1]:.2f} → {[s.kind for s in sigs] or 'no signal'}")
            company = None if args.demo else data.get_info(t).get("shortName")
            for s in sigs:
                if not (args.demo or args.force) and not st.should_alert(state, s.key(), ac["cooldown_hours"]):
                    print(f"  {s.kind}: already alerted recently, skipping")
                    continue
                results.append(analyse_signal(s, df, masks[s.kind], cfg, analyst, args.demo, company))
                st.mark(state, s.key())
        except Exception as ex:
            errors.append(f"{t}: {ex}")
            traceback.print_exc()

    if results:
        tickers = ", ".join(sorted({r["sig"].ticker for r in results}))
        body = (f"<div style='font-family:Arial,sans-serif;max-width:720px'>"
                f"<p style='color:#666'>Stock Watcher · {datetime.now(NY):%a %b %d, %I:%M %p} ET</p>"
                + "".join(render_alert(r) for r in results)
                + ("<p style='color:#b91c1c'>Errors: " + html.escape("; ".join(errors)) + "</p>" if errors else "")
                + "<p style='font-size:11px;color:#888'>Automated educational analysis — not financial advice. "
                  "Probabilities are estimates from past price behaviour and headline sentiment.</p></div>")
        send(f"📈 Stock alert: {tickers}", body, ac.get("email_to") or None, dry_run=args.dry_run or args.demo)
    else:
        print("No new signals.")
    if not args.demo:
        st.save(state)
    return 1 if errors and not results else 0


# ──────────────────────────── consult ────────────────────────────

def cmd_consult(args, cfg) -> int:
    cc, ic = cfg["consultant"], cfg["indicators"]
    rows = []
    for t in cc["universe"]:
        try:
            raw = data.demo_history(t, crash_end=False) if args.demo else data.get_history(t, 5)
            df = add_indicators(raw, ic["ema_period"], ic["bb_period"], ic["bb_std"])
            info = {} if args.demo else data.get_info(t)
            row = {"ticker": t, **fundamentals(info), **price_stats(df["Close"]),
                   "pct_b": float(df["pct_b"].iloc[-1]), "below_ema": bool(df["Close"].iloc[-1] < df["ema"].iloc[-1]),
                   "price": float(df["Close"].iloc[-1])}
            rows.append(row)
            print(f"{t}: loaded")
        except Exception as ex:
            print(f"{t}: skipped ({ex})")
    if not rows:
        print("No data loaded.")
        return 1
    table = score_universe(rows)
    memo = ConsultantAgent(LLM(cfg.get("llm", {}))).advise(table, cc["top_n"])

    e = html.escape
    picks = "".join(
        f"<div style='border:1px solid #ddd;border-radius:8px;padding:10px 14px;margin:10px 0'>"
        f"<b style='font-size:16px'>{e(p['ticker'])}</b>"
        + (f" — ${table.loc[p['ticker'], 'price']:,.2f}" if p['ticker'] in table.index else "")
        + f"<p style='margin:6px 0'>{e(p.get('thesis', ''))}</p>"
        f"<p style='margin:4px 0;font-size:13px'><b>Entry:</b> {e(p.get('entry_note', ''))}</p>"
        f"<p style='margin:4px 0;font-size:13px;color:#a16207'><b>Risks:</b> {e(p.get('risks', ''))}</p></div>"
        for p in memo.get("picks", []))
    watch = "".join(f"<li><b>{e(w['ticker'])}</b>: {e(w.get('reason', ''))}</li>" for w in memo.get("avoid_or_watch", []))

    def pct(v):
        return "—" if v is None or v != v else f"{v:.0%}"
    rows_html = "".join(
        f"<tr><td>{e(t)}</td><td>{r['score']:+.2f}</td><td>{pct(r.get('cagr_5y'))}</td><td>{pct(r.get('div_yield'))}</td>"
        f"<td>{pct(r.get('margin'))}</td><td>{pct(r.get('max_drawdown'))}</td><td>{r['pct_b']:.2f}</td></tr>"
        for t, r in table.iterrows())
    body = f"""<div style="font-family:Arial,sans-serif;max-width:720px">
<h2>🧼 Weekly consultant memo — "people still need soap"</h2>
<p>{e(memo.get('market_view', ''))}</p><h3>Top picks</h3>{picks}
<h3>Watch / avoid</h3><ul>{watch}</ul>
<h3>Full screen</h3>
<table style="border-collapse:collapse;font-size:12px" cellpadding="4" border="1">
<tr><th>Ticker</th><th>Score</th><th>5y CAGR</th><th>Div yld</th><th>Margin</th><th>Max DD</th><th>%B</th></tr>{rows_html}</table>
<p style="font-size:11px;color:#888">{e(memo.get('disclaimer', 'Not financial advice.'))} · Source: {e(memo.get('source', ''))}</p></div>"""
    send("🧼 Weekly essentials-stocks memo", body, cfg["alerts"].get("email_to") or None,
         dry_run=args.dry_run or args.demo)
    if not args.demo:
        (ROOT / "state").mkdir(exist_ok=True)
        (ROOT / "state" / "last_consult.json").write_text(json.dumps(
            {"ran_at": datetime.now(timezone.utc).isoformat(), "top": list(table.index[:cc['top_n']])}, indent=2))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["watch", "consult"])
    ap.add_argument("--force", action="store_true", help="run even if market closed / ignore cooldown")
    ap.add_argument("--dry-run", action="store_true", help="print emails instead of sending")
    ap.add_argument("--demo", action="store_true", help="offline synthetic data (implies --dry-run)")
    args = ap.parse_args(argv)
    cfg = load_cfg()
    return cmd_watch(args, cfg) if args.command == "watch" else cmd_consult(args, cfg)


if __name__ == "__main__":
    sys.exit(main())
