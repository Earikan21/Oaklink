from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from conftest import FakeMarket
from fundkit.research.earnings import EarningsResult, reported_within, upcoming_within

TODAY = date(2026, 11, 29)  # a Sunday; test calendar anchor
ET = "America/New_York"
NAN = np.nan


def _earnings(rows, with_surprise=True):
    """rows: (ET timestamp string, est, actual, surprise); newest first like yfinance."""
    idx = pd.DatetimeIndex([pd.Timestamp(r[0], tz=ET) for r in rows], name="Earnings Date")
    df = pd.DataFrame(
        {"EPS Estimate": [r[1] for r in rows], "Reported EPS": [r[2] for r in rows],
         "Surprise(%)": [r[3] for r in rows]},
        index=idx,
    )
    return df if with_surprise else df.drop(columns="Surprise(%)")


def _history(closes: dict[str, float]) -> pd.DataFrame:
    idx = pd.DatetimeIndex(list(closes))  # tz-naive, like MarketData.history
    c = list(closes.values())
    return pd.DataFrame({"Close": c, "Open": c, "High": c, "Low": c, "Volume": 1.0}, index=idx)


def _market(earnings, history=None):
    entry = {"earnings_dates": earnings}
    if history is not None:
        entry["history"] = history
    return FakeMarket({"ACME": entry})


# 3 past rows + 1 future row (in 5 days)
PAST_AND_FUTURE = [
    ("2026-12-04 08:00", 1.10, NAN, NAN),
    ("2026-11-27 16:30", 1.00, 1.05, 5.0),
    ("2026-08-20 16:30", 0.90, 0.95, 5.5),
    ("2026-05-21 16:30", 0.80, 0.78, -2.5),
]


def test_upcoming_found_within_7_days():
    m = _market(_earnings(PAST_AND_FUTURE))
    ts = upcoming_within("ACME", m, TODAY)
    assert ts == datetime(2026, 12, 4, 8, 0, tzinfo=pd.Timestamp("2026-12-04", tz=ET).tzinfo)
    assert ts.utcoffset() is not None and ts.date() == date(2026, 12, 4)


def test_upcoming_none_when_beyond_window():
    m = _market(_earnings(PAST_AND_FUTURE))
    assert upcoming_within("ACME", m, TODAY, days=3) is None


def test_upcoming_none_when_only_past_rows():
    m = _market(_earnings(PAST_AND_FUTURE[1:]))
    assert upcoming_within("ACME", m, TODAY) is None


def test_upcoming_none_when_empty_or_unavailable():
    assert upcoming_within("ACME", _market(_earnings(PAST_AND_FUTURE).iloc[0:0]), TODAY) is None
    assert upcoming_within("ZZZ", _market(_earnings(PAST_AND_FUTURE)), TODAY) is None


def test_reported_after_close_reaction_uses_next_session():
    # reported Fri 2026-11-27 16:30 ET: report-day close 100, next session (Mon 11-30) 105 -> +5%
    hist = _history({"2026-11-25": 99.0, "2026-11-26": 99.5, "2026-11-27": 100.0, "2026-11-30": 105.0})
    m = _market(_earnings(PAST_AND_FUTURE), hist)
    r = reported_within("ACME", m, TODAY)
    assert isinstance(r, EarningsResult)
    assert r.report_ts.date() == date(2026, 11, 27) and r.report_ts.hour == 16
    assert (r.eps_est, r.eps_actual, r.surprise_pct) == (1.00, 1.05, 5.0)
    assert r.reaction_pct == pytest.approx(5.0)


def test_reported_before_open_reaction_uses_same_day():
    # reported Fri 2026-11-27 08:00 ET: prior close (11-26) 100, report-day close 97 -> -3%
    rows = [("2026-11-27 08:00", 1.00, 0.90, -10.0)]
    hist = _history({"2026-11-25": 101.0, "2026-11-26": 100.0, "2026-11-27": 97.0})
    r = reported_within("ACME", _market(_earnings(rows), hist), TODAY)
    assert r.reaction_pct == pytest.approx(-3.0)


def test_reaction_none_when_no_post_close():
    # after-close report on the latest bar: the next session has not closed yet
    hist = _history({"2026-11-25": 99.0, "2026-11-26": 99.5, "2026-11-27": 100.0})
    r = reported_within("ACME", _market(_earnings(PAST_AND_FUTURE), hist), TODAY)
    assert r is not None and r.reaction_pct is None
    assert r.eps_actual == 1.05


def test_reaction_none_when_history_unavailable():
    r = reported_within("ACME", _market(_earnings(PAST_AND_FUTURE)), TODAY)  # no history key
    assert r is not None and r.reaction_pct is None


def test_reported_none_when_last_report_8_days_ago():
    rows = [("2026-11-21 16:30", 1.00, 1.05, 5.0)]
    assert reported_within("ACME", _market(_earnings(rows), _history({"2026-11-20": 1.0})), TODAY) is None


def test_reported_picks_most_recent_in_window():
    rows = [("2026-11-27 16:30", 1.0, 1.1, 10.0), ("2026-11-24 08:00", 0.9, 0.9, 0.0)]
    hist = _history({"2026-11-27": 100.0, "2026-11-30": 110.0})
    r = reported_within("ACME", _market(_earnings(rows), hist), TODAY)
    assert r.report_ts.date() == date(2026, 11, 27)


def test_surprise_computed_when_column_missing():
    rows = [("2026-11-27 16:30", -2.0, -1.0, NAN)]
    m = _market(_earnings(rows, with_surprise=False), _history({"2026-11-27": 1.0}))
    assert reported_within("ACME", m, TODAY).surprise_pct == pytest.approx(50.0)  # (-1+2)/2*100


def test_surprise_computed_when_column_nan_and_none_when_est_zero():
    m = _market(_earnings([("2026-11-27 16:30", 1.0, 1.2, NAN)]), _history({"2026-11-27": 1.0}))
    assert reported_within("ACME", m, TODAY).surprise_pct == pytest.approx(20.0)
    m = _market(_earnings([("2026-11-27 16:30", 0.0, 1.2, NAN)]), _history({"2026-11-27": 1.0}))
    assert reported_within("ACME", m, TODAY).surprise_pct is None


def test_missing_estimate_is_none():
    m = _market(_earnings([("2026-11-27 16:30", NAN, 1.2, NAN)]), _history({"2026-11-27": 1.0}))
    r = reported_within("ACME", m, TODAY)
    assert r.eps_est is None and r.surprise_pct is None


def test_market_unavailable_returns_none():
    m = FakeMarket({})
    assert upcoming_within("ACME", m, TODAY) is None
    assert reported_within("ACME", m, TODAY) is None


def test_non_eastern_index_is_converted_to_et_dates():
    # 2026-11-28 02:00 UTC is Fri 2026-11-27 21:00 ET: after-close report on the 27th, not the 28th
    idx = pd.DatetimeIndex([pd.Timestamp("2026-11-28 02:00", tz="UTC")], name="Earnings Date")
    df = pd.DataFrame({"EPS Estimate": [1.0], "Reported EPS": [1.1], "Surprise(%)": [10.0]}, index=idx)
    hist = _history({"2026-11-27": 100.0, "2026-11-30": 104.0})
    r = reported_within("ACME", _market(df, hist), TODAY)
    assert r.report_ts.date() == date(2026, 11, 27)
    assert r.reaction_pct == pytest.approx(4.0)
