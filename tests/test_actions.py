from datetime import date

import numpy as np
import pandas as pd
import pytest

import fundkit.actions as actions_mod
from conftest import FakeMarket, _acme_market, d, make_history
from fundkit.actions import TAX_NOTE, ActionSet, build_actions
from fundkit.models import Holding, LedgerRow, Quote, Targets, WatchItem
from fundkit.store import Store

TODAY = d(0)  # 2026-11-29
PEERS = ["P1", "P2", "P3"]


# ---------------------------------------------------------------- helpers
def _q(ticker: str, price: float, bid: float | None = None, ask: float | None = None,
       quote_type: str = "ETF", expense_ratio: float | None = None) -> Quote:
    if expense_ratio is None and quote_type == "ETF":
        expense_ratio = 0.0003  # a compliant index fund unless a test says otherwise
    return Quote(ticker=ticker, price=price, bid=bid, ask=ask, expense_ratio=expense_ratio, nav=None,
                 quote_type=quote_type, avg_volume=None)


def _lot(ticker, sleeve, shares, cost=1.0, when=d(-7)) -> Holding:
    return Holding(ticker=ticker, sleeve=sleeve, shares=shares, cost_basis=cost, lot_date=when)


def _hist(seed: int, drift: float = 0.002, start: float = 30.0) -> pd.DataFrame:
    returns = drift + np.random.default_rng(seed).normal(0.0, 0.01, 300)
    base = make_history(start, len(returns))
    close = start * np.exp(np.cumsum(returns))
    base["Close"], base["High"], base["Low"] = close, close * 1.01, close * 0.99
    return base


def _store(tmp_path, holdings, watch=(), ledger=()) -> Store:
    s = Store(tmp_path)
    s.init()
    s.save_holdings(list(holdings))
    s.save_watchlist(list(watch))
    s.save_ledger(list(ledger))
    return s


def _market(prices: dict[str, float], candidates: tuple[str, ...] = (), held_hist: tuple[str, ...] = ()) -> FakeMarket:
    """Quotes for `prices`; each name in `candidates` is a clean gate candidate (ACME clone, upside ~0.31)."""
    m = FakeMarket({})
    if candidates:
        m = _acme_market(info_overrides={"debtToEquity": 50.0, "sector": "Industrials", "longName": "Acme Corp"})
        acme = m.data.pop("ACME")
        for i, t in enumerate(candidates):
            m.data[t] = {**acme, "quote": _q(t, 33.0, 33.0, 33.03, "EQUITY"), "history": _hist(10 + i)}
        m.data["__screen__"] = PEERS
    for t, p in prices.items():
        m.data.setdefault(t, {})["quote"] = _q(t, p, quote_type="EQUITY" if t not in ("VTI", "BND") else "ETF")
        if t in ("VTI", "BND"):
            m.data[t].setdefault("info", {"longName": f"Vanguard {t} ETF"})
    for i, t in enumerate(held_hist):
        m.data[t]["history"] = _hist(100 + i, drift=0.001, start=50.0)
    return m


def _down_revisions(m: FakeMarket, ticker: str) -> None:
    m.data[ticker]["eps_trend"] = pd.DataFrame({"current": [1.8], "30daysAgo": [2.0]}, index=["0y"])
    m.data[ticker]["eps_revisions"] = pd.DataFrame({"upLast30days": [0], "downLast30days": [3]}, index=["0y"])


def _held(ticker="HELD", fair=80.0, upside=0.6) -> WatchItem:
    return WatchItem(ticker=ticker, thesis="t", last_valued=d(-10), fair_value=fair, upside=upside, status="held")


def _passed(ticker, upside) -> WatchItem:
    return WatchItem(ticker=ticker, thesis="t", last_valued=d(-1), fair_value=43.0, upside=upside, status="passed")


def _pairs(props):
    return [(p.action, p.ticker, round(p.dollars, 6)) for p in props]


def _snapshot(root):
    return {p.name: p.read_bytes() for p in sorted(root.iterdir()) if p.is_file()}


# ---------------------------------------------------------------- first run
@pytest.fixture
def fake_market_vti300_bnd75():
    return _market({"VTI": 300.0, "BND": 75.0})


@pytest.mark.parametrize("review", [True, False])
def test_first_run_proposes_index_and_bond_only(store_with_cash_3000, fake_market_vti300_bnd75, review):
    a = build_actions(store_with_cash_3000, fake_market_vti300_bnd75, Targets(), TODAY, review=review)
    assert isinstance(a, ActionSet)
    assert [(p.action, p.ticker) for p in a.proposals] == [("BUY", "VTI"), ("BUY", "BND")]
    assert a.proposals[0].dollars == 0.50 * 3000 == 1500
    assert a.proposals[1].shares == 12  # round(0.30 * 3000 / 75) = round(12.0)
    assert a.proposals[1].dollars == 12 * 75 == 900
    assert all(p.reason == "initial allocation" for p in a.proposals)
    assert a.gated == [] and a.sells == [] and a.ledger_notes == []
    assert a.total == 3000 and a.values == {"CASH": 3000.0}
    assert a.compliance.sleeve_flags  # 0% stocks / 0% bonds / 100% cash is flagged


