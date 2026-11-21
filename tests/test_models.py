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


# ---------------------------------------------------------------- I9: Targets validators
FRACTIONS = ["band", "active_ceiling", "name_cap", "trim_threshold", "review_trim", "fee_cap", "min_upside",
             "max_corr", "max_spread", "reval_move"]


def test_name_cap_given_as_percent_is_rejected():
    with pytest.raises(ValidationError):
        Targets(name_cap=5)


@pytest.mark.parametrize("field", FRACTIONS)
@pytest.mark.parametrize("bad", [0.0, -0.1, 1.5])
def test_fraction_fields_must_be_in_0_1(field, bad):
    with pytest.raises(ValidationError, match="must be in \\(0, 1\\]"):
        Targets(**{field: bad})


def test_allocation_fractions_must_be_positive():
    with pytest.raises(ValidationError):
        Targets(stocks=0.7, bonds=0.3, cash=0.0)


@pytest.mark.parametrize("kw", [
    dict(name_cap=0.06, active_ceiling=0.05),           # name_cap > active_ceiling
    dict(active_ceiling=0.55),                           # active_ceiling > stocks
    dict(review_trim=0.08),                              # review_trim > trim_threshold
    dict(name_cap=0.065),                                # name_cap > review_trim
    dict(fee_cap=0.01),                                  # above the IPS 0.9% cap
    dict(review_every=0),
])
def test_targets_ordering_and_caps_rejected(kw):
    with pytest.raises(ValidationError):
        Targets(**kw)


def test_boundary_targets_accepted():
    t = Targets(name_cap=0.06, review_trim=0.06, trim_threshold=0.06, active_ceiling=0.06, fee_cap=0.009,
                review_every=1, stocks=0.5, max_corr=1.0)
    assert t.name_cap == t.review_trim == t.trim_threshold == 0.06
