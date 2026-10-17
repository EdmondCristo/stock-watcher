"""Probability that price rises/falls after a signal.

Step 1 — Historical base rate: find every past day this stock was in the same
condition (first day of each streak only, so one long dip isn't counted 15
times), and check whether the close N trading days later was higher.
A Beta(1,1) prior keeps small samples from producing 0% or 100%.

Step 2 — News adjustment: shift the base rate in log-odds space by
news_weight * sentiment (sentiment in [-1, +1]). Negative news lowers the
chance of a bounce; positive news raises it.

This is a transparent, explainable estimate — not a guarantee.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd


@dataclass
class ProbabilityEstimate:
    p_up_hist: float          # historical P(higher after N days)
    n_events: int             # independent past occurrences
    avg_fwd_return: float     # mean N-day return after those events
    sentiment: float          # -1..+1
    p_up: float               # news-adjusted P(up)
    horizon_days: int

    @property
    def p_down(self) -> float:
        return 1 - self.p_up

    def summary(self) -> str:
        return (f"P(up in {self.horizon_days}d) = {self.p_up:.0%} "
                f"(history {self.p_up_hist:.0%} over {self.n_events} past events, "
                f"avg move {self.avg_fwd_return:+.2%}; news sentiment {self.sentiment:+.2f})")


def first_of_streak(mask: pd.Series) -> pd.Series:
    mask = mask.fillna(False).astype(bool)
    return mask & ~mask.shift(1, fill_value=False)


def historical_odds(close: pd.Series, mask: pd.Series, horizon: int) -> tuple[float, int, float]:
    fwd = close.shift(-horizon) / close - 1
    events = first_of_streak(mask)
    rets = fwd[events].dropna()
    n = int(len(rets))
    ups = int((rets > 0).sum())
    p = (ups + 1) / (n + 2)               # Beta(1,1) smoothing
    avg = float(rets.mean()) if n else 0.0
    return p, n, avg


def _logit(p: float) -> float:
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def _sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def news_adjust(p_up: float, sentiment: float, weight: float) -> float:
    return _sigmoid(_logit(p_up) + weight * max(-1.0, min(1.0, sentiment)))


def estimate(close: pd.Series, mask: pd.Series, horizon: int, sentiment: float, weight: float) -> ProbabilityEstimate:
    p_hist, n, avg = historical_odds(close, mask, horizon)
    return ProbabilityEstimate(
        p_up_hist=p_hist, n_events=n, avg_fwd_return=avg, sentiment=sentiment,
        p_up=news_adjust(p_hist, sentiment, weight), horizon_days=horizon,
    )