def test_first_run_unpriceable_bond_etf_raises(store_with_cash_3000):
    with pytest.raises(ValueError, match="BND"):
        build_actions(store_with_cash_3000, _market({"VTI": 300.0}), Targets(), TODAY, review=True)


# ---------------------------------------------------------------- trims
@pytest.fixture
def store_active_name_at_8pct_and_stocks_60pct(tmp_path):
    # total 10_000: VTI 52*100=5200, ACME 16*50=800 (8%), BND 40*75=3000, CASH 1000 -> stocks 60%, cash 10%
    return _store(tmp_path, [
        _lot("VTI", "indexed_equity", 52, 90.0), _lot("ACME", "active", 16, 40.0, d(-400)),
        _lot("BND", "bonds", 40, 75.0), _lot("CASH", "cash", 1000.0)])


def test_trim_then_rebalance_uses_post_trim_weights(store_active_name_at_8pct_and_stocks_60pct):
    m = _market({"VTI": 100.0, "ACME": 50.0, "BND": 75.0})
    a = build_actions(store_active_name_at_8pct_and_stocks_60pct, m, Targets(), TODAY, review=False)
    # trim ACME 800 -> 0.05 * 10_000 = 500: sell 300 = 6 shares from the long-term lot
    assert len(a.sells) == 1
    trim = a.sells[0]
    assert (trim.action, trim.ticker, trim.dollars) == ("SELL", "ACME", 300.0)
    assert trim.reason == "trim 8.0% → 5% cap"
    assert trim.est_tax == pytest.approx((50.0 - 40.0) * 6 * 0.15)  # 9.00
    assert TAX_NOTE in a.notes
    # post-trim stocks = (5200 + 500) / 10_000 = 57% -> SELL VTI (0.57 - 0.50) * 10_000 = 700
    # (pre-trim weights would have given 1000)
    assert _pairs(a.proposals) == [("SELL", "VTI", 700.0)]
    assert all(p.ticker != "ACME" for p in a.proposals)
    stocks_after = (5200 - 700 + 500) / 10_000
    assert abs(stocks_after - 0.50) <= Targets().band


@pytest.fixture
def store_two_lots_one_old(tmp_path):
    # ACME 18 shares @50 = 900 of 10_000 (9%). Short-term lot listed FIRST in the file.
    return _store(tmp_path, [
        _lot("VTI", "indexed_equity", 45, 90.0),
        _lot("ACME", "active", 12, 45.0, d(-100)),   # short-term
        _lot("ACME", "active", 6, 20.0, d(-400)),    # long-term
        _lot("BND", "bonds", 40, 75.0), _lot("CASH", "cash", 1600.0)])


def test_sell_prefers_long_term_lot_and_states_tax(store_two_lots_one_old):
    m = _market({"VTI": 100.0, "ACME": 50.0, "BND": 75.0})
    a = build_actions(store_two_lots_one_old, m, Targets(), TODAY, review=False)
    trim = a.sells[0]
    # sell 900 - 500 = 400 = 8 shares: 6 from the LT lot, then 2 from the ST lot
    assert (trim.ticker, trim.dollars) == ("ACME", 400.0)
    lt_tax = (50 - 20) * 6 * 0.15   # 27.0
    st_tax = (50 - 45) * 2 * 0.24   # 2.4
    assert trim.est_tax == pytest.approx(lt_tax + st_tax)  # 29.4 (ST-first would be 9.6)
    assert TAX_NOTE in a.notes


def test_lot_exactly_365_days_old_is_long_term_and_losses_are_not_taxed(tmp_path):
    s = _store(tmp_path, [
        _lot("VTI", "indexed_equity", 45, 90.0),
        _lot("ACME", "active", 6, 60.0, d(-365)),    # loss lot, long-term by boundary
        _lot("ACME", "active", 12, 20.0, d(-364)),   # short-term
        _lot("BND", "bonds", 40, 75.0), _lot("CASH", "cash", 1600.0)])
    a = build_actions(s, _market({"VTI": 100.0, "ACME": 50.0, "BND": 75.0}), Targets(), TODAY, review=False)
    # 8 shares: 6 from the d(-365) loss lot (no tax), 2 from the d(-364) lot at 24%
    assert a.sells[0].est_tax == pytest.approx((50 - 20) * 2 * 0.24)  # 14.4


def test_zero_tax_trim_has_no_tax_note(tmp_path):
    s = _store(tmp_path, [
        _lot("VTI", "indexed_equity", 45, 90.0), _lot("ACME", "active", 18, 60.0, d(-400)),
        _lot("BND", "bonds", 40, 75.0), _lot("CASH", "cash", 1600.0)])
    a = build_actions(s, _market({"VTI": 100.0, "ACME": 50.0, "BND": 75.0}), Targets(), TODAY, review=False)
    assert a.sells[0].est_tax == 0.0
    assert TAX_NOTE not in a.notes


