"""Two AI agents.

AnalystAgent  — reviews every alert the program produces, re-reads the news
                itself, and judges whether the conclusion actually holds up.
ConsultantAgent — screens 'essentials' businesses (people still need soap)
                and writes a long-term watchlist memo.

Both use a free LLM when a key is configured, and fall back to transparent
rule-based reasoning otherwise, so the program never breaks.
"""
from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd

from .llm import LLM

# ───────────────────────────── Analyst ─────────────────────────────

ANALYST_SYSTEM = """You are a skeptical senior equity analyst reviewing the output of an automated
technical-alert program. Your job is to check whether its conclusion is justified — not to cheerlead.

You receive: the signal (Bollinger Band / EMA condition), indicator values, a historical base-rate
probability with its sample size, an automated headline-sentiment score, and recent headlines.

Do this:
1. Read the headlines yourself and rate overall news sentiment for THIS stock from -1 (clearly bad for
   the share price) to +1 (clearly good). Ignore headlines that are not really about the company.
   Distinguish company-specific news (earnings, guidance, lawsuits, recalls, M&A, downgrades) from
   general market noise; company-specific news matters more.
2. Check the program's logic: Is the sample size large enough to trust? Does the news explain the move
   (e.g. a lower-band touch caused by an earnings miss often keeps falling — a 'falling knife')?
   Do signals conflict? Is there an upcoming catalyst?
3. Give a verdict on the program's conclusion: AGREE, DISAGREE, or UNCERTAIN.

Respond with JSON only:
{"news_sentiment": float, "verdict": "AGREE|DISAGREE|UNCERTAIN", "confidence": float 0-1,
 "bias": "BULLISH|BEARISH|NEUTRAL", "reasoning": "3-5 sentences, plain English",
 "concerns": ["short bullet", ...], "key_headlines": ["most relevant headline", ...]}
"""


class AnalystAgent:
    def __init__(self, llm: LLM):
        self.llm = llm

    def review(self, ctx: dict) -> dict:
        prompt = "Review this alert:\n" + json.dumps(ctx, indent=2, default=str)
        res = self.llm.ask_json(ANALYST_SYSTEM, prompt)
        if res and "verdict" in res:
            res["source"] = "AI analyst"
            try:
                res["news_sentiment"] = max(-1.0, min(1.0, float(res.get("news_sentiment", ctx["news_sentiment_auto"]))))
            except (TypeError, ValueError):
                res["news_sentiment"] = ctx["news_sentiment_auto"]
            return res
        return self._rules(ctx)

    @staticmethod
    def _rules(ctx: dict) -> dict:
        concerns = []
        n = ctx["probability"]["n_events"]
        p_up = ctx["probability"]["p_up"]
        sent = ctx["news_sentiment_auto"]
        kind = ctx["signal"]["kind"]
        if n < 15:
            concerns.append(f"Only {n} similar past events — the historical odds are noisy.")
        if not ctx["headlines"]:
            concerns.append("No recent news found; the news adjustment is neutral.")
        if kind == "AT_LOWER_BAND" and sent < -0.2:
            concerns.append("Lower-band touch with negative news — classic falling-knife risk.")
        if kind == "AT_UPPER_BAND" and sent > 0.2:
            concerns.append("Upper-band touch with positive news — momentum may carry it higher before any pullback.")
        edge = abs(p_up - 0.5)
        verdict = "UNCERTAIN" if n < 15 or edge < 0.08 else "AGREE"
        bias = "BULLISH" if p_up > 0.55 else "BEARISH" if p_up < 0.45 else "NEUTRAL"
        return {
            "source": "rule-based analyst (add a free GEMINI_API_KEY for AI review)",
            "news_sentiment": sent, "verdict": verdict,
            "confidence": round(min(0.9, 0.4 + edge + min(n, 60) / 300), 2),
            "bias": bias,
            "reasoning": (f"Historically this setup led to a higher price {ctx['probability']['p_up_hist']:.0%} of the time "
                          f"over {ctx['probability']['horizon_days']} days across {n} events. Headline sentiment is "
                          f"{sent:+.2f}, giving an adjusted {p_up:.0%} chance of rising."),
            "concerns": concerns, "key_headlines": [h["title"] for h in ctx["headlines"][:3]],
        }


# ──────────────────────────── Consultant ────────────────────────────

CONSULTANT_SYSTEM = """You are a long-term investment consultant with a simple philosophy:
"People still need soap." You favor durable businesses selling things people buy in good times and bad —
household and personal care, food, discount retail, healthcare, utilities, waste — with pricing power,
steady cash flow, sustainable dividends, and reasonable valuations. You are honest about risks and you
never promise returns.

You receive a screened table of candidates with fundamentals, price trends, and a quantitative score,
plus the current technical position of each (Bollinger %B, vs EMA 10).

Respond with JSON only:
{"market_view": "2-3 sentences on how defensive/essentials stocks look right now",
 "picks": [{"ticker": str, "thesis": "2-3 sentences: why people keep buying from them and why it can compound",
            "entry_note": "is now a good entry based on the technicals, or wait for what?",
            "risks": "1-2 sentences"}],
 "avoid_or_watch": [{"ticker": str, "reason": str}],
 "disclaimer": "one sentence"}
"""


