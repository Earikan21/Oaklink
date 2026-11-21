from datetime import date

import numpy as np
import pandas as pd
import pytest

import fundkit.run as run_mod
from conftest import FakeMarket, _acme_market, d, make_history
from fundkit.actions import ActionSet
from fundkit.ips import ComplianceReport
from fundkit.memo import MemoContext
from fundkit.models import Holding, LedgerRow, Quote, Snapshot, WatchItem
from fundkit.run import RunResult, weekly_run
from fundkit.store import Store

TODAY = d(0)  # 2026-11-29


# ---------------------------------------------------------------- helpers
def _q(ticker, price, quote_type="ETF", nav=None, bid=None, ask=None) -> Quote:
    return Quote(ticker=ticker, price=price, bid=bid, ask=ask, expense_ratio=None, nav=nav,
                 quote_type=quote_type, avg_volume=None)


def _lot(ticker, sleeve, shares, cost=1.0, when=d(-7)) -> Holding:
    return Holding(ticker=ticker, sleeve=sleeve, shares=shares, cost_basis=cost, lot_date=when)


def _snap(when: date, total=3000.0) -> Snapshot:
    return Snapshot(date=when, total=total, indexed_equity=0.0, active=0.0, bonds=0.0, cash=total,
                    bench_total=total, active_vs_index=None, turnover_qtd=0.0)


def _base_data(vti_nav=None) -> dict:
    """VTI 300 / BND 75 with flat 300-day histories (enough for the benchmark)."""
    return {
        "VTI": {"quote": _q("VTI", 300.0, nav=vti_nav), "history": make_history(300.0, 300)},
        "BND": {"quote": _q("BND", 75.0), "history": make_history(75.0, 300)},
    }


def _files(root) -> dict:
    return {p.name: p.read_bytes() for p in sorted(root.iterdir()) if p.is_file()}


def _earnings(rows) -> pd.DataFrame:
    """rows: (ET timestamp, est, actual); NaN actual = not yet reported."""
    idx = pd.DatetimeIndex([pd.Timestamp(r[0], tz="America/New_York") for r in rows], name="Earnings Date")
    return pd.DataFrame({"EPS Estimate": [r[1] for r in rows], "Reported EPS": [r[2] for r in rows]}, index=idx)


def _store(tmp_path, holdings, watch=(), ledger=(), perf=()) -> Store:
    s = Store(tmp_path)
    s.init()
    s.save_holdings(list(holdings))
    s.save_watchlist(list(watch))
    s.save_ledger(list(ledger))
    for p in perf:
        s.append_performance(p)
    return s


@pytest.fixture
def fake_market_basic():
    return FakeMarket(_base_data())


@pytest.fixture
def store_with_5_snapshots(store_with_cash_3000):
    for i in range(5):
        store_with_cash_3000.append_performance(_snap(d(-35 + 7 * i)))
    return store_with_cash_3000


@pytest.fixture
def store_with_4_snapshots(store_with_cash_3000):
    for i in range(4):
        store_with_cash_3000.append_performance(_snap(d(-28 + 7 * i)))
    return store_with_cash_3000


# ---------------------------------------------------------------- review detection
def test_sixth_run_is_review(store_with_5_snapshots, fake_market_basic):
    r = weekly_run(store_with_5_snapshots, fake_market_basic, TODAY, dry_run=True)
    assert r.review is True
    assert "REVIEW RUN" in r.memo_md


def test_fifth_run_is_not_review(store_with_4_snapshots, fake_market_basic):
    r = weekly_run(store_with_4_snapshots, fake_market_basic, TODAY, dry_run=True)
    assert r.review is False
    assert "REVIEW RUN" not in r.memo_md


def test_first_run_is_not_review(store_with_cash_3000, fake_market_basic):
    assert weekly_run(store_with_cash_3000, fake_market_basic, TODAY, dry_run=True).review is False


@pytest.mark.parametrize("force", [True, False])
def test_force_review_overrides_cadence(store_with_4_snapshots, fake_market_basic, force):
    assert weekly_run(store_with_4_snapshots, fake_market_basic, TODAY, dry_run=True, force_review=force).review is force


def test_inception_is_first_performance_row(store_with_5_snapshots, fake_market_basic, monkeypatch):
    seen = {}
    real = run_mod.snapshot

    def spy(store, market, targets, today, inception, inception_value):
        seen["args"] = (inception, inception_value)
        return real(store, market, targets, today, inception, inception_value)

    monkeypatch.setattr(run_mod, "snapshot", spy)
    weekly_run(store_with_5_snapshots, fake_market_basic, TODAY, dry_run=True)
    assert seen["args"] == (d(-35), 3000.0)