# ---------------------------------------------------------------- picks
def _on_target_holdings():
    # total 3000: VTI 4.5*300=1350, HELD 3*50=150, BND 12*75=900, CASH 600 -> 50/30/20, active 5%
    return [_lot("VTI", "indexed_equity", 4.5, 300.0), _lot("HELD", "active", 3, 40.0),
            _lot("BND", "bonds", 12, 75.0), _lot("CASH", "cash", 600.0)]


@pytest.fixture
def store_on_target_with_passed_watch_item(tmp_path):
    return _store(tmp_path, _on_target_holdings(), [_held(), _passed("ACME", 0.31)])


@pytest.fixture
def market_on_target():
    return _market({"VTI": 300.0, "BND": 75.0, "HELD": 50.0}, candidates=("ACME",), held_hist=("HELD",))


def test_review_run_funds_pick_by_selling_index(store_on_target_with_passed_watch_item, market_on_target):
    a = build_actions(store_on_target_with_passed_watch_item, market_on_target, Targets(), TODAY, review=True)
    dollars = min(0.05 * 3000, 0.30 * 3000 - 150)  # = 150
    assert dollars == 150
    assert len(a.gated) == 1 and a.gated[0].passed, a.notes
    assert _pairs(a.proposals) == [("SELL", "VTI", 150.0), ("BUY", "ACME", 150.0)]
    assert a.proposals[0].reason == "fund ACME"
    assert a.proposals[1].reason == f"{a.gated[0].upside:.0%} upside, gate passed"
    assert a.sells == [] and a.ledger_notes == []


def test_weekly_run_never_adds_new_names(store_on_target_with_passed_watch_item, market_on_target):
    a = build_actions(store_on_target_with_passed_watch_item, market_on_target, Targets(), TODAY, review=False)
    assert a.gated == []
    assert all(p.ticker != "ACME" for p in a.proposals)
    assert a.proposals == []


def test_cap_three_pairs(tmp_path):
    names = ("C1", "C2", "C3", "C4", "C5")
    upsides = (0.30, 0.40, 0.26, 0.35, 0.28)  # order by upside: C2, C4, C1, C5, C3
    s = _store(tmp_path, _on_target_holdings(), [_held()] + [_passed(t, u) for t, u in zip(names, upsides)])
    m = _market({"VTI": 300.0, "BND": 75.0, "HELD": 50.0}, candidates=names, held_hist=("HELD",))
    a = build_actions(s, m, Targets(), TODAY, review=True)
    assert [g.passed for g in a.gated] == [True, True, True], a.notes
    # each pick: min(150, 900 - active); active 150 -> 300 -> 450 -> 600, so 150 each
    assert _pairs(a.proposals) == [
        ("SELL", "VTI", 150.0), ("BUY", "C2", 150.0),
        ("SELL", "VTI", 150.0), ("BUY", "C4", 150.0),
        ("SELL", "VTI", 150.0), ("BUY", "C1", 150.0)]
    assert any("2 more passed watchlist item(s) deferred" in n for n in a.notes)


def test_active_ceiling_reached_note(store_on_target_with_passed_watch_item, market_on_target):
    targets = Targets(active_ceiling=0.05)  # HELD already 150 / 3000 = 5% -> min(150, 150 - 150) = 0
    a = build_actions(store_on_target_with_passed_watch_item, market_on_target, targets, TODAY, review=True)
    assert a.gated == [] and a.proposals == []
    assert "active ceiling reached" in a.notes


def test_failed_gate_becomes_note(store_on_target_with_passed_watch_item, market_on_target):
    market_on_target.data["ACME"]["quote"] = _q("ACME", 33.0, 32.0, 33.0, "EQUITY")  # spread ~3%
    a = build_actions(store_on_target_with_passed_watch_item, market_on_target, Targets(), TODAY, review=True)
    assert len(a.gated) == 1 and not a.gated[0].passed
    assert a.proposals == []
    assert f"ACME failed gate: " + "; ".join(a.gated[0].reasons) in a.notes


