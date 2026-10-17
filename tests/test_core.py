import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import probability, signals
from src.agents import AnalystAgent, score_universe
from src.data import demo_history
from src.indicators import add_indicators, bollinger, ema


def test_ema_matches_pandas_definition():
    s = pd.Series(np.arange(1, 31, dtype=float))
    e = ema(s, 10)
    alpha = 2 / 11
    manual = [s.iloc[0]]
    for x in s.iloc[1:]:
        manual.append(alpha * x + (1 - alpha) * manual[-1])
    assert np.allclose(e.values, manual)


def test_bollinger_flat_series_has_zero_width():
    bb = bollinger(pd.Series([10.0] * 30), 20, 2)
    assert bb["bb_upper"].iloc[-1] == bb["bb_lower"].iloc[-1] == 10.0


def test_lower_band_detected_after_crash():
    df = add_indicators(demo_history("TEST", 3, seed=1), 10, 20, 2.0)
    kinds = [s.kind for s in signals.detect("TEST", df, 0.05)]
    assert signals.LOWER in kinds


def test_probability_bounds_and_news_direction():
    df = add_indicators(demo_history("TEST", 5, seed=2), 10, 20, 2.0)
    mask = signals.condition_masks(df, 0.05)[signals.LOWER]
    neutral = probability.estimate(df["Close"], mask, 5, 0.0, 1.0)
    bad = probability.estimate(df["Close"], mask, 5, -0.8, 1.0)
    good = probability.estimate(df["Close"], mask, 5, 0.8, 1.0)
    assert 0 < neutral.p_up < 1 and neutral.n_events > 10
    assert bad.p_up < neutral.p_up < good.p_up


def test_streaks_counted_once():
    m = pd.Series([False, True, True, True, False, True])
    assert probability.first_of_streak(m).sum() == 2


def test_rule_based_analyst_flags_falling_knife():
    ctx = {"signal": {"kind": signals.LOWER}, "news_sentiment_auto": -0.6,
           "probability": {"n_events": 40, "p_up": 0.3, "p_up_hist": 0.55, "horizon_days": 5},
           "headlines": [{"title": "X misses earnings"}]}
    r = AnalystAgent._rules(ctx)
    assert any("falling-knife" in c for c in r["concerns"]) and r["bias"] == "BEARISH"


def test_consultant_scoring_orders_rows():
    rows = [{"ticker": t, "cagr_5y": g, "margin": 0.15, "volatility": v, "max_drawdown": -0.2,
             "div_yield": 0.02, "payout": 0.5, "rev_growth": 0.03, "roe": 0.2, "fwd_pe": 20, "debt_eq": 50}
            for t, g, v in [("A", 0.12, 0.15), ("B", 0.02, 0.35), ("C", 0.07, 0.2)]]
    assert list(score_universe(rows).index)[0] == "A"