def test_inception_defaults_to_today_and_total(store_with_cash_3000, fake_market_basic, monkeypatch):
    seen = {}
    real = run_mod.snapshot

    def spy(store, market, targets, today, inception, inception_value):
        seen["args"] = (inception, inception_value)
        return real(store, market, targets, today, inception, inception_value)

    monkeypatch.setattr(run_mod, "snapshot", spy)
    weekly_run(store_with_cash_3000, fake_market_basic, TODAY, dry_run=True)
    assert seen["args"] == (TODAY, 3000.0)


# ---------------------------------------------------------------- dry run vs live
def test_dry_run_writes_nothing(store_with_cash_3000, fake_market_basic):
    before = _files(store_with_cash_3000.root)
    r = weekly_run(store_with_cash_3000, fake_market_basic, TODAY, dry_run=True)
    assert r.written is False
    assert store_with_cash_3000.load_ledger() == []
    assert store_with_cash_3000.load_performance() == []
    assert _files(store_with_cash_3000.root) == before  # reconcile had nothing to change
    assert [(p.action, p.ticker) for p in r.actions.proposals] == [("BUY", "VTI"), ("BUY", "BND")]


def test_dry_run_still_reconciles(tmp_path, fake_market_basic):
    """Documented exception: reconcile records Emmet's own fills even on a dry run."""
    fill = LedgerRow(id="x1", date=d(-6), action="BUY", ticker="VTI", amount=1500, rationale="initial allocation",
                     ips_check="PASS", response="done", fill_shares=5, fill_price=300.0)
    s = _store(tmp_path, [_lot("CASH", "cash", 3000.0)], ledger=[fill])
    r = weekly_run(s, fake_market_basic, TODAY, dry_run=True)
    assert r.written is False
    assert [x.response for x in s.load_ledger()] == ["applied"]
    assert {h.ticker for h in s.load_holdings()} == {"VTI", "CASH"}
    assert s.load_performance() == []
    assert r.context.reconcile.applied == ["x1"]


def test_live_run_appends_pending_ledger_rows_and_snapshot(store_with_cash_3000, fake_market_basic):
    r = weekly_run(store_with_cash_3000, fake_market_basic, TODAY, dry_run=False)
    assert r.written is True
    ledger = store_with_cash_3000.load_ledger()
    assert len(ledger) == 2
    assert all(x.response == "pending" for x in ledger)
    perf = store_with_cash_3000.load_performance()
    assert len(perf) == 1 and perf[0] == r.snapshot and perf[0].date == TODAY


def test_pending_rows_have_id_format_and_fields(store_with_cash_3000, fake_market_basic):
    weekly_run(store_with_cash_3000, fake_market_basic, TODAY, dry_run=False)
    vti, bnd = store_with_cash_3000.load_ledger()
    assert vti.id == "20261129-01-BUY-VTI" and bnd.id == "20261129-02-BUY-BND"
    assert (vti.date, vti.action, vti.ticker, vti.amount, vti.rationale) == (
        TODAY, "BUY", "VTI", 1500.0, "initial allocation")
    assert (bnd.amount, bnd.ips_check, bnd.fill_shares, bnd.fill_price, bnd.est_tax) == (900.0, "PASS", None, None, None)


@pytest.fixture
def store_trim(tmp_path):
    # total 10_000: VTI 5200, ACME 800 (8% -> trim, long-term lot @40), BND 3000, CASH 1000
    return _store(tmp_path, [
        _lot("VTI", "indexed_equity", 52, 90.0), _lot("ACME", "active", 16, 40.0, d(-400)),
        _lot("BND", "bonds", 40, 75.0), _lot("CASH", "cash", 1000.0)])


@pytest.fixture
def market_trim():
    data = {
        "VTI": {"quote": _q("VTI", 100.0), "history": make_history(100.0, 300)},
        "BND": {"quote": _q("BND", 75.0), "history": make_history(75.0, 300)},
        "ACME": {"quote": _q("ACME", 50.0, "EQUITY")},
    }
    return FakeMarket(data)


def test_sells_listed_first_and_est_tax_carried(store_trim, market_trim):
    weekly_run(store_trim, market_trim, TODAY, dry_run=False)
    rows = store_trim.load_ledger()
    assert [x.id for x in rows] == ["20261129-01-SELL-ACME", "20261129-02-SELL-VTI"]
    assert rows[0].amount == 300.0 and rows[0].est_tax == pytest.approx(9.0)
    assert rows[0].rationale == "trim 8.0% → 5% cap"
    assert rows[1].est_tax is None


