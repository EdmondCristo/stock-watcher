"""Technical indicators: EMA and Bollinger Bands."""
from __future__ import annotations

import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential moving average (same formula TradingView/Yahoo use)."""
    return series.ewm(span=period, adjust=False).mean()


def bollinger(series: pd.Series, period: int = 20, num_std: float = 2.0) -> pd.DataFrame:
    """Middle = SMA(period); upper/lower = middle ± num_std * population std."""
    mid = series.rolling(period).mean()
    std = series.rolling(period).std(ddof=0)
    upper = mid + num_std * std
    lower = mid - num_std * std
    width = upper - lower
    pct_b = (series - lower) / width  # 0 = on lower band, 1 = on upper band
    return pd.DataFrame({"bb_mid": mid, "bb_upper": upper, "bb_lower": lower, "pct_b": pct_b})


def add_indicators(df: pd.DataFrame, ema_period: int, bb_period: int, bb_std: float) -> pd.DataFrame:
    """Return a copy of an OHLC frame with indicator columns added."""
    out = df.copy()
    out[f"ema{ema_period}"] = ema(out["Close"], ema_period)
    out["ema"] = out[f"ema{ema_period}"]
    out = out.join(bollinger(out["Close"], bb_period, bb_std))
    return out
