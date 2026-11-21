from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from conftest import FakeMarket, _acme_market, make_holding, make_history
from fundkit.models import Quote, Targets, WatchItem
from fundkit.screen import (UNIVERSE_QUERY, GateResult, gate, peers_for, quality_ok,
                            refresh_watchlist, universe)

TARGETS = Targets()
TODAY = date(2026, 11, 29)
PEERS = ["P1", "P2", "P3"]


def _noise(seed: int, n: int = 300) -> np.ndarray:
    return np.random.default_rng(seed).normal(0.0, 0.01, n)


def _hist_from_returns(returns: np.ndarray, start: float = 30.0) -> pd.DataFrame:
    base = make_history(start, len(returns))
    close = start * np.exp(np.cumsum(returns))
    base["Close"] = close
    base["High"], base["Low"] = close * 1.01, close * 0.99
    return base


def _quote(price: float, bid: float | None = 33.0, ask: float | None = 33.03) -> Quote:
    return Quote(ticker="ACME", price=price, bid=bid, ask=ask, expense_ratio=None, nav=None,
                 quote_type="EQUITY", avg_volume=None)


def gate_env(*, price=33.0, bid=33.0, ask=33.03, cand_drift=0.002, rho=0.0, peers=PEERS,
             dollars=150.0, info_extra=None, cashflow_fcf=None, dollars_held=150.0):
    """Clean candidate by default: upside ~0.31, stage 2, corr ~0, spread 0.09%, quality ok, $150 into $3k."""
    market = _acme_market(info_overrides={"debtToEquity": 50.0, "industry": "Widgets", "longName": "Acme Corp",
                                          **(info_extra or {})})
    cand_ret = cand_drift + _noise(1)
    held_ret = 0.001 + rho * _noise(1) + np.sqrt(1 - rho ** 2) * _noise(2)
    market.data["ACME"]["quote"] = _quote(price, bid, ask)
    market.data["ACME"]["history"] = _hist_from_returns(cand_ret)
    market.data["HELD"] = {"history": _hist_from_returns(held_ret, 50.0)}
    if cashflow_fcf is not None:
        cf = market.data["ACME"]["cashflow"].copy()
        cf.loc["Free Cash Flow"] = cashflow_fcf
        market.data["ACME"]["cashflow"] = cf
    holdings = [make_holding("VTI", "indexed_equity", 5, 300.0), make_holding("BND", "bonds", 10, 75.0),
                make_holding("CASH", "cash", 550.0), make_holding("HELD", "active", 3, 50.0)]
    values = {"VTI": 1500.0, "BND": 800.0, "CASH": 550.0, "HELD": dollars_held}
    return dict(ticker="ACME", dollars=dollars, holdings=holdings, values=values, targets=TARGETS,
                market=market, peers=peers, held_active=["HELD"])


@pytest.fixture
def gate_env_pass():
    return gate_env()


@pytest.fixture
def gate_env_stage4_lowupside():
    return gate_env(price=39.3, cand_drift=-0.003)


@pytest.fixture
def gate_env_corr_081():
    return gate_env(rho=0.95)


@pytest.fixture
def gate_env_spread_exact_half_pct():
    return gate_env(bid=99.75 * 0.33, ask=100.25 * 0.33)  # (ask-bid)/mid = 0.5%


# ---------------------------------------------------------------- universe / quality / peers
def test_universe_returns_screen_result():
    m = FakeMarket({"__screen__": ["AAA", "BBB"]})
    assert universe(m) == ["AAA", "BBB"]


def test_universe_query_verbatim():
    assert UNIVERSE_QUERY == {"region": "us", "marketcap_gt": 2e9, "pe_lt": 20, "de_lt": 1.0, "eps_growth_5y_gt": 0}


def _q_market(fcf, de, drop_fcf=False):
    cf = pd.DataFrame({"2025-12-31": [fcf], "2024-12-31": [-99.0]}, index=["Free Cash Flow"])
    if drop_fcf:
        cf = cf.drop(index="Free Cash Flow")
        cf.loc["Other"] = [1.0, 1.0]
    info = {} if de is None else {"debtToEquity": de}
    return FakeMarket({"X": {"cashflow": cf, "info": info}})


def test_quality_ok_passes():
    assert quality_ok("X", _q_market(10.0, 50.0)) == (True, [])