def test_two_live_runs_same_day_do_not_duplicate(store_with_5_snapshots, fake_market_basic):
    r1 = weekly_run(store_with_5_snapshots, fake_market_basic, TODAY, dry_run=False)
    r2 = weekly_run(store_with_5_snapshots, fake_market_basic, TODAY, dry_run=False)
    assert r1.written and r2.written
    assert [x.id for x in store_with_5_snapshots.load_ledger()] == ["20261129-01-BUY-VTI", "20261129-02-BUY-BND"]
    assert [p.date for p in store_with_5_snapshots.load_performance()].count(TODAY) == 1
    assert len(store_with_5_snapshots.load_performance()) == 6
    assert r1.review is True and r2.review is True  # a same-day re-render keeps its run number


def test_pending_row_from_an_earlier_day_does_not_block_today(store_with_cash_3000, fake_market_basic):
    old = LedgerRow(id="20261127-01-BUY-VTI", date=d(-2), action="BUY", ticker="VTI", amount=1500,
                    rationale="initial allocation", ips_check="PASS")
    store_with_cash_3000.save_ledger([old])
    weekly_run(store_with_cash_3000, fake_market_basic, TODAY, dry_run=False)
    assert [x.id for x in store_with_cash_3000.load_ledger()] == [
        "20261127-01-BUY-VTI", "20261129-01-BUY-VTI", "20261129-02-BUY-BND"]


def test_ledger_notes_written_on_live_run_only(store_with_cash_3000, fake_market_basic, monkeypatch):
    note = LedgerRow(id=f"tw-{TODAY}-ACME", date=TODAY, action="NOTE", ticker="ACME", amount=0,
                     rationale="thesis_warning:ACME", ips_check="PASS", response="applied")

    def fake_build(store, market, targets, today, review):
        return ActionSet(compliance=ComplianceReport(), ledger_notes=[note], total=3000.0)

    monkeypatch.setattr(run_mod, "build_actions", fake_build)
    weekly_run(store_with_cash_3000, fake_market_basic, TODAY, dry_run=True)
    assert store_with_cash_3000.load_ledger() == []
    weekly_run(store_with_cash_3000, fake_market_basic, TODAY, dry_run=False)
    assert store_with_cash_3000.load_ledger() == [note]


# ---------------------------------------------------------------- memo content
def test_empty_store_renders_two_first_run_buy_lines(store_with_cash_3000, fake_market_basic):
    r = weekly_run(store_with_cash_3000, fake_market_basic, TODAY, dry_run=True)
    assert "- [ ] BUY — VTI — $1,500 — initial allocation — IPS check: PASS" in r.memo_md
    assert "- [ ] BUY — BND — $900 (12 sh) — initial allocation — IPS check: PASS" in r.memo_md
    assert isinstance(r, RunResult) and isinstance(r.context, MemoContext)
    assert r.context.actions is r.actions and r.context.snapshot is r.snapshot


def test_ai_notes_passed_through(store_with_cash_3000, fake_market_basic):
    r = weekly_run(store_with_cash_3000, fake_market_basic, TODAY, dry_run=True, ai_notes={"6": "AI PARAGRAPH"})
    assert r.context.ai_notes == {"6": "AI PARAGRAPH"}
    assert "AI PARAGRAPH" in r.memo_md.split("## 6. Actions")[1]


def test_etf_premium_rows_and_notes(tmp_path):
    m = FakeMarket(_base_data(vti_nav=299.0))  # VTI has a NAV, BND does not
    s = _store(tmp_path, [_lot("VTI", "indexed_equity", 5, 300.0), _lot("BND", "bonds", 12, 75.0),
                          _lot("CASH", "cash", 600.0)])
    r = weekly_run(s, m, TODAY, dry_run=True)
    assert [t for t, _ in r.context.etf_rows] == ["VTI"]
    assert r.context.etf_notes == ["BND: premium check unavailable"]
    assert "- BND: premium check unavailable" in r.memo_md
    assert "- VTI: premium +0.33%" in r.memo_md


def test_etf_quote_failure_becomes_note(tmp_path):
    data = _base_data()
    s = _store(tmp_path, [_lot("VTI", "indexed_equity", 5, 300.0), _lot("BND", "bonds", 12, 75.0),
                          _lot("CASH", "cash", 600.0)])
    del data["BND"]["quote"]
    r = weekly_run(s, FakeMarket(data), TODAY, dry_run=True)
    assert "BND: premium check unavailable" in r.context.etf_notes
    assert "BND" in r.context.unpriced


