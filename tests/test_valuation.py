import math

import numpy as np
import pytest

from fundkit.models import Quote
from fundkit.research.valuation import Valuation, value_company
from tests.conftest import _acme_market

PEERS = ["P1", "P2", "P3"]


def test_acme_blended_and_upside(fake_market_acme):
    v = value_company("ACME", fake_market_acme, peers=PEERS, rf=0.045)
    assert v.dcf is not None and v.relative is not None
    assert v.blended == pytest.approx(0.5 * v.dcf + 0.5 * v.relative)
    assert v.upside == pytest.approx(v.blended / 50 - 1)
    assert len(v.sensitivity) == 25


def test_negative_ebitda_skips_multiple(fake_market_acme_neg_ebitda):
    v = value_company("ACME", fake_market_acme_neg_ebitda, peers=PEERS, rf=0.045)
    assert "ev_ebitda_skipped" in v.flags and not math.isnan(v.blended)


def test_wacc_below_g_is_capped(fake_market_acme_low_beta):
    v = value_company("ACME", fake_market_acme_low_beta, peers=PEERS, rf=0.045)
    assert v.wacc == pytest.approx(0.02025)
    assert "g_capped" in v.flags and v.g == pytest.approx(v.wacc - 0.005)


def test_missing_beta_uses_default_one(fake_market_acme_no_beta):
    v = value_company("ACME", fake_market_acme_no_beta, peers=PEERS, rf=0.045)
    assert "beta_default" in v.flags
    assert v.wacc == pytest.approx(0.045 + 1.0 * 0.055)


def test_peer_dropped_on_market_unavailable(fake_market_acme):
    v = value_company("ACME", fake_market_acme, peers=PEERS + ["NOPE"], rf=0.045)
    assert "peer_dropped:NOPE" in v.flags
    assert v.relative is not None


def test_stale_rf_when_tnx_unavailable(fake_market_acme):
    v = value_company("ACME", fake_market_acme, peers=PEERS)
    assert "stale_rf" in v.flags
    assert v.wacc == pytest.approx(0.045 + 0.055)


def test_rf_from_tnx(fake_market_acme):
    from fundkit.models import Quote
    fake_market_acme.data["^TNX"] = {"quote": Quote(ticker="^TNX", price=4.0, bid=None, ask=None,
                                                    expense_ratio=None, nav=None, quote_type="INDEX",
                                                    avg_volume=None)}
    v = value_company("ACME", fake_market_acme, peers=PEERS)
    assert "stale_rf" not in v.flags
    assert v.wacc == pytest.approx(0.04 + 0.055)


def test_no_peers(fake_market_acme):
    v = value_company("ACME", fake_market_acme, peers=[], rf=0.045)
    assert v.relative is None and "no_peers" in v.flags
    assert v.blended == pytest.approx(v.dcf)
    assert v.confidence == "low"


def test_missing_row_flagged_low_confidence(fake_market_acme_no_nwc):
    v = value_company("ACME", fake_market_acme_no_nwc, peers=PEERS, rf=0.045)
    assert "missing:Change In Working Capital" in v.flags
    assert v.confidence == "low"


def test_confidence_high_for_clean_acme(fake_market_acme):
    v = value_company("ACME", fake_market_acme, peers=PEERS, rf=0.045)
    assert v.confidence == "high"
    assert v.flags == []


