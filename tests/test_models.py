from datetime import date

import pytest
from pydantic import ValidationError

from fundkit.models import Holding, LedgerRow, Quote, Snapshot, Targets, WatchItem


def test_targets_default_sum_to_one():
    assert Targets().stocks + Targets().bonds + Targets().cash == pytest.approx(1.0)


def test_targets_reject_bad_sum():
    with pytest.raises(ValidationError):
        Targets(stocks=0.6, bonds=0.3, cash=0.2)


def test_cash_holding_uses_shares_as_dollars():
    h = Holding(ticker="CASH", sleeve="cash", shares=600.0, cost_basis=1.0, lot_date=date(2026, 11, 22))
    assert h.shares == 600.0


def test_watch_item_round_trips_status():
    w = WatchItem(
        ticker="ACME",
        thesis="undervalued cash generator",
        last_valued=None,
        fair_value=None,
        upside=None,
        status="candidate",
    )
    assert w.status == "candidate"
    assert w.last_valued is None


def test_ledger_row_response_defaults_to_pending():
    row = LedgerRow(
        id="2026-11-22-001",
        date=date(2026, 11, 22),
        action="BUY",
        ticker="VTI",
        amount=600.0,
        rationale="rebalance to indexed sleeve",
        ips_check="PASS",
    )
    assert row.response == "pending"
    assert row.amount == 600.0


def test_snapshot_round_trips_total():
    s = Snapshot(
        date=date(2026, 11, 22),
        total=100_000.0,
        indexed_equity=50_000.0,
        active=10_000.0,
        bonds=30_000.0,
        cash=10_000.0,
        bench_total=99_000.0,
        active_vs_index=0.01,
        turnover_qtd=0.02,
    )
    assert s.total == 100_000.0
    assert s.active_vs_index == 0.01


def test_quote_round_trips_price():
    q = Quote(
        ticker="VTI",
        price=250.0,
        bid=249.9,
        ask=250.1,
        expense_ratio=0.0003,
        nav=249.95,
        quote_type="ETF",
        avg_volume=3_000_000.0,
    )
    assert q.price == 250.0
    assert q.quote_type == "ETF"