# ---------------------------------------------------------------- active sleeve
def _acme_holdings():
    # total 10_000: VTI 5000, BND 3000, ACME 10 sh, CASH remainder
    return [_lot("VTI", "indexed_equity", 50, 100.0), _lot("BND", "bonds", 40, 75.0),
            _lot("ACME", "active", 10, 40.0), _lot("CASH", "cash", 1450.0)]


def _acme_item(fair=75.0, upside=0.5, last=d(-7)) -> WatchItem:
    # price at valuation = 75 / 1.5 = 50
    return WatchItem(ticker="ACME", thesis="t", last_valued=last, fair_value=fair, upside=upside, status="held")


def _acme_mkt(price: float, earnings=None, history=None, info_overrides=None) -> FakeMarket:
    m = _acme_market(info_overrides=info_overrides)
    m.data["ACME"]["quote"] = _q("ACME", price, "EQUITY")
    if earnings is not None:
        m.data["ACME"]["earnings_dates"] = earnings
    if history is not None:
        m.data["ACME"]["history"] = history
    m.data.update({"VTI": {"quote": _q("VTI", 100.0), "history": make_history(100.0, 300)},
                   "BND": {"quote": _q("BND", 75.0), "history": make_history(75.0, 300)}})
    return m


@pytest.fixture
def store_active_acme_valued_last_week(tmp_path):
    return _store(tmp_path, _acme_holdings(), watch=[_acme_item()])


@pytest.fixture
def fake_market_acme_up_10():
    return _acme_mkt(55.0)


@pytest.fixture
def fake_market_acme_up_16():
    return _acme_mkt(58.0)


def test_revalue_only_on_earnings_or_15pct_move(store_active_acme_valued_last_week, fake_market_acme_up_10,
                                                fake_market_acme_up_16):
    s = store_active_acme_valued_last_week
    r = weekly_run(s, fake_market_acme_up_10, TODAY, dry_run=True, force_review=False)
    row = r.context.active_rows[0]
    assert (row.ticker, row.fair_value, row.upside) == ("ACME", 75.0, 0.5)
    assert [w.last_valued for w in r.context.watch_summary] == [d(-7)]

    r = weekly_run(s, fake_market_acme_up_16, TODAY, dry_run=True, force_review=False)
    row = r.context.active_rows[0]
    assert row.fair_value != 75.0 and row.upside != 0.5
    (item,) = r.context.watch_summary
    assert (item.last_valued, item.fair_value, item.upside, item.status) == (TODAY, row.fair_value, row.upside, "held")
    assert s.load_watchlist() == [_acme_item()]  # dry run: revaluation not persisted


def test_revalue_after_recent_earnings_even_without_move(tmp_path):
    s = _store(tmp_path, _acme_holdings(), watch=[_acme_item()])
    m = _acme_mkt(50.0, earnings=_earnings([("2026-11-26 16:30", 1.0, 1.1)]))
    r = weekly_run(s, m, TODAY, dry_run=False, force_review=False)
    assert r.context.active_rows[0].earnings_recent is True
    assert len(r.context.earnings_recaps) == 1
    (saved,) = s.load_watchlist()
    assert saved.last_valued == TODAY and saved.fair_value != 75.0


def test_revalue_when_valuation_missing(tmp_path):
    s = _store(tmp_path, _acme_holdings(), watch=[_acme_item(fair=None, upside=None, last=None)])
    r = weekly_run(s, _acme_mkt(50.0), TODAY, dry_run=True, force_review=False)
    assert r.context.watch_summary[0].last_valued == TODAY
    assert r.context.active_rows[0].fair_value is not None


def test_revaluation_failure_is_a_note(tmp_path):
    s = _store(tmp_path, _acme_holdings(), watch=[_acme_item()])
    m = _acme_mkt(58.0, info_overrides={"marketCap": None})
    r = weekly_run(s, m, TODAY, dry_run=True, force_review=False)
    assert any(n.startswith("ACME: revaluation failed") for n in r.actions.notes)
    assert r.context.active_rows[0].fair_value == 75.0
    assert r.context.watch_summary == [_acme_item()]


