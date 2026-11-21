"""Pairwise Pearson correlation of daily log returns, for diversification checks."""
import numpy as np
import pandas as pd

from fundkit.market import MarketData

MIN_OVERLAP = 60


def pairwise(tickers: list[str], market: MarketData, period: str = "1y") -> pd.DataFrame:
    closes = pd.concat({t: market.history(t, period)["Close"] for t in tickers}, axis=1, join="inner").dropna()
    returns = np.log(closes / closes.shift(1)).dropna()
    if len(returns) < MIN_OVERLAP:
        raise ValueError("insufficient overlap")
    return returns.corr()


def max_corr_with(ticker: str, held: list[str], market: MarketData) -> float:
    others = [h for h in dict.fromkeys(held) if h != ticker]
    if not others:
        return 0.0
    return float(pairwise([ticker] + others, market).loc[ticker, others].max())
