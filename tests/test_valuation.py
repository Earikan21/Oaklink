import math

import numpy as np
import pytest

from fundkit.research.valuation import value_company
from tests.conftest import FakeMarket

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
    assert v.confidence == "medium"


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
    assert math.isnan(v.sensitivity["0.0102|0.0350"])  # wacc 0.01025 <= g 0.035
    assert not math.isnan(v.blended)


def test_valuation_serializes_to_json(fake_market_acme):
    v = value_company("ACME", fake_market_acme, peers=PEERS, rf=0.045)
    assert "sensitivity" in v.model_dump_json()