def test_hand_calculation_and_sensitivity_center(fake_market_acme):
    """ACME by hand: wacc = 0.045 + 1.0*0.055 = 0.10 (no debt), tax 0.21, g = 0.025.
    y1 = (121/100)^(1/2) - 1 = 0.10; path = linspace(0.10, 0.035, 5).
    Margins: EBIT 20%, D&A 5%, CapEx -6%, dNWC -1% of revenue."""
    v = value_company("ACME", fake_market_acme, peers=PEERS, rf=0.045)
    wacc, g, tax = 0.10, 0.025, 0.21
    rev, pv, fcff = 121.0, 0.0, 0.0
    for t, gr in enumerate([0.10, 0.08375, 0.0675, 0.05125, 0.035], start=1):
        rev *= 1 + gr
        ebit, da = 0.20 * rev, 0.05 * rev
        fcff = ebit * (1 - tax) + da - 0.06 * rev - 0.01 * rev
        pv += fcff / (1 + wacc) ** t
    gordon = fcff * (1 + g) / (wacc - g)
    exit_tv = (ebit + da) * 15
    ev = pv + (0.5 * (gordon + exit_tv)) / (1 + wacc) ** 5
    assert v.dcf == pytest.approx((ev + 20.0) / 10.0)
    assert v.tv_share == pytest.approx((0.5 * (gordon + exit_tv)) / 1.1**5 / ev)
    # relative: P/E 22*2=44; EV/Rev (5*121-(-20))/10 = 62.5; EV/EBITDA (15*30.25+20)/10 = 47.375
    assert v.relative == pytest.approx(47.375)  # median of 44, 62.5, 47.375
    # sensitivity centre = Gordon-only DCF at base wacc/g
    centre = (pv + gordon / (1 + wacc) ** 5 + 20.0) / 10.0
    assert v.sensitivity["0.1000|0.0250"] == pytest.approx(centre)
    assert set(v.sensitivity) >= {"0.0900|0.0150", "0.1100|0.0350"}


def test_sensitivity_nan_cell_when_wacc_le_g(fake_market_acme_low_beta):
    v = value_company("ACME", fake_market_acme_low_beta, peers=PEERS, rf=0.045)
    assert len(v.sensitivity) == 25
    assert v.sensitivity["0.0102|0.0350"] is None  # wacc 0.01025 <= g 0.035
    assert not math.isnan(v.blended)


def test_json_round_trip_with_none_cells(fake_market_acme_low_beta):
    v = value_company("ACME", fake_market_acme_low_beta, peers=PEERS, rf=0.045)
    assert any(c is None for c in v.sensitivity.values())
    assert Valuation.model_validate_json(v.model_dump_json()) == v


def test_fewer_than_three_years_is_low_confidence():
    v = value_company("ACME", _acme_market(n_years=2), peers=PEERS, rf=0.045)
    assert v.confidence == "low" and v.dcf is not None


def test_medium_confidence_on_pe_skipped():
    m = _acme_market(info_overrides={"forwardEps": -1.0})
    v = value_company("ACME", m, peers=PEERS, rf=0.045)
    assert "pe_skipped" in v.flags and v.confidence == "medium"


def test_growth_clamped():
    m = _acme_market()
    m.data["ACME"]["income_stmt"].loc["Total Revenue"] = [200.0, 110.0, 100.0]  # CAGR 0.414
    v = value_company("ACME", m, peers=PEERS, rf=0.045)
    assert "growth_clamped" in v.flags
    # here the clamped DCF (~114) and the relative value (~47) are >2x apart, so confidence is low;
    # with the relative method removed the clamp alone gives medium
    assert "methods_disagree" in v.flags and v.confidence == "low"
    v2 = value_company("ACME", m, peers=[], rf=0.045)
    assert "growth_clamped" in v2.flags and "methods_disagree" not in v2.flags


def test_no_dcf_fills_25_none_cells_and_still_blends():
    m = _acme_market(n_years=1)
    v = value_company("ACME", m, peers=PEERS, rf=0.045)
    assert v.dcf is None and "no_dcf" in v.flags and v.tv_share is None
    assert len(v.sensitivity) == 25 and all(c is None for c in v.sensitivity.values())
    assert v.blended == pytest.approx(v.relative) and v.confidence == "low"


def test_valuation_impossible_raises():
    with pytest.raises(ValueError, match="valuation impossible"):
        value_company("ACME", _acme_market(n_years=1), peers=[], rf=0.045)


