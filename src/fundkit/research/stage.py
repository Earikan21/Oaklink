"""Weinstein-style market stage (1-4) from price vs 50/150/200-day moving averages."""
from typing import Literal

from fundkit.market import MarketData

Stage = Literal[1, 2, 3, 4]
MIN_ROWS = 250
SLOPE_LOOKBACK = 21  # trading days
FLAT_BAND = 0.05  # price within +/-5% of the 200MA
FLAT_SLOPE = 0.01  # 200MA moved < 1% over the lookback


def classify(ticker: str, market: MarketData) -> Stage:
    close = market.history(ticker, "2y")["Close"]
    if len(close) < MIN_ROWS:
        raise ValueError("insufficient history")
    price = float(close.iloc[-1])
    sma50 = float(close.rolling(50).mean().iloc[-1])
    sma150 = float(close.rolling(150).mean().iloc[-1])
    sma200_series = close.rolling(200).mean()
    sma200 = float(sma200_series.iloc[-1])
    sma200_prior = float(sma200_series.iloc[-1 - SLOPE_LOOKBACK])

    if price > sma150 > sma200 and sma200 > sma200_prior and price > sma50:
        return 2
    if price < sma200 and sma200 < sma200_prior:
        return 4
    if abs(price / sma200 - 1) <= FLAT_BAND and abs(sma200 / sma200_prior - 1) < FLAT_SLOPE:
        return 1
    return 3