def test_quality_fails_on_negative_fcf():
    ok, reasons = quality_ok("X", _q_market(-1.0, 50.0))
    assert not ok and reasons == ["FCF <= 0"]


def test_quality_fails_on_leverage_and_reports_both():
    ok, reasons = quality_ok("X", _q_market(-1.0, 120.0))
    assert not ok and reasons == ["FCF <= 0", "debt/equity 1.20 >= 1.0"]


def test_quality_de_exactly_one_fails():
    assert quality_ok("X", _q_market(1.0, 100.0)) == (False, ["debt/equity 1.00 >= 1.0"])


def test_quality_missing_data_fails():
    assert quality_ok("X", _q_market(1.0, None))[1] == ["no debt/equity data"]
    assert quality_ok("X", _q_market(1.0, 50.0, drop_fcf=True))[1] == ["no FCF data"]


def test_peers_for_excludes_self_and_caps_at_five():
    m = FakeMarket({"__screen__": ["ACME", "A", "B", "C", "D", "E"], "ACME": {"info": {"industry": "Widgets"}}})
    assert peers_for("ACME", m) == ["A", "B", "C", "D", "E"]


def test_peers_for_empty_when_industry_missing():
    m = FakeMarket({"__screen__": ["A"], "ACME": {"info": {}}})
    assert peers_for("ACME", m) == []


def test_peers_for_empty_when_screen_raises():
    class Boom(FakeMarket):
        def screen(self, query):
            raise RuntimeError("down")
    assert peers_for("ACME", Boom({"ACME": {"info": {"industry": "W"}}})) == []


# ---------------------------------------------------------------- gate
def test_gate_passes_clean_candidate(gate_env_pass):
    g = gate(**gate_env_pass)
    assert isinstance(g, GateResult)
    assert g.passed and g.reasons == []
    assert g.upside >= 0.25 and g.stage == 2 and g.max_corr <= 0.8 and g.spread_pct < 0.005
    assert g.valuation is not None and g.confidence == "high"


def test_gate_fails_on_stage4_and_lists_all_reasons(gate_env_stage4_lowupside):
    g = gate(**gate_env_stage4_lowupside)
    assert not g.passed and len(g.reasons) == 2
    assert g.reasons[0].startswith("upside ") and g.reasons[0].endswith("< 25%")
    assert g.reasons[1] == "stage 4 downtrend"
    assert g.stage == 4


def test_gate_fails_correlation_0_81(gate_env_corr_081):
    g = gate(**gate_env_corr_081)
    assert not g.passed and g.max_corr > 0.80
    assert g.reasons == [f"correlation {g.max_corr:.2f} > 0.80"]


def test_gate_fails_spread_0_5(gate_env_spread_exact_half_pct):
    g = gate(**gate_env_spread_exact_half_pct)  # 0.5% is not < 0.5%
    assert not g.passed
    assert g.reasons == ["spread 0.50% >= 0.50%"]


def test_gate_reports_low_valuation_confidence():
    g = gate(**gate_env(peers=[]))
    assert "low valuation confidence" in g.reasons
    assert g.confidence == "low" and not g.passed


def test_gate_runs_every_check_and_reports_all_reasons():
    env = gate_env(price=39.3, cand_drift=-0.003, rho=0.95, bid=99.0, ask=101.0, dollars=900.0,
                   info_extra={"debtToEquity": 150.0, "longName": "Acme 3x Leveraged"})
    g = gate(**env)
    joined = " | ".join(g.reasons)
    for needle in ("upside", "stage 4 downtrend", "correlation", "spread", "debt/equity 1.50 >= 1.0",
                   "per-name cap", "denied keyword '3x'"):
        assert needle in joined, needle
    assert "denied keyword 'leveraged'" in joined
    assert len(g.reasons) == 8


def test_gate_quality_failure_negative_fcf():
    g = gate(**gate_env(cashflow_fcf=[-5.0, 5.0, 5.0]))
    assert g.reasons == ["FCF <= 0"]


def test_gate_buy_allowed_reason_verbatim():
    g = gate(**gate_env(dollars=900.0))
    assert len(g.reasons) == 1 and "per-name cap" in g.reasons[0]


def test_gate_valuation_impossible():
    env = gate_env()
    env["market"].data["ACME"]["info"] = {**env["market"].data["ACME"]["info"], "marketCap": None}
    g = gate(**env)
    assert "valuation impossible" in g.reasons and g.upside is None and g.valuation is None and not g.passed


