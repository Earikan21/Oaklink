import pandas as pd
import pytest

from conftest import FakeMarket
from fundkit.research.estimates import revision_summary

PERIODS = ["0q", "+1q", "0y", "+1y"]


def _trend(current_0y, ago30_0y):
    df = pd.DataFrame(
        {"current": [1.0, 1.1, current_0y, 6.0], "7daysAgo": [1.0, 1.1, current_0y, 6.0],
         "30daysAgo": [1.0, 1.1, ago30_0y, 6.0], "60daysAgo": [1.0] * 4, "90daysAgo": [1.0] * 4},
        index=PERIODS,
    )
    return df


def _revisions(up30, down30, down_col="downLast7Days"):
    return pd.DataFrame(
        {"upLast7days": [0] * 4, "upLast30days": [1, 1, up30, 1],
         "downLast30days": [1, 1, down30, 1], down_col: [0] * 4},
        index=PERIODS,
    )


def _earnings(rows):
    """rows: list of (date, reported, estimate), any order."""
    idx = pd.DatetimeIndex([r[0] for r in rows], name="Earnings Date")
    return pd.DataFrame({"EPS Estimate": [r[2] for r in rows], "Reported EPS": [r[1] for r in rows]}, index=idx)


def _market(trend, revisions, earnings=None):
    entry = {"eps_trend": trend, "eps_revisions": revisions}
    if earnings is not None:
        entry["earnings_dates"] = earnings
    return FakeMarket({"X": entry})


def test_revision_up_when_30d_gain_over_1pct():
    r = revision_summary("X", _market(_trend(5.10, 5.00), _revisions(7, 3)))
    assert r.direction == "up"
    assert r.pct_30d == pytest.approx(5.10 / 5.00 - 1)
    assert r.pct_30d == pytest.approx(0.02)


def test_revision_down_when_30d_drop_over_1pct():
    r = revision_summary("X", _market(_trend(4.90, 5.00), _revisions(7, 3)))
    assert r.direction == "down"
    assert r.pct_30d == pytest.approx(-0.02)


def test_revision_flat_within_1pct():
    r = revision_summary("X", _market(_trend(5.04, 5.00), _revisions(7, 3)))
    assert r.direction == "flat"


def test_revision_flat_when_30d_ago_missing():
    trend = _trend(5.10, 5.00).drop(columns=["30daysAgo"])
    r = revision_summary("X", _market(trend, _revisions(7, 3)))
    assert r.direction == "flat" and r.pct_30d == 0.0


def test_revision_flat_when_30d_ago_zero():
    r = revision_summary("X", _market(_trend(0.5, 0.0), _revisions(7, 3)))
    assert r.direction == "flat" and r.pct_30d == 0.0


def test_breadth_ratio():
    r = revision_summary("X", _market(_trend(5.0, 5.0), _revisions(7, 3)))
    assert (r.up_30d, r.down_30d) == (7, 3)
    assert r.breadth == pytest.approx(0.7)


def test_breadth_none_when_no_revisions():
    r = revision_summary("X", _market(_trend(5.0, 5.0), _revisions(0, 0)))
    assert r.breadth is None


def test_revisions_columns_case_insensitive():
    r = revision_summary("X", _market(_trend(5.0, 5.0), _revisions(2, 2, down_col="downlast7days").rename(
        columns={"upLast30days": "UPLAST30DAYS", "downLast30days": "downlast30DAYS"})))
    assert (r.up_30d, r.down_30d) == (2, 2)


def test_beat_rate_uses_most_recent_four_reported_rows():
    e = _earnings([
        ("2026-10-01", 1.1, 1.0),   # beat
        ("2026-07-01", 1.0, 1.0),   # equal -> not a beat
        ("2026-04-01", 1.2, 1.0),   # beat
        ("2026-01-01", 0.9, 1.0),   # miss
        ("2025-10-01", 2.0, 1.0),   # beat but 5th most recent: ignored
        ("2027-01-15", None, 1.0),  # future, not reported: ignored
    ])
    r = revision_summary("X", _market(_trend(5.0, 5.0), _revisions(1, 1), e))
    assert r.beat_rate_4q == pytest.approx(2 / 4)


def test_beat_rate_with_fewer_than_four_rows():
    e = _earnings([("2026-10-01", 1.1, 1.0), ("2026-07-01", 0.9, 1.0)])
    r = revision_summary("X", _market(_trend(5.0, 5.0), _revisions(1, 1), e))
    assert r.beat_rate_4q == pytest.approx(0.5)


def test_beat_rate_none_without_reported_rows():
    e = _earnings([("2027-01-15", None, 1.0)])
    r = revision_summary("X", _market(_trend(5.0, 5.0), _revisions(1, 1), e))
    assert r.beat_rate_4q is None


def test_beat_rate_none_when_earnings_dates_unavailable():
    r = revision_summary("X", _market(_trend(5.0, 5.0), _revisions(1, 1)))
    assert r.beat_rate_4q is None
