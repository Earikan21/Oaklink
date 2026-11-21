from datetime import date

import pandas as pd
import pytest

from conftest import FakeMarket, d, make_history, make_holding
from fundkit.models import LedgerRow, Quote, Targets
from fundkit.performance import active_vs_index, benchmark_value, snapshot, turnover_qtd
from fundkit.portfolio import value_holdings

YIELD = 0.04
G1 = 1 + YIELD / 365  # one day of cash accrual


def _q(ticker, price):
    return Quote(ticker=ticker, price=price, bid=None, ask=None, expense_ratio=None, nav=None,
                 quote_type="EQUITY", avg_volume=None)


def _daily(start, days, price_fn):
    """Close frame with one row per calendar day from `start` for `days` days."""
    idx = pd.date_range(start=pd.Timestamp(start), periods=days, freq="D")
    close = [price_fn(i) for i in range(days)]
    return pd.DataFrame({"Close": close}, index=idx)


def _etf_market(vti_hist, bnd_hist, vti_price=None):
    return FakeMarket({
        "VTI": {"history": vti_hist, "quote": _q("VTI", vti_price or float(vti_hist["Close"].iloc[-1]))},
        "BND": {"history": bnd_hist, "quote": _q("BND", float(bnd_hist["Close"].iloc[-1]))},
    })


@pytest.fixture
def fake_market_flat():
    return _etf_market(make_history(100.0, 800, 0.0), make_history(50.0, 800, 0.0))


START = d(-84)


@pytest.fixture
def fake_market_stock_doubles_day_43():
    # VTI is 100 through day 42 after START, 200 from day 43 on. BND flat 50.
    vti = _daily(START, 90, lambda i: 100.0 if i < 43 else 200.0)
    bnd = _daily(START, 90, lambda i: 50.0)
    return _etf_market(vti, bnd)


@pytest.fixture
def fake_market_pick_up_20_etf_up_10():
    # fill on d(-30): VTI 100 -> 110 (+10%); ACME bought at 50, now 60 (+20%)
    vti = _daily(d(-60), 61, lambda i: 100.0 if i <= 30 else 110.0)
    m = _etf_market(vti, make_history(50.0, 800, 0.0))
    m.data["ACME"] = {"quote": _q("ACME", 60.0)}
    return m


def _buy(id_, ticker, day, shares, price, response="applied", action="BUY"):
    return LedgerRow(id=id_, date=day, action=action, ticker=ticker, amount=shares * price, rationale="t",
                     ips_check="PASS", response=response, fill_shares=shares, fill_price=price)


@pytest.fixture
def ledger_one_active_buy():
    return [_buy("1", "ACME", d(-30), 10, 50.0)]


# ---------------------------------------------------------------- benchmark
def test_benchmark_flat_market_grows_only_by_cash_yield(fake_market_flat):
    # Flat prices: only cash accrues; every 42 days cash is trimmed back to 20% of total.
    # Per 42-day cycle: T -> 0.8T + 0.2T*g42.  400 days = 9 cycles (378 days) + 22 days.
    days = 400
    g42, g22 = G1 ** 42, G1 ** 22
    cycle = 0.8 + 0.2 * g42
    expected = 3000.0 * cycle ** 9 * (0.8 + 0.2 * g22)
    got = benchmark_value(3000.0, d(-days), d(0), fake_market_flat, Targets())
    assert got == pytest.approx(expected, rel=1e-12)
    # sanity: strictly between no-rebalance cash growth and no growth
    assert 3000.0 < got < 3000.0 * (0.8 + 0.2 * G1 ** days)


def test_benchmark_rebalances_every_42_days(fake_market_stock_doubles_day_43):
    m = fake_market_stock_doubles_day_43
    g42 = G1 ** 42
    # day 41: before first rebalance
    assert benchmark_value(1000.0, START, START.fromordinal(START.toordinal() + 41), m, Targets()) == \
        pytest.approx(800.0 + 200.0 * G1 ** 41, rel=1e-12)
    # day 42: rebalance happens on day 42 (prices still 100/50): sleeves reset to 50/30/20
    t1 = 800.0 + 200.0 * g42
    assert benchmark_value(1000.0, START, START.fromordinal(START.toordinal() + 42), m, Targets()) == \
        pytest.approx(t1, rel=1e-12)
    # day 84: VTI doubled on day 43; the day-42 rebalance made VTI 0.5*t1 -> t1; bonds 0.3*t1; cash 0.2*t1*g42
    t2 = t1 * (1.0 + 0.3 + 0.2 * g42)
    got = benchmark_value(1000.0, START, d(0), m, Targets())
    assert got == pytest.approx(t2, rel=1e-12)
    # and it differs from never rebalancing, proving the day-42 rebalance occurred
    no_rebal = 500.0 * 2 + 300.0 + 200.0 * G1 ** 84
    assert abs(got - no_rebal) > 0.1