def _cagr(close: pd.Series, years: float) -> float | None:
    days = int(years * 252)
    if len(close) <= days:
        return None
    start, end = close.iloc[-days - 1], close.iloc[-1]
    return float((end / start) ** (1 / years) - 1)


def _max_drawdown(close: pd.Series) -> float:
    return float((close / close.cummax() - 1).min())


def price_stats(close: pd.Series) -> dict:
    rets = close.pct_change().dropna()
    return {
        "ret_1y": _cagr(close, 1), "cagr_5y": _cagr(close, 4.9),
        "volatility": float(rets.std() * math.sqrt(252)) if len(rets) else None,
        "max_drawdown": _max_drawdown(close),
    }


FUND_FIELDS = {
    "dividendYield": "div_yield", "payoutRatio": "payout", "revenueGrowth": "rev_growth",
    "profitMargins": "margin", "returnOnEquity": "roe", "trailingPE": "pe",
    "forwardPE": "fwd_pe", "beta": "beta", "debtToEquity": "debt_eq", "marketCap": "mkt_cap",
}


def fundamentals(info: dict) -> dict:
    f = {v: info.get(k) for k, v in FUND_FIELDS.items()}
    # yfinance has reported dividendYield both as 0.025 and as 2.5 — normalise to a fraction.
    if f["div_yield"] is not None and f["div_yield"] > 0.25:
        f["div_yield"] /= 100
    f["name"] = info.get("shortName") or info.get("longName")
    f["sector"] = info.get("sector")
    return f


def score_universe(rows: list[dict]) -> pd.DataFrame:
    """Rank candidates on durability: growth, quality, stability, income, value."""
    df = pd.DataFrame(rows).set_index("ticker")

    def z(col, higher_better=True):
        s = pd.to_numeric(df.get(col), errors="coerce")
        if s is None or s.notna().sum() < 3:
            return pd.Series(0.0, index=df.index)
        zz = (s - s.mean()) / (s.std() or 1)
        zz = zz.clip(-2.5, 2.5).fillna(0)
        return zz if higher_better else -zz

    payout = pd.to_numeric(df.get("payout"), errors="coerce")
    payout_pen = ((payout - 0.75).clip(lower=0) * 4).fillna(0)  # penalise payout > 75%
    df["score"] = (
        1.0 * z("cagr_5y") + 0.8 * z("rev_growth") + 0.8 * z("margin") + 0.6 * z("roe")
        + 0.6 * z("div_yield") + 0.8 * z("volatility", False) + 0.8 * z("max_drawdown")
        + 0.5 * z("fwd_pe", False) + 0.4 * z("debt_eq", False) - payout_pen
    ).round(2)
    return df.sort_values("score", ascending=False)


class ConsultantAgent:
    def __init__(self, llm: LLM):
        self.llm = llm

    def advise(self, table: pd.DataFrame, top_n: int) -> dict:
        cols = ["name", "sector", "score", "cagr_5y", "ret_1y", "rev_growth", "margin", "roe", "div_yield",
                "payout", "fwd_pe", "beta", "volatility", "max_drawdown", "pct_b", "below_ema"]
        view = table[[c for c in cols if c in table.columns]].head(max(top_n * 2, 10))
        records = json.loads(view.replace({np.nan: None}).round(4).reset_index().to_json(orient="records"))
        prompt = (f"Pick the best {top_n} long-term holdings from this screen and explain. "
                  f"Screen (sorted by score):\n{json.dumps(records, indent=1)}")
        res = self.llm.ask_json(CONSULTANT_SYSTEM, prompt)
        if res and "picks" in res:
            res["source"] = "AI consultant"
            return res
        return self._rules(table, top_n)

    @staticmethod
    def _rules(table: pd.DataFrame, top_n: int) -> dict:
        picks = []
        for t, r in table.head(top_n).iterrows():
            entry = ("Near the lower Bollinger Band — historically a better entry zone." if (r.get("pct_b") or 0.5) < 0.2
                     else "Stretched near the upper band — consider waiting for a pullback toward the middle band."
                     if (r.get("pct_b") or 0.5) > 0.8 else "Mid-range technically; fine to accumulate gradually.")
            bits = []
            if r.get("cagr_5y") is not None and not pd.isna(r.get("cagr_5y")):
                bits.append(f"~{r['cagr_5y']:.0%}/yr price growth over 5 years")
            if r.get("div_yield") and not pd.isna(r.get("div_yield")):
                bits.append(f"{r['div_yield']:.1%} dividend yield")
            if r.get("margin") and not pd.isna(r.get("margin")):
                bits.append(f"{r['margin']:.0%} profit margin")
            picks.append({"ticker": t, "thesis": f"{r.get('name') or t}: " + (", ".join(bits) or "screens well") + ".",
                          "entry_note": entry, "risks": "Check valuation and recent news before buying."})
        return {"source": "rule-based consultant (add a free GEMINI_API_KEY for AI memos)",
                "market_view": "Ranking is purely quantitative this week.", "picks": picks,
                "avoid_or_watch": [{"ticker": t, "reason": f"lowest score ({r['score']})"}
                                   for t, r in table.tail(3).iterrows()],
                "disclaimer": "Educational screening only — not financial advice."}
