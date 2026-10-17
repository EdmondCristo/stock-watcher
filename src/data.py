"""Price + fundamentals from Yahoo Finance (free, no API key), plus a synthetic demo feed."""
from __future__ import annotations

import numpy as np
import pandas as pd


def get_history(ticker: str, years: int = 5) -> pd.DataFrame:
    import yfinance as yf
    df = yf.Ticker(ticker).history(period=f"{years}y", interval="1d", auto_adjust=True)
    if df is None or df.empty:
        raise RuntimeError(f"No price data for {ticker}")
    df.index = pd.to_datetime(df.index).tz_localize(None)
    return df[["Open", "High", "Low", "Close", "Volume"]]


def get_info(ticker: str) -> dict:
    import yfinance as yf
    try:
        return yf.Ticker(ticker).info or {}
    except Exception:
        return {}


def demo_history(ticker: str, years: int = 5, seed: int | None = None, crash_end: bool = True) -> pd.DataFrame:
    """Synthetic random-walk prices for offline testing (--demo)."""
    import zlib
    rng = np.random.default_rng(seed if seed is not None else zlib.crc32(ticker.encode()))
    n = years * 252
    rets = rng.normal(0.0004, 0.013, n)
    if crash_end:
        rets[-4:] = [-0.02, -0.025, -0.03, -0.035]   # force a lower-band touch at the end
    close = 100 * np.exp(np.cumsum(rets))
    idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=n)
    return pd.DataFrame({"Open": close, "High": close * 1.005, "Low": close * 0.995,
                         "Close": close, "Volume": rng.integers(1e6, 5e6, n)}, index=idx)