def test_negative_peer_multiple_dropped(fake_market_acme):
    d = fake_market_acme.data
    d["P1"]["info"]["forwardPE"], d["P2"]["info"]["forwardPE"], d["P3"]["info"]["forwardPE"] = 30, 40, -15
    v = value_company("ACME", fake_market_acme, peers=PEERS, rf=0.045)
    assert "peer_multiple_dropped:P3:forwardPE" in v.flags
    # P/E median 35 -> 70; implied (70, 62.5, 47.375) -> 62.5 (a -15 would give median 30 -> 60)
    assert v.relative == pytest.approx(62.5)


def test_forward_eps_preferred_and_trailing_fallback(fake_market_acme):
    d = fake_market_acme.data["ACME"]["info"]
    d["forwardEps"] = 3.0
    v = value_company("ACME", fake_market_acme, peers=PEERS, rf=0.045)
    assert "eps_trailing_used" not in v.flags
    assert v.relative == pytest.approx(np.median([22 * 3.0, 62.5, 47.375]))
    del d["forwardEps"]
    v = value_company("ACME", fake_market_acme, peers=PEERS, rf=0.045)
    assert "eps_trailing_used" in v.flags
    assert v.relative == pytest.approx(47.375)


def test_nan_tnx_price_is_stale_rf(fake_market_acme):
    fake_market_acme.data["^TNX"] = {"quote": Quote(ticker="^TNX", price=float("nan"), bid=None, ask=None,
                                                    expense_ratio=None, nav=None, quote_type="INDEX",
                                                    avg_volume=None)}
    v = value_company("ACME", fake_market_acme, peers=PEERS)
    assert "stale_rf" in v.flags and v.wacc == pytest.approx(0.10)


def test_debt_path_kd_and_ev_bridge():
    """debt 100, interest 5 -> kd 5%; wacc = 5/6*0.10 + 1/6*0.05*0.79; equity = EV + cash - debt."""
    v = value_company("ACME", _acme_market(debt=100.0, interest=5.0), peers=PEERS, rf=0.045)
    wacc = (500 / 600) * 0.10 + (100 / 600) * 0.05 * 0.79
    assert v.wacc == pytest.approx(wacc)
    tax, g, rev, pv, fcff = 0.21, 0.025, 121.0, 0.0, 0.0
    for t, gr in enumerate(np.linspace(0.10, g + 0.01, 5), start=1):
        rev *= 1 + gr
        ebit, da = 0.2 * rev, 0.05 * rev
        fcff = ebit * (1 - tax) + da - 0.07 * rev
        pv += fcff / (1 + wacc) ** t
    tv = 0.5 * (fcff * (1 + g) / (wacc - g) + (ebit + da) * 15)
    assert v.dcf == pytest.approx((pv + tv / (1 + wacc) ** 5 + 20.0 - 100.0) / 10.0)


def test_valuation_refuses_statement_currency_different_from_price_currency():
    from tests.conftest import _acme_market
    m = _acme_market(info_overrides={"currency": "USD", "financialCurrency": "CNY"})
    with pytest.raises(ValueError, match="statements in CNY but price in USD"):
        value_company("ACME", m, peers=[])


def test_methods_more_than_2x_apart_drop_confidence_to_low():
    # peers at ~3x ACME's own multiples push the relative value far above the DCF
    m = _acme_market(info_overrides={"forwardPE": 10.0})
    for p in ("P1", "P2", "P3"):
        m.data[p] = {"info": {"forwardPE": 60.0, "enterpriseToRevenue": 15.0, "enterpriseToEbitda": 60.0}}
    v = value_company("ACME", m, peers=["P1", "P2", "P3"], rf=0.045)
    assert "methods_disagree" in v.flags and v.confidence == "low"
    assert max(v.dcf, v.relative) / min(v.dcf, v.relative) > 2


def test_negative_dcf_with_positive_relative_is_a_disagreement():
    from fundkit.research import valuation as V
    m = _acme_market(ebit_margin=-0.30)  # losses -> negative DCF
    v = value_company("ACME", m, peers=PEERS, rf=0.045)
    assert v.dcf is not None and v.dcf < 0 < v.relative
    assert "methods_disagree" in v.flags and v.confidence == "low"