def test_benchmark_raises_when_start_precedes_history(fake_market_flat):
    with pytest.raises(ValueError, match="insufficient history for benchmark"):
        benchmark_value(3000.0, date(2000, 1, 1), d(0), fake_market_flat, Targets())


# ------------------------------------------------------------ active_vs_index
def test_active_vs_index_positive_when_pick_beats_etf(ledger_one_active_buy, fake_market_pick_up_20_etf_up_10):
    holdings = [make_holding("ACME", "active", 10, 50.0)]
    values = {"ACME": 600.0}
    got = active_vs_index(ledger_one_active_buy, holdings, values, fake_market_pick_up_20_etf_up_10, Targets())
    # actual 10*60 = 600; counterfactual 500 * 110/100 = 550
    assert got == pytest.approx(50.0)


def test_active_vs_index_none_without_active_buys(fake_market_pick_up_20_etf_up_10):
    ledger = [
        _buy("1", "VTI", d(-30), 5, 100.0),                       # index ETF, not active
        _buy("2", "BND", d(-30), 5, 50.0),                        # bond ETF
        _buy("3", "ACME", d(-30), 10, 50.0, response="pending"),  # not applied
        _buy("4", "ACME", d(-30), 10, 50.0, action="SELL"),       # not a BUY
    ]
    assert active_vs_index(ledger, [], {}, fake_market_pick_up_20_etf_up_10, Targets()) is None


def test_active_vs_index_skips_sold_and_unpriceable(fake_market_pick_up_20_etf_up_10):
    m = fake_market_pick_up_20_etf_up_10
    ledger = [
        _buy("1", "ACME", d(-30), 10, 50.0),   # held, priced -> +50
        _buy("2", "SOLD", d(-30), 10, 50.0),   # no longer held
        _buy("3", "GHOST", d(-30), 10, 50.0),  # held but quote unavailable
    ]
    holdings = [make_holding("ACME", "active", 10), make_holding("GHOST", "active", 10)]
    values = {"ACME": 600.0, "GHOST": 500.0}
    assert active_vs_index(ledger, holdings, values, m, Targets()) == pytest.approx(50.0)
    # only skipped rows -> None
    assert active_vs_index(ledger[1:], holdings, values, m, Targets()) is None


# ---------------------------------------------------------------- turnover
def test_turnover_qtd_resets_at_quarter():
    ledger = [
        _buy("1", "ACME", date(2026, 9, 30), 10, 50.0),                       # Q3 -> 500
        _buy("2", "ACME", date(2026, 10, 1), 10, 50.0),                       # Q4 -> 500
        _buy("3", "ACME", date(2026, 11, 15), 4, 25.0, action="SELL"),        # Q4 -> 100
        _buy("4", "ACME", date(2026, 11, 16), 99, 99.0, response="pending"),  # not applied
        _buy("5", "ACME", date(2026, 11, 17), 99, 99.0, response="skipped"),
        _buy("6", "ACME", date(2026, 11, 18), 99, 99.0, action="REBALANCE"),  # not BUY/SELL
    ]
    assert turnover_qtd(ledger, date(2026, 11, 29), 2000.0) == pytest.approx(600.0 / 2000.0)
    # new quarter: nothing yet
    assert turnover_qtd(ledger, date(2027, 1, 2), 2000.0) == 0.0
    # in Q3 only the Sep 30 row (later rows are in the future)
    assert turnover_qtd(ledger, date(2026, 9, 30), 1000.0) == pytest.approx(0.5)
    assert turnover_qtd(ledger, date(2026, 11, 29), 0.0) == 0.0