def test_active_row_fields(tmp_path):
    hist = make_history(50.0, 30, drift=0.01)
    m = _acme_mkt(55.0, history=hist)
    m.data["ACME"]["eps_trend"] = pd.DataFrame({"current": [2.2], "30daysAgo": [2.0]}, index=["0y"])
    m.data["ACME"]["eps_revisions"] = pd.DataFrame({"upLast30days": [3], "downLast30days": [0]}, index=["0y"])
    s = _store(tmp_path, _acme_holdings(), watch=[_acme_item()])
    r = weekly_run(s, m, TODAY, dry_run=True, force_review=False)
    (row,) = r.context.active_rows
    assert row.weight_pct == pytest.approx(550 / 10_000 * 100)
    assert row.price_change_pct == pytest.approx((1.01 ** 5 - 1) * 100)
    assert row.revision_direction == "up"
    assert (row.earnings_soon, row.earnings_recent) == (False, False)


def test_research_failures_become_notes_not_crashes(store_active_acme_valued_last_week, fake_market_acme_up_10):
    # ACME has no history, eps_trend or earnings_dates in this market
    r = weekly_run(store_active_acme_valued_last_week, fake_market_acme_up_10, TODAY, dry_run=True)
    (row,) = r.context.active_rows
    assert row.price_change_pct is None and row.revision_direction is None
    assert "ACME: price change unavailable (MarketUnavailable)" in r.actions.notes
    assert "ACME: revisions unavailable (MarketUnavailable)" in r.actions.notes


def test_earnings_preview_in_memo(tmp_path):
    s = _store(tmp_path, _acme_holdings(), watch=[_acme_item()])
    m = _acme_mkt(55.0, earnings=_earnings([("2026-12-02 16:30", 1.0, np.nan), ("2026-12-01 08:00", 1.0, np.nan)]))
    r = weekly_run(s, m, TODAY, dry_run=True, force_review=False)
    assert r.context.active_rows[0].earnings_soon is True
    assert r.context.earnings_previews == ["ACME reports 2026-12-01 (before open)"]
    assert "- ACME reports 2026-12-01 (before open)" in r.memo_md


def test_earnings_preview_after_close(tmp_path):
    s = _store(tmp_path, _acme_holdings(), watch=[_acme_item()])
    m = _acme_mkt(55.0, earnings=_earnings([("2026-12-02 16:30", 1.0, np.nan)]))
    r = weekly_run(s, m, TODAY, dry_run=True, force_review=False)
    assert r.context.earnings_previews == ["ACME reports 2026-12-02 (after close)"]


def test_earnings_helper_crash_becomes_note(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("bad frame")

    monkeypatch.setattr(run_mod.earnings, "upcoming_within", boom)
    monkeypatch.setattr(run_mod.earnings, "reported_within", boom)
    s = _store(tmp_path, _acme_holdings(), watch=[_acme_item()])
    r = weekly_run(s, _acme_mkt(55.0), TODAY, dry_run=True, force_review=False)
    assert "ACME: earnings calendar unavailable (RuntimeError)" in r.actions.notes
    assert "ACME: earnings results unavailable (RuntimeError)" in r.actions.notes
    assert r.context.active_rows[0].earnings_soon is False


# ---------------------------------------------------------------- watchlist
@pytest.fixture
def store_with_candidate(store_with_cash_3000):
    store_with_cash_3000.save_watchlist([WatchItem(ticker="ACME", thesis="t", last_valued=None, fair_value=None,
                                                   upside=None, status="candidate")])
    return store_with_cash_3000


def _market_with_acme_candidate() -> FakeMarket:
    m = _acme_market()
    m.data.update(_base_data())
    return m


def test_watchlist_refreshed_on_review_but_saved_only_live(store_with_candidate):
    m = _market_with_acme_candidate()
    before = _files(store_with_candidate.root)
    r = weekly_run(store_with_candidate, m, TODAY, dry_run=True, force_review=True)
    assert r.context.watch_summary[0].last_valued == TODAY
    assert _files(store_with_candidate.root) == before

    weekly_run(store_with_candidate, m, TODAY, dry_run=False, force_review=True)
    assert store_with_candidate.load_watchlist()[0].last_valued == TODAY


def test_watchlist_not_refreshed_on_non_review(store_with_candidate):
    r = weekly_run(store_with_candidate, _market_with_acme_candidate(), TODAY, dry_run=False, force_review=False)
    assert r.context.watch_summary[0].last_valued is None
    assert store_with_candidate.load_watchlist()[0].last_valued is None


def test_refresh_watchlist_crash_becomes_note(store_with_candidate, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("screen down")

    monkeypatch.setattr(run_mod, "refresh_watchlist", boom)
    r = weekly_run(store_with_candidate, _market_with_acme_candidate(), TODAY, dry_run=True, force_review=True)
    assert "watchlist refresh failed (RuntimeError); kept last week's values" in r.actions.notes
    assert r.context.watch_summary[0].last_valued is None
