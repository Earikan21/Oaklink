from datetime import date

from conftest import d
from fundkit.models import Holding, LedgerRow, WatchItem
from fundkit.reconcile import reconcile


def row(id, offset, action, ticker, **kw):
    return LedgerRow(id=id, date=d(offset), action=action, ticker=ticker, amount=kw.pop("amount", 0),
                     rationale="", ips_check="PASS", **kw)


def holdings(s):
    return {h.ticker: h for h in s.load_holdings()}


def test_done_buy_adds_lot_and_reduces_cash(store_with_cash_3000):
    s = store_with_cash_3000
    s.append_ledger([row("b1", -3, "BUY", "VTI", amount=1500, response="done", fill_shares=5.0, fill_price=300.0)])
    r = reconcile(s, today=d(0))
    assert r.applied == ["b1"]
    h = holdings(s)
    assert h["VTI"].shares == 5.0 and h["CASH"].shares == 1500.0
    assert h["VTI"].cost_basis == 300.0 and h["VTI"].lot_date == d(-3) and h["VTI"].sleeve == "indexed_equity"


def test_done_without_fill_is_incomplete(store_with_cash_3000):
    s = store_with_cash_3000
    s.append_ledger([row("b2", -3, "BUY", "VTI", amount=1500, response="done"),
                     row("b3", -3, "BUY", "VTI", amount=1500, response="done", fill_shares=5.0)])
    r = reconcile(s, today=d(0))
    assert r.incomplete == ["b2", "b3"] and r.applied == []
    assert [h.ticker for h in s.load_holdings()] == ["CASH"]
    assert [x.response for x in s.load_ledger()] == ["done", "done"]


def test_pending_older_than_7_days_is_dropped(store_with_cash_3000):
    s = store_with_cash_3000
    s.append_ledger([row("p1", -8, "BUY", "BND", amount=900)])
    r = reconcile(s, today=d(0))
    assert r.dropped == ["p1"] and s.load_ledger()[0].response == "skipped"


def test_pending_within_7_days_is_carried(store_with_cash_3000):
    s = store_with_cash_3000
    s.append_ledger([row("p7", -7, "BUY", "BND", amount=900)])
    r = reconcile(s, today=d(0))
    assert r.carried == ["p7"] and r.dropped == [] and s.load_ledger()[0].response == "pending"


def test_sell_is_fifo(tmp_path):
    from fundkit.store import Store
    s = Store(tmp_path); s.init()
    s.save_holdings([
        Holding(ticker="VTI", sleeve="indexed_equity", shares=3.0, cost_basis=250.0, lot_date=date(2026, 6, 1)),
        Holding(ticker="VTI", sleeve="indexed_equity", shares=2.0, cost_basis=200.0, lot_date=date(2026, 1, 1)),
        Holding(ticker="CASH", sleeve="cash", shares=0.0, cost_basis=1.0, lot_date=date(2026, 1, 1)),
    ])
    s.append_ledger([row("s1", -1, "SELL", "VTI", amount=750, response="done", fill_shares=2.5, fill_price=300.0)])
    r = reconcile(s, today=d(0))
    assert r.applied == ["s1"] and r.warnings == []
    h = s.load_holdings()
    vti = [x for x in h if x.ticker == "VTI"]
    assert [(x.lot_date, x.shares) for x in vti] == [(date(2026, 6, 1), 2.5)]
    assert holdings(s)["CASH"].shares == 750.0


def test_sell_exceeding_holdings_sells_what_exists_and_warns(store_with_cash_3000):
    s = store_with_cash_3000
    s.save_holdings(s.load_holdings() + [
        Holding(ticker="AAPL", sleeve="active", shares=1.0, cost_basis=100.0, lot_date=date(2026, 1, 1))])
    s.append_ledger([row("s2", -1, "SELL", "AAPL", response="done", fill_shares=3.0, fill_price=200.0)])
    r = reconcile(s, today=d(0))
    assert r.applied == ["s2"]
    assert r.warnings and "AAPL" in r.warnings[0] and "s2" in r.warnings[0] and "2" in r.warnings[0]
    h = holdings(s)
    assert "AAPL" not in h and h["CASH"].shares == 3600.0


def test_buy_sleeve_resolution_and_watchlist_held(store_with_cash_3000):
    s = store_with_cash_3000
    s.save_watchlist([WatchItem(ticker="MSFT", thesis="t", last_valued=None, fair_value=None, upside=None, status="candidate")])
    s.append_ledger([
        row("a", -2, "BUY", "MSFT", response="done", fill_shares=1.0, fill_price=100.0),
        row("b", -2, "BUY", "BND", response="done", fill_shares=1.0, fill_price=100.0),
        row("c", -2, "BUY", "XYZ", response="done", fill_shares=1.0, fill_price=100.0),
    ])
    reconcile(s, today=d(0))
    h = holdings(s)
    assert (h["MSFT"].sleeve, h["BND"].sleeve, h["XYZ"].sleeve) == ("active", "bonds", "active")
    assert s.load_watchlist()[0].status == "held"


def test_cash_may_go_negative(store_with_cash_3000):
    s = store_with_cash_3000
    s.append_ledger([row("big", -1, "BUY", "VTI", response="done", fill_shares=20.0, fill_price=300.0)])
    reconcile(s, today=d(0))
    assert holdings(s)["CASH"].shares == -3000.0


def test_reconcile_twice_applies_once(store_with_cash_3000):
    s = store_with_cash_3000
    s.append_ledger([row("b1", -3, "BUY", "VTI", amount=1500, response="done", fill_shares=5.0, fill_price=300.0)])
    assert reconcile(s, today=d(0)).applied == ["b1"]
    assert s.load_ledger()[0].response == "applied"
    assert reconcile(s, today=d(0)).applied == []
    h = holdings(s)
    assert h["VTI"].shares == 5.0 and h["CASH"].shares == 1500.0


def test_index_etf_on_watchlist_is_still_indexed_equity(store_with_cash_3000):
    s = store_with_cash_3000
    s.save_watchlist([WatchItem(ticker="VTI", thesis="t", last_valued=None, fair_value=None, upside=None, status="candidate")])
    s.append_ledger([row("v", -1, "BUY", "VTI", response="done", fill_shares=1.0, fill_price=100.0)])
    reconcile(s, today=d(0))
    assert holdings(s)["VTI"].sleeve == "indexed_equity"
    assert s.load_watchlist()[0].status == "held"