def test_gate_exception_becomes_note(store_on_target_with_passed_watch_item, market_on_target, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("provider down")
    monkeypatch.setattr(actions_mod, "gate", boom)
    a = build_actions(store_on_target_with_passed_watch_item, market_on_target, Targets(), TODAY, review=True)
    assert a.gated == [] and a.proposals == []
    assert any(n.startswith("ACME: gate error") and "provider down" in n for n in a.notes)


def test_peers_exception_becomes_note(store_on_target_with_passed_watch_item, market_on_target, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("screen down")
    monkeypatch.setattr(actions_mod, "peers_for", boom)
    a = build_actions(store_on_target_with_passed_watch_item, market_on_target, Targets(), TODAY, review=True)
    assert a.gated == [] and a.proposals == []
    assert any(n.startswith("ACME: peers error") for n in a.notes)


# ---------------------------------------------------------------- thesis sells
def _thesis_holdings():
    # total 3000: VTI 5*300=1500, HELD 2*50=100, BND 12*75=900, CASH 500 (stocks 53.3%, cash 16.7%)
    return [_lot("VTI", "indexed_equity", 5, 300.0), _lot("HELD", "active", 2, 40.0, d(-30)),
            _lot("BND", "bonds", 12, 75.0), _lot("CASH", "cash", 500.0)]


def test_thesis_sell_on_fair_value_reached(tmp_path):
    s = _store(tmp_path, _thesis_holdings(), [_held(fair=50.0, upside=0.1)])
    a = build_actions(s, _market({"VTI": 300.0, "BND": 75.0, "HELD": 50.0}), Targets(), TODAY, review=False)
    assert len(a.sells) == 1
    sell = a.sells[0]
    assert (sell.action, sell.ticker, sell.dollars, sell.reason) == ("SELL", "HELD", 100.0, "fair value reached")
    assert sell.est_tax == pytest.approx((50 - 40) * 2 * 0.24)  # 4.8, short-term lot
    # after the sell: stocks 1500/3000 = 50%, cash 600/3000 = 20% -> no rebalance
    assert a.proposals == []


def test_thesis_warning_then_sell_on_second_negative_review(tmp_path):
    held = _held(fair=55.0, upside=0.10)
    m = _market({"VTI": 300.0, "BND": 75.0, "HELD": 50.0})
    _down_revisions(m, "HELD")

    first = build_actions(_store(tmp_path / "a", _thesis_holdings(), [held]), m, Targets(), TODAY, review=True)
    assert first.sells == []
    expected = LedgerRow(id=f"tw-{TODAY}-HELD", date=TODAY, action="NOTE", ticker="HELD", amount=0,
                         rationale="thesis_warning:HELD", ips_check="PASS", response="applied")
    assert first.ledger_notes == [expected]
    assert any("HELD" in n and "thesis warning" in n for n in first.notes)

    second = build_actions(_store(tmp_path / "b", _thesis_holdings(), [held], ledger=[expected]), m,
                           Targets(), d(7), review=True)
    assert _pairs(second.sells) == [("SELL", "HELD", 100.0)]
    assert second.sells[0].reason == "thesis broken: two reviews of negative revisions"
    assert second.ledger_notes == []


def test_negative_revisions_ignored_on_weekly_run(tmp_path):
    m = _market({"VTI": 300.0, "BND": 75.0, "HELD": 50.0})
    _down_revisions(m, "HELD")
    s = _store(tmp_path, _thesis_holdings(), [_held(fair=55.0, upside=0.10)])
    a = build_actions(s, m, Targets(), TODAY, review=False)
    assert a.sells == [] and a.ledger_notes == []


def test_revision_exception_becomes_note(tmp_path):
    m = _market({"VTI": 300.0, "BND": 75.0, "HELD": 50.0})  # no eps_trend -> MarketUnavailable
    s = _store(tmp_path, _thesis_holdings(), [_held(fair=55.0, upside=0.10)])
    a = build_actions(s, m, Targets(), TODAY, review=True)
    assert a.sells == [] and a.ledger_notes == []
    assert any(n.startswith("HELD: revisions unavailable") for n in a.notes)


# ---------------------------------------------------------------- robustness
def test_unpriced_holding_skips_trims_and_picks_but_still_reports(tmp_path):
    s = _store(tmp_path, [_lot("VTI", "indexed_equity", 52, 90.0), _lot("ACME", "active", 16, 40.0, d(-400)),
                          _lot("GONE", "active", 5, 10.0), _lot("BND", "bonds", 40, 75.0),
                          _lot("CASH", "cash", 1000.0)], [_passed("ZZZ", 0.4)])
    a = build_actions(s, _market({"VTI": 100.0, "ACME": 50.0, "BND": 75.0}), Targets(), TODAY, review=True)
    assert a.unpriced == ["GONE"]
    assert a.sells == [] and a.proposals == [] and a.gated == []
    assert any("GONE" in n and "skipped" in n for n in a.notes)


def test_missing_index_price_on_rebalance_becomes_note(store_active_name_at_8pct_and_stocks_60pct):
    m = _market({"VTI": 100.0, "ACME": 50.0, "BND": 75.0})
    original = m.quote
    calls = {"n": 0}

    def flaky(ticker):  # VTI prices once (for valuation), then becomes unavailable
        if ticker == "VTI":
            calls["n"] += 1
            if calls["n"] > 1:
                raise actions_mod.MarketUnavailable("VTI")
        return original(ticker)
    m.quote = flaky
    a = build_actions(store_active_name_at_8pct_and_stocks_60pct, m, Targets(), TODAY, review=False)
    assert a.sells[0].ticker == "ACME"
    assert a.proposals == []
    assert any("VTI" in n and "price" in n for n in a.notes)


def test_empty_holdings_returns_empty_action_set(tmp_path):
    a = build_actions(_store(tmp_path, []), _market({"VTI": 300.0, "BND": 75.0}), Targets(), TODAY, review=True)
    assert a.proposals == [] and a.sells == [] and a.total == 0
    assert a.notes


def test_build_actions_never_writes_to_store(tmp_path):
    held = _held(fair=55.0, upside=0.10)
    s = _store(tmp_path, _thesis_holdings(), [held, _passed("ACME", 0.31)])
    m = _market({"VTI": 300.0, "BND": 75.0, "HELD": 50.0}, candidates=("ACME",), held_hist=("HELD",))
    _down_revisions(m, "HELD")
    before = _snapshot(tmp_path)
    a = build_actions(s, m, Targets(), TODAY, review=True)
    assert a.ledger_notes  # a warning was produced ...
    assert _snapshot(tmp_path) == before  # ... but nothing was persisted


def _warning(when: date) -> LedgerRow:
    return LedgerRow(id=f"tw-{when}-HELD", date=when, action="NOTE", ticker="HELD", amount=0,
                     rationale="thesis_warning:HELD", ips_check="PASS", response="applied")


@pytest.mark.parametrize("age_days, sells", [(130, False), (121, False), (120, True), (40, True)])
def test_thesis_warning_counts_only_within_120_days(tmp_path, age_days, sells):
    m = _market({"VTI": 300.0, "BND": 75.0, "HELD": 50.0})
    _down_revisions(m, "HELD")
    s = _store(tmp_path, _thesis_holdings(), [_held(fair=55.0, upside=0.10)], ledger=[_warning(d(-age_days))])
    a = build_actions(s, m, Targets(), TODAY, review=True)
    if sells:
        assert _pairs(a.sells) == [("SELL", "HELD", 100.0)]
        assert a.ledger_notes == []
    else:  # stale warning ignored: a fresh one is emitted instead of a sell
        assert a.sells == []
        assert [r.id for r in a.ledger_notes] == [f"tw-{TODAY}-HELD"]



def test_same_day_warning_neither_sells_nor_duplicates(tmp_path):
    m = _market({"VTI": 300.0, "BND": 75.0, "HELD": 50.0})
    _down_revisions(m, "HELD")
    s = _store(tmp_path, _thesis_holdings(), [_held(fair=55.0, upside=0.10)], ledger=[_warning(TODAY)])
    a = build_actions(s, m, Targets(), TODAY, review=True)  # a re-run on the same day
    assert a.sells == []
    assert a.ledger_notes == []
    assert "thesis warning: HELD already recorded today" in a.notes  # the revision check did run


def test_down_revisions_with_upside_at_min_upside_is_not_a_warning(tmp_path):
    m = _market({"VTI": 300.0, "BND": 75.0, "HELD": 50.0})
    _down_revisions(m, "HELD")
    # upside exactly min_upside (0.25): margin of safety intact -> no thesis check
    s = _store(tmp_path, _thesis_holdings(), [_held(fair=62.5, upside=0.25)], ledger=[_warning(d(-40))])
    a = build_actions(s, m, Targets(), TODAY, review=True)
    assert a.sells == [] and a.ledger_notes == []


def test_name_over_trim_threshold_and_at_fair_value_sold_once(tmp_path):
    # ACME 16 @50 = 800 of 10_000 (8% > 7.5%) and price 50 >= fair value 45
    s = _store(tmp_path, [_lot("VTI", "indexed_equity", 52, 90.0), _lot("ACME", "active", 16, 40.0, d(-400)),
                          _lot("BND", "bonds", 40, 75.0), _lot("CASH", "cash", 1000.0)],
               [_held("ACME", fair=45.0, upside=0.3)])
    a = build_actions(s, _market({"VTI": 100.0, "ACME": 50.0, "BND": 75.0}), Targets(), TODAY, review=False)
    assert _pairs(a.sells) == [("SELL", "ACME", 800.0)]
    assert a.sells[0].reason == "fair value reached"
    assert a.sells[0].est_tax == pytest.approx((50 - 40) * 16 * 0.15)  # 24.0


def test_review_run_trims_at_6_5_pct(tmp_path):
    # ACME 13 @50 = 650 of 10_000 = 6.5%: below the weekly 7.5% threshold, above the review 6%
    holdings = [_lot("VTI", "indexed_equity", 50, 90.0), _lot("ACME", "active", 13, 40.0, d(-400)),
                _lot("BND", "bonds", 40, 75.0), _lot("CASH", "cash", 1350.0)]
    m = _market({"VTI": 100.0, "ACME": 50.0, "BND": 75.0})
    assert build_actions(_store(tmp_path / "w", holdings), m, Targets(), TODAY, review=False).sells == []
    a = build_actions(_store(tmp_path / "r", holdings), m, Targets(), TODAY, review=True)
    assert _pairs(a.sells) == [("SELL", "ACME", 150.0)]  # 650 - 0.05 * 10_000
    assert a.sells[0].reason == "trim 6.5% → 5% cap"
    assert a.sells[0].est_tax == pytest.approx((50 - 40) * 3 * 0.15)  # 4.5


# ---------------------------------------------------------------- indexed >= active invariant
def _sleeve_totals_after(values: dict[str, float], proposals, sleeve_of: dict[str, str]) -> dict[str, float]:
    v = dict(values)
    for p in proposals:
        sign = 1 if p.action == "BUY" else -1
        v[p.ticker] = v.get(p.ticker, 0.0) + sign * p.dollars
        v["CASH"] = v.get("CASH", 0.0) - sign * p.dollars
    out: dict[str, float] = {}
    for t, x in v.items():
        out[sleeve_of.get(t, "active")] = out.get(sleeve_of.get(t, "active"), 0.0) + x
    return out


def test_pick_sized_so_indexed_never_trails_active(tmp_path):
    # $10,000: VTI 2,850 / active 2,150 (4 x 500 + 150) / BND 3,000 / cash 2,000 -> 50/30/20, no rebalance
    actives = ["A1", "A2", "A3", "A4", "A5"]
    holdings = ([_lot("VTI", "indexed_equity", 28.5, 90.0)]
                + [_lot(t, "active", 10, 40.0) for t in actives[:4]] + [_lot("A5", "active", 3, 40.0)]
                + [_lot("BND", "bonds", 40, 75.0), _lot("CASH", "cash", 2000.0)])
    s = _store(tmp_path, holdings, [_passed("ACME", 0.31)])
    m = _market({"VTI": 100.0, "BND": 75.0, **{t: 50.0 for t in actives}}, candidates=("ACME",), held_hist=tuple(actives))
    a = build_actions(s, m, Targets(), TODAY, review=True)
    # min(cap 500, ceiling 3000 - 2150 = 850, (indexed 2850 - active 2150) / 2 = 350) = 350
    dollars = min(0.05 * 10_000, 0.30 * 10_000 - 2150, (2850 - 2150) / 2)
    assert dollars == 350
    assert a.gated and a.gated[0].passed, a.notes
    assert _pairs(a.proposals) == [("SELL", "VTI", 350.0), ("BUY", "ACME", 350.0)]
    assert "pick sized by indexed ≥ active rule (effective active ceiling 25% at 50% stocks)" in a.notes
    sleeves = _sleeve_totals_after(a.values, a.proposals, {"VTI": "indexed_equity", "BND": "bonds", "CASH": "cash"})
    assert sleeves["indexed_equity"] == sleeves["active"] == 2500


def test_no_room_for_picks_when_indexed_equals_active(tmp_path):
    actives = ["A1", "A2", "A3", "A4", "A5"]
    # VTI 2,500 / active 2,500 (5 x 500) / BND 3,000 / cash 2,000
    holdings = ([_lot("VTI", "indexed_equity", 25, 90.0)] + [_lot(t, "active", 10, 40.0) for t in actives]
                + [_lot("BND", "bonds", 40, 75.0), _lot("CASH", "cash", 2000.0)])
    s = _store(tmp_path, holdings, [_passed("ACME", 0.31)])
    m = _market({"VTI": 100.0, "BND": 75.0, **{t: 50.0 for t in actives}}, candidates=("ACME",), held_hist=tuple(actives))
    a = build_actions(s, m, Targets(), TODAY, review=True)
    assert a.gated == [] and a.proposals == []
    assert "indexed ≥ active binds; no room for picks" in a.notes


def test_accepted_pick_on_standard_fixture_keeps_indexed_ge_active(store_on_target_with_passed_watch_item,
                                                                  market_on_target):
    a = build_actions(store_on_target_with_passed_watch_item, market_on_target, Targets(), TODAY, review=True)
    assert a.gated[0].passed
    sleeves = _sleeve_totals_after(a.values, a.proposals,
                                   {"VTI": "indexed_equity", "BND": "bonds", "CASH": "cash"})
    assert sleeves["indexed_equity"] == 1350 - 150 and sleeves["active"] == 150 + 150
    assert sleeves["indexed_equity"] >= sleeves["active"]
    assert not any("indexed ≥ active" in n for n in a.notes)  # (1350-150)/2 = 600 > 150: cap binds


def test_watch_override_replaces_stored_watchlist(tmp_path, market_on_target):
    stored = WatchItem(ticker="ACME", thesis="t", last_valued=None, fair_value=None, upside=None, status="candidate")
    s = _store(tmp_path, _on_target_holdings(), [_held(), stored])
    assert build_actions(s, market_on_target, Targets(), TODAY, review=True).gated == []
    a = build_actions(s, market_on_target, Targets(), TODAY, review=True, watch=[_held(), _passed("ACME", 0.31)])
    assert _pairs(a.proposals) == [("SELL", "VTI", 150.0), ("BUY", "ACME", 150.0)]
    assert s.load_watchlist() == [_held(), stored]  # the override is never written back


# ---------------------------------------------------------------- C1: fund instruments are IPS-checked
def test_first_run_refuses_index_etf_that_fails_instrument_check(store_with_cash_3000):
    m = _market({"VTI": 300.0, "BND": 75.0})
    m.data["TQQQ"] = {"quote": _q("TQQQ", 50.0, expense_ratio=0.0095),
                      "info": {"longName": "ProShares UltraPro QQQ"}}
    a = build_actions(store_with_cash_3000, m, Targets(index_etf="TQQQ"), TODAY, review=False)
    assert [(p.action, p.ticker) for p in a.proposals] == [("BUY", "BND")]
    assert ("TQQQ fails IPS instrument check: expense ratio 0.95% >= 0.90% cap; "
            "denied keyword 'ultra' (leveraged/inverse product) in name") in a.notes


def test_first_run_instrument_data_unavailable_blocks_the_buy(store_with_cash_3000):
    m = _market({"VTI": 300.0, "BND": 75.0})
    del m.data["BND"]["info"]
    a = build_actions(store_with_cash_3000, m, Targets(), TODAY, review=False)
    assert [(p.action, p.ticker) for p in a.proposals] == [("BUY", "VTI")]
    assert "BND fails IPS instrument check: instrument data unavailable" in a.notes


def test_rebalance_buy_of_failing_index_etf_is_dropped(tmp_path):
    # total 10_000: VTI 3000 / BND 3000 / CASH 4000 -> stocks 30%: rebalance would BUY VTI 2000
    s = _store(tmp_path, [_lot("VTI", "indexed_equity", 30, 90.0), _lot("BND", "bonds", 40, 75.0),
                          _lot("CASH", "cash", 4000.0)])
    m = _market({"VTI": 100.0, "BND": 75.0})
    m.data["VTI"]["quote"] = _q("VTI", 100.0, expense_ratio=None, quote_type="MUTUALFUND")
    a = build_actions(s, m, Targets(), TODAY, review=False)
    assert all(p.ticker != "VTI" for p in a.proposals)
    assert "VTI fails IPS instrument check: expense ratio unavailable" in a.notes
    assert a.compliance.fund_flags == ["VTI: expense ratio unavailable"]


def test_compliance_in_action_set_carries_fund_flags(tmp_path):
    s = _store(tmp_path, [_lot("VTI", "indexed_equity", 50, 90.0), _lot("BND", "bonds", 40, 75.0),
                          _lot("CASH", "cash", 2000.0)])
    m = _market({"VTI": 100.0, "BND": 75.0})
    m.data["BND"]["quote"] = _q("BND", 75.0, expense_ratio=0.0095)
    a = build_actions(s, m, Targets(), TODAY, review=False)
    assert a.compliance.fund_flags == ["BND: expense ratio 0.95% >= 0.90% cap"]


# ---------------------------------------------------------------- I4: unresolved IPS breach note
BREACH_NOTE = "IPS breach unresolved (active ceiling / indexed<active): decide manually — candidates: "


def test_unresolved_active_ceiling_breach_note_lists_names_by_weight(tmp_path):
    # total 7_300: VTI 1400 / A1..A8 450 down to 275 (2900, 39.7% > 30%; each < 7.5%, so no trim) / BND 3000
    names = [f"A{i}" for i in range(1, 9)]
    holdings = ([_lot("VTI", "indexed_equity", 14, 90.0)]
                + [_lot(t, "active", 9 - i * 0.5, 40.0) for i, t in enumerate(names)]
                + [_lot("BND", "bonds", 40, 75.0), _lot("CASH", "cash", 0.0)])
    s = _store(tmp_path, holdings)
    m = _market({"VTI": 100.0, "BND": 75.0, **{t: 50.0 for t in names}})
    a = build_actions(s, m, Targets(), TODAY, review=False)
    assert a.compliance.active_ceiling_flag and a.compliance.indexed_lt_active
    assert a.sells == []
    assert BREACH_NOTE + ", ".join(names) in a.notes


def test_breach_resolved_by_thesis_sell_has_no_note(tmp_path):
    # VTI 1400, HELD 1600 (active > indexed); HELD sold at fair value -> resolved
    s = _store(tmp_path, [_lot("VTI", "indexed_equity", 14, 90.0), _lot("HELD", "active", 32, 40.0),
                          _lot("BND", "bonds", 40, 75.0), _lot("CASH", "cash", 4000.0)],
               [_held(fair=50.0, upside=0.0)])
    a = build_actions(s, _market({"VTI": 100.0, "BND": 75.0, "HELD": 50.0}), Targets(), TODAY, review=False)
    assert a.compliance.indexed_lt_active
    assert [p.ticker for p in a.sells] == ["HELD"]
    assert not any(n.startswith("IPS breach unresolved") for n in a.notes)


def test_no_breach_no_note(store_on_target_with_passed_watch_item, market_on_target):
    a = build_actions(store_on_target_with_passed_watch_item, market_on_target, Targets(), TODAY, review=False)
    assert not any(n.startswith("IPS breach unresolved") for n in a.notes)


# ---------------------------------------------------------------- I8: tax on ETF sells
def test_rebalance_sells_of_index_and_bond_etf_carry_est_tax(tmp_path):
    # total 10_000: VTI 6000 (LT lot @80) / BND 4000 (ST lot @70) / CASH 0 -> sell VTI 1000, BND 13 sh
    s = _store(tmp_path, [_lot("VTI", "indexed_equity", 60, 80.0, d(-400)), _lot("BND", "bonds", 53.333333, 70.0),
                          _lot("CASH", "cash", 0.0)])
    a = build_actions(s, _market({"VTI": 100.0, "BND": 75.0}), Targets(), TODAY, review=False)
    by = {p.ticker: p for p in a.proposals}
    assert by["VTI"].action == "SELL" and by["VTI"].dollars == pytest.approx(1000.0)
    assert by["VTI"].est_tax == pytest.approx(10 * (100 - 80) * 0.15)  # 30.0, long-term
    assert by["BND"].action == "SELL" and by["BND"].shares == 13
    assert by["BND"].est_tax == pytest.approx(13 * (75 - 70) * 0.24)  # 15.6, short-term
    assert TAX_NOTE in a.notes


def test_pick_funding_sell_carries_est_tax(store_on_target_with_passed_watch_item, market_on_target):
    # VTI lot: 4.5 sh @300, d(-7); price 300 -> no gain, tax 0
    a = build_actions(store_on_target_with_passed_watch_item, market_on_target, Targets(), TODAY, review=True)
    fund = a.proposals[0]
    assert (fund.action, fund.ticker) == ("SELL", "VTI") and fund.est_tax == 0.0
    assert a.proposals[1].est_tax is None  # buys carry no tax


def test_rebalance_buy_has_no_est_tax(tmp_path):
    s = _store(tmp_path, [_lot("VTI", "indexed_equity", 30, 90.0), _lot("BND", "bonds", 40, 75.0),
                          _lot("CASH", "cash", 4000.0)])
    a = build_actions(s, _market({"VTI": 100.0, "BND": 75.0}), Targets(), TODAY, review=False)
    assert [(p.action, p.ticker, p.est_tax) for p in a.proposals] == [("BUY", "VTI", None)]


# ---------------------------------------------------------------- I12: netting and the BUY-line cap
def _cash_heavy_store(tmp_path, names):
    # total 10_000: VTI 3000 / HELD 150 / BND 1950 / CASH 4900 -> stocks 31.5%: BUY VTI and BUY BND
    return _store(tmp_path, [_lot("VTI", "indexed_equity", 30, 90.0), _lot("HELD", "active", 3, 40.0),
                             _lot("BND", "bonds", 26, 75.0), _lot("CASH", "cash", 4900.0)],
                  [_held()] + [_passed(t, u) for t, u in names])


def test_buy_lines_capped_at_three_and_extra_picks_deferred(tmp_path):
    names = (("C1", 0.40), ("C2", 0.35), ("C3", 0.30))
    m = _market({"VTI": 100.0, "BND": 75.0, "HELD": 50.0}, candidates=tuple(t for t, _ in names),
                held_hist=("HELD",))
    a = build_actions(_cash_heavy_store(tmp_path, names), m, Targets(), TODAY, review=True)
    buys = [p.ticker for p in a.proposals if p.action == "BUY"]
    assert buys == ["VTI", "BND", "C1"], a.notes
    assert "deferred: C2" in a.notes and "deferred: C3" in a.notes
    assert not any(p.reason in ("fund C2", "fund C3") for p in a.proposals)
    assert not any("fund C1" == p.reason for p in a.proposals)  # netted into the VTI line


def test_same_ticker_buy_and_sell_are_netted(tmp_path):
    names = (("C1", 0.40),)
    m = _market({"VTI": 100.0, "BND": 75.0, "HELD": 50.0}, candidates=("C1",), held_hist=("HELD",))
    a = build_actions(_cash_heavy_store(tmp_path, names), m, Targets(), TODAY, review=True)
    vti = [p for p in a.proposals if p.ticker == "VTI"]
    assert len(vti) == 1, a.proposals
    (line,) = vti
    c1 = next(p for p in a.proposals if p.ticker == "C1")
    # rebalance BUY VTI (0.50 - 0.315) * 10_000 = 1850, minus the C1 funding SELL
    assert line.action == "BUY" and line.dollars == pytest.approx(1850.0 - c1.dollars)
    assert line.reason == "net: rebalance stocks 31.5% → 50%; fund C1"
    assert line.est_tax is None


def test_netting_unit_drops_sub_dollar_and_flips_to_sell():
    from fundkit.portfolio import Proposal
    P = lambda a, t, d, r: Proposal(action=a, ticker=t, dollars=d, reason=r)  # noqa: E731
    out = actions_mod._net([P("BUY", "VTI", 100.0, "a"), P("SELL", "VTI", 99.5, "b"), P("BUY", "X", 5.0, "c")],
                           {}, {}, TODAY)
    assert _pairs(out) == [("BUY", "X", 5.0)]
    lots = {"VTI": [_lot("VTI", "indexed_equity", 10, 80.0, d(-400))]}
    out = actions_mod._net([P("BUY", "VTI", 100.0, "a"), P("SELL", "VTI", 300.0, "b")], lots, {"VTI": 100.0}, TODAY)
    assert _pairs(out) == [("SELL", "VTI", 200.0)]
    assert out[0].reason == "net: a; b" and out[0].est_tax == pytest.approx(2 * 20 * 0.15)