def test_gate_insufficient_history_for_stage():
    env = gate_env()
    env["market"].data["ACME"]["history"] = env["market"].data["ACME"]["history"].iloc[-100:]
    g = gate(**env)
    assert "insufficient history for stage" in g.reasons and g.stage is None


def test_gate_spread_missing_fails_closed():
    g = gate(**gate_env(bid=None, ask=None))
    assert "spread unavailable" in g.reasons and g.spread_pct is None


# ---------------------------------------------------------------- refresh_watchlist
def _watch(ticker="ACME", last=TODAY - timedelta(days=10), fair=40.0, upside=0.25, status="candidate"):
    return WatchItem(ticker=ticker, thesis="t", last_valued=last, fair_value=fair, upside=upside, status=status)


def _refresh_market(price):
    m = _acme_market(info_overrides={"industry": "Widgets"})
    m.data["ACME"]["quote"] = _quote(price)
    m.data["__screen__"] = ["ACME"] + PEERS
    return m


# fair 40 / 1.25 -> price at valuation 32.0
def test_refresh_only_stale_or_moved():
    fresh = _watch("ACME", fair=40.0, upside=0.25)            # price 32 -> +3% = 32.96, 10 days old
    moved = _watch("MOVE", fair=40.0, upside=0.25)            # +16% = 37.12
    m = _refresh_market(32.96)
    m.data["MOVE"] = {**_acme_market(info_overrides={"industry": "Widgets"}).data["ACME"],
                      "quote": _quote(37.12)}
    m.data["MOVE"]["quote"] = m.data["MOVE"]["quote"].model_copy(update={"ticker": "MOVE"})
    out = refresh_watchlist([fresh, moved], m, TARGETS, TODAY)
    assert out[0] == fresh
    assert out[1].last_valued == TODAY and out[1].fair_value != 40.0


def test_refresh_revalues_when_never_valued_and_marks_passed_at_030():
    item = _watch(last=None, fair=None, upside=None)
    m = _refresh_market(33.29)  # blended 43.28 -> upside ~0.30
    [out] = refresh_watchlist([item], m, TARGETS, TODAY)
    assert out.status == "passed" and out.upside == pytest.approx(0.30, abs=0.01)
    assert out.fair_value == pytest.approx(43.2817, abs=1e-3) and out.last_valued == TODAY


def test_refresh_marks_candidate_at_010():
    item = _watch(last=None, fair=None, upside=None, status="passed")
    m = _refresh_market(39.35)  # upside ~0.10
    [out] = refresh_watchlist([item], m, TARGETS, TODAY)
    assert out.status == "candidate" and out.upside == pytest.approx(0.10, abs=0.01)


def test_refresh_stale_by_age_boundary():
    m = _refresh_market(32.0)
    at42 = _watch(last=TODAY - timedelta(days=42))
    at43 = _watch(last=TODAY - timedelta(days=43))
    assert refresh_watchlist([at42], m, TARGETS, TODAY)[0] == at42
    assert refresh_watchlist([at43], m, TARGETS, TODAY)[0].last_valued == TODAY


def test_refresh_preserves_held_and_rejected_status():
    m = _refresh_market(33.29)
    held = _watch(last=None, fair=None, upside=None, status="held")
    rej = _watch(last=None, fair=None, upside=None, status="rejected")
    out = refresh_watchlist([held, rej], m, TARGETS, TODAY)
    assert [o.status for o in out] == ["held", "rejected"]
    assert all(o.last_valued == TODAY and o.upside is not None for o in out)


def test_refresh_leaves_item_unchanged_when_valuation_fails_and_does_not_mutate():
    m = _refresh_market(33.29)
    m.data["ACME"]["info"] = {**m.data["ACME"]["info"], "marketCap": None}
    item = _watch(last=None, fair=None, upside=None)
    before = item.model_copy()
    out = refresh_watchlist([item], m, TARGETS, TODAY)
    assert out == [before] and item == before


def test_refresh_does_not_mutate_inputs():
    m = _refresh_market(33.29)
    item = _watch(last=None, fair=None, upside=None)
    out = refresh_watchlist([item], m, TARGETS, TODAY)
    assert item.last_valued is None and out[0] is not item