# ---------------------------------------------------------------- snapshot
def test_snapshot_sleeves_sum_to_total_and_has_benchmark(tmp_path, fake_market_flat):
    from fundkit.store import Store
    store = Store(tmp_path)
    store.init()
    store.save_holdings([
        make_holding("VTI", "indexed_equity", 5, 100.0),
        make_holding("ACME", "active", 10, 50.0),
        make_holding("BND", "bonds", 12, 50.0),
        make_holding("CASH", "cash", 600.0),
    ])
    store.save_ledger([_buy("1", "ACME", d(-30), 10, 50.0)])
    fake_market_flat.data["ACME"] = {"quote": _q("ACME", 60.0)}
    snap = snapshot(store, fake_market_flat, Targets(), d(0), d(-100), 3000.0)
    assert snap.date == d(0)
    assert snap.indexed_equity == pytest.approx(500.0)
    assert snap.active == pytest.approx(600.0)
    assert snap.bonds == pytest.approx(600.0)
    assert snap.cash == pytest.approx(600.0)
    assert snap.total == pytest.approx(2300.0)
    assert snap.indexed_equity + snap.active + snap.bonds + snap.cash == pytest.approx(snap.total)
    assert snap.bench_total == pytest.approx(
        benchmark_value(3000.0, d(-100), d(0), fake_market_flat, Targets()))
    assert snap.active_vs_index == pytest.approx(600.0 - 500.0)  # flat VTI
    assert snap.turnover_qtd == pytest.approx(500.0 / 2300.0)
    assert store.load_performance() == []  # snapshot() does not append


class _PeriodAwareMarket(FakeMarket):
    """Like a real provider: a '2y' request returns only the last 2 calendar years."""

    def history(self, ticker, period="1y"):
        df = super().history(ticker, period)
        if period == "2y":
            df = df[df.index >= df.index[-1] - pd.Timedelta(days=730)]
        return df


def test_benchmark_uses_full_history_beyond_two_years():
    m = _PeriodAwareMarket({
        "VTI": {"history": make_history(100.0, 1000, 0.0)},
        "BND": {"history": make_history(50.0, 1000, 0.0)},
    })
    got = benchmark_value(3000.0, d(-900), d(0), m, Targets())
    assert 3000.0 < got < 3000.0 * (0.8 + 0.2 * G1 ** 900)


def test_active_vs_index_fill_after_last_index_close(fake_market_pick_up_20_etf_up_10):
    # index history ends d(0); a fill dated one day later has no close yet -> ratio 1.0
    row = _buy("1", "ACME", d(1), 10, 50.0)
    got = active_vs_index([row], [make_holding("ACME", "active", 10)], {"ACME": 600.0},
                          fake_market_pick_up_20_etf_up_10, Targets())
    assert got == pytest.approx(10 * 60.0 - 500.0)  # counterfactual == dollars


def test_benchmark_saturday_start_uses_prior_friday_close():
    # Fri 2026-11-20 closes at 100, Mon 11-23 at 200; start Saturday 11-21, end Sunday 11-22.
    idx = pd.to_datetime(["2026-11-19", "2026-11-20", "2026-11-23"])
    vti = pd.DataFrame({"Close": [90.0, 100.0, 200.0]}, index=idx)
    bnd = pd.DataFrame({"Close": [50.0, 50.0, 50.0]}, index=idx)
    m = _etf_market(vti, bnd)
    # initial units from Friday's 100: 500/100 = 5 shares. One day later (Sunday) VTI still 100.
    got = benchmark_value(1000.0, date(2026, 11, 21), date(2026, 11, 22), m, Targets())
    assert got == pytest.approx(5.0 * 100.0 + 300.0 + 200.0 * G1)
    # after Monday's jump: those same 5 units are worth 200 each
    got2 = benchmark_value(1000.0, date(2026, 11, 21), date(2026, 11, 23), m, Targets())
    assert got2 == pytest.approx(5.0 * 200.0 + 300.0 + 200.0 * G1 ** 2)


def test_snapshot_reports_unpriced_and_excludes_from_total(tmp_path, fake_market_flat):
    from fundkit.store import Store
    store = Store(tmp_path)
    store.init()
    store.save_holdings([make_holding("VTI", "indexed_equity", 5, 100.0),
                         make_holding("GHOST", "active", 10, 50.0),
                         make_holding("CASH", "cash", 600.0)])
    snap = snapshot(store, fake_market_flat, Targets(), d(0), d(-100), 3000.0)
    assert snap.unpriced == "GHOST"
    assert snap.total == pytest.approx(500.0 + 600.0)
    assert snap.active == 0.0
    # round-trips through the CSV store
    store.append_performance(snap)
    assert store.load_performance()[0].unpriced == "GHOST"
