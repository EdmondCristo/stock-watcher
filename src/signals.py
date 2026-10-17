"""Signal detection: price at the upper/lower Bollinger band, or below EMA."""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

UPPER = "AT_UPPER_BAND"
LOWER = "AT_LOWER_BAND"
BELOW_EMA = "BELOW_EMA"

DESCRIPTIONS = {
    UPPER: "Price is at or above the top Bollinger Band (stretched high — possible sell / take-profit zone)",
    LOWER: "Price is at or below the bottom Bollinger Band (stretched low — possible buy zone, or a falling knife)",
    BELOW_EMA: "Price closed below the EMA — short-term momentum turned down",
}


def condition_masks(df: pd.DataFrame, tolerance: float) -> dict[str, pd.Series]:
    """Boolean Series per signal type, evaluated on every row (used for history too)."""
    pb = df["pct_b"]
    return {
        UPPER: pb >= 1 - tolerance,
        LOWER: pb <= tolerance,
        BELOW_EMA: df["Close"] < df["ema"],
    }


@dataclass
class Signal:
    ticker: str
    kind: str
    price: float
    ema: float
    bb_upper: float
    bb_lower: float
    bb_mid: float
    pct_b: float
    date: str
    description: str = ""
    extra: dict = field(default_factory=dict)

    def key(self) -> str:
        return f"{self.ticker}:{self.kind}"


def detect(ticker: str, df: pd.DataFrame, tolerance: float) -> list[Signal]:
    """Signals active on the latest bar."""
    df = df.dropna(subset=["pct_b", "ema"])
    if df.empty:
        return []
    masks = condition_masks(df, tolerance)
    last = df.iloc[-1]
    out = []
    for kind, mask in masks.items():
        if bool(mask.iloc[-1]):
            # For BELOW_EMA, only alert on a fresh cross (yesterday was above),
            # otherwise a stock in a downtrend would alert every single day.
            if kind == BELOW_EMA and len(mask) > 1 and bool(mask.iloc[-2]):
                continue
            out.append(Signal(
                ticker=ticker, kind=kind, price=float(last["Close"]), ema=float(last["ema"]),
                bb_upper=float(last["bb_upper"]), bb_lower=float(last["bb_lower"]),
                bb_mid=float(last["bb_mid"]), pct_b=float(last["pct_b"]),
                date=str(df.index[-1].date()), description=DESCRIPTIONS[kind],
            ))
    return out
