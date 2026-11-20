import pytest

from conftest import make_holding
from fundkit.models import Targets
from fundkit.portfolio import (
    Proposal, name_weights, rebalance_proposals, sleeve_weights, value_holdings, whole_share_target,
)


def test_value_holdings_prices_etfs_and_sums_cash(three_k_portfolio, fake_market_3k):
    holdings, expected = three_k_portfolio
    values, unpriced = value_holdings(holdings, fake_market_3k)
    assert values == pytest.approx(expected)
    assert unpriced == []


def test_value_holdings_sums_multiple_lots(fake_market_3k):
    holdings = [make_holding("VTI", "indexed_equity", 2), make_holding("VTI", "indexed_equity", 3),
                make_holding("CASH", "cash", 100.0), make_holding("CASH", "cash", 50.0)]
    values, _ = value_holdings(holdings, fake_market_3k)
    assert values == pytest.approx({"VTI": 1500.0, "CASH": 150.0})


def test_unpriced_ticker_reported_not_fatal(three_k_portfolio, fake_market_3k):
    holdings = three_k_portfolio[0] + [make_holding("ZZZZ", "active", 10)]
    values, unpriced = value_holdings(holdings, fake_market_3k)
    assert unpriced == ["ZZZZ"]
    assert "VTI" in values and "ZZZZ" not in values


def test_weights_include_cash(three_k_portfolio):
    w = sleeve_weights(*three_k_portfolio)
    assert w == pytest.approx({"indexed_equity": 0.5, "active": 0.0, "bonds": 0.3, "cash": 0.2})


def test_sleeve_weights_empty_total_is_all_zero():
    assert sleeve_weights([], {}) == {"indexed_equity": 0.0, "active": 0.0, "bonds": 0.0, "cash": 0.0}


def test_name_weights_active_only(three_k_portfolio):
    holdings, values = three_k_portfolio
    holdings = holdings + [make_holding("AAPL", "active", 1)]
    values = {**values, "AAPL": 100.0}
    # total 3100; VTI/BND/CASH are not names
    assert name_weights(holdings, values) == pytest.approx({"AAPL": 100.0 / 3100.0})


@pytest.mark.parametrize("dollars,price,expected", [
    (900, 75.3, 12), (0, 75.0, 0), (-50, 75.0, 0), (112.5, 75.0, 2), (30, 75.0, 0),
])
def test_whole_share_target(dollars, price, expected):
    assert whole_share_target(dollars, price) == expected


def _portfolio(vti, bnd, cash, bnd_price=75.0):
    holdings = [make_holding("VTI", "indexed_equity", vti / 300.0), make_holding("BND", "bonds", bnd / bnd_price),
                make_holding("CASH", "cash", cash)]
    return holdings, {"VTI": vti, "BND": bnd, "CASH": cash}


def test_rebalance_sells_index_etf_when_stocks_57pct():
    holdings, values = _portfolio(1710.0, 900.0, 390.0)  # 57 / 30 / 13 of 3000
    props = rebalance_proposals(holdings, values, Targets(), {"VTI": 300.0, "BND": 75.0}, review=False)
    assert len(props) == 1
    p = props[0]
    assert (p.action, p.ticker, p.shares) == ("SELL", "VTI", None)
    assert p.dollars == pytest.approx(210.0)
    assert p.reason == "rebalance stocks 57.0% → 50%"


def test_rebalance_sells_index_etf_not_active_names():
    holdings, values = _portfolio(1200.0, 900.0, 390.0)
    holdings.append(make_holding("AAPL", "active", 1))
    values["AAPL"] = 510.0  # stocks = 1710/3000 = 57%
    props = rebalance_proposals(holdings, values, Targets(), {"VTI": 300.0, "BND": 75.0}, review=False)
    assert [(p.action, p.ticker) for p in props] == [("SELL", "VTI")]
    assert props[0].dollars == pytest.approx(210.0)


def test_rebalance_buys_bond_etf_in_whole_shares_when_bonds_24pct():
    holdings, values = _portfolio(1500.0, 720.0, 780.0, bnd_price=80.0)  # 50 / 24 / 26 of 3000
    props = rebalance_proposals(holdings, values, Targets(), {"VTI": 300.0, "BND": 80.0}, review=False)
    assert len(props) == 1
    p = props[0]
    assert (p.action, p.ticker, p.shares) == ("BUY", "BND", 2)  # 6% gap = $180 = 2.25 sh
    assert p.dollars == pytest.approx(160.0)
    assert p.reason == "rebalance bonds 24.0% → 30%"


def test_rebalance_nothing_when_within_band(three_k_portfolio):
    assert rebalance_proposals(*three_k_portfolio, Targets(), {"VTI": 300.0, "BND": 75.0}, review=False) == []


def test_rebalance_bond_gap_within_one_share_proposes_nothing():
    # bonds 28.5% vs 30% is outside a 1pt band, but the $45 gap rounds to 1 share (<= tolerance)
    holdings, values = _portfolio(1500.0, 855.0, 645.0)
    props = rebalance_proposals(holdings, values, Targets(band=0.01), {"VTI": 300.0, "BND": 75.0}, review=False)
    assert props == []


def test_rebalance_empty_portfolio_proposes_nothing():
    assert rebalance_proposals([], {}, Targets(), {}, review=False) == []


def test_proposal_model_defaults():
    p = Proposal(action="BUY", ticker="BND", dollars=75.0, reason="x")
    assert p.shares is None and p.est_tax is None
