import pytest

from conftest import make_holding
from fundkit.ips import ALLOWED_TYPES, DENIED_KEYWORDS, buy_allowed, check_instrument, compliance
from fundkit.models import Quote, Targets


def quote(er=0.0003, quote_type="ETF"):
    return Quote(ticker="X", price=10, bid=None, ask=None, expense_ratio=er, nav=None,
                 quote_type=quote_type, avg_volume=None)


def with_active(three_k_portfolio, ticker, dollars, cash_after):
    """Replace cash so total stays 3000 with `ticker` held as an active name."""
    holdings, values = three_k_portfolio
    holdings = [h for h in holdings if h.ticker != "CASH"] + [
        make_holding(ticker, "active", 1), make_holding("CASH", "cash", cash_after)]
    return holdings, {"VTI": 1500.0, "BND": 900.0, ticker: dollars, "CASH": cash_after}


def test_constants():
    assert ALLOWED_TYPES == {"ETF", "MUTUALFUND", "EQUITY"}
    assert DENIED_KEYWORDS == ("leveraged", "2x", "3x", "ultra", "inverse", "bear", "futures")


def test_no_flags_when_on_target(three_k_portfolio):
    rep = compliance(*three_k_portfolio, Targets(), review=False)
    assert rep.sleeve_flags == [] and rep.trim_flags == []
    assert rep.active_ceiling_flag is None and rep.indexed_lt_active is False and rep.unpriced == []


def test_sleeve_flag_outside_5pt_band():  # stocks at 56%, bonds 30%, cash 14%... bonds/cash computed below
    holdings = [make_holding("VTI", "indexed_equity", 1), make_holding("BND", "bonds", 1), make_holding("CASH", "cash", 1)]
    values = {"VTI": 560.0, "BND": 300.0, "CASH": 140.0}  # 56 / 30 / 14
    rep = compliance(holdings, values, Targets(), review=False)
    assert rep.sleeve_flags == ["stocks 56.0% vs 50% (+6.0 pts)", "cash 14.0% vs 20% (-6.0 pts)"]


def test_exactly_on_band_edge_not_flagged():
    holdings = [make_holding("VTI", "indexed_equity", 1), make_holding("BND", "bonds", 1), make_holding("CASH", "cash", 1)]
    values = {"VTI": 550.0, "BND": 300.0, "CASH": 150.0}  # 55 / 30 / 15
    assert compliance(holdings, values, Targets(), review=False).sleeve_flags == []


def test_stocks_flag_counts_active_with_indexed():
    holdings = [make_holding("VTI", "indexed_equity", 1), make_holding("AAPL", "active", 1),
                make_holding("BND", "bonds", 1), make_holding("CASH", "cash", 1)]
    values = {"VTI": 400.0, "AAPL": 160.0, "BND": 300.0, "CASH": 140.0}  # stocks 56
    assert compliance(holdings, values, Targets(), review=False).sleeve_flags[0] == "stocks 56.0% vs 50% (+6.0 pts)"


@pytest.mark.parametrize("review,aapl_dollars,flagged", [
    (False, 210.0, False),  # 7.0% weekly: under 7.5%
    (True, 210.0, True),    # 7.0% review: over 6%
    (False, 228.0, True),   # 7.6% weekly
    (True, 228.0, True),    # 7.6% review
])
def test_trim_threshold_7_5_weekly_6_review(three_k_portfolio, review, aapl_dollars, flagged):
    holdings, values = with_active(three_k_portfolio, "AAPL", aapl_dollars, 600.0 - aapl_dollars)
    rep = compliance(holdings, values, Targets(), review=review)
    assert (len(rep.trim_flags) == 1) is flagged


def test_trim_flag_string_weekly_and_review(three_k_portfolio):
    holdings, values = with_active(three_k_portfolio, "AAPL", 228.0, 372.0)
    assert compliance(holdings, values, Targets(), review=False).trim_flags == ["AAPL 7.6% > 7.5% trim threshold"]
    assert compliance(holdings, values, Targets(), review=True).trim_flags == ["AAPL 7.6% > 6.0% trim threshold"]


def test_active_ceiling_flag_and_indexed_lt_active():
    holdings = [make_holding("VTI", "indexed_equity", 1), make_holding("AAPL", "active", 1),
                make_holding("BND", "bonds", 1), make_holding("CASH", "cash", 1)]
    values = {"VTI": 150.0, "AAPL": 350.0, "BND": 300.0, "CASH": 200.0}  # active 35%, indexed 15%
    rep = compliance(holdings, values, Targets(), review=False)
    assert rep.active_ceiling_flag == "active 35.0% > 30% ceiling"
    assert rep.indexed_lt_active is True


def test_unpriced_passed_through(three_k_portfolio):
    rep = compliance(*three_k_portfolio, Targets(), review=False, unpriced=["ZZZZ"])
    assert rep.unpriced == ["ZZZZ"]


def test_buy_refused_above_name_cap(three_k_portfolio):
    ok, why = buy_allowed("AAPL", 160.0, *three_k_portfolio, Targets())  # 160/3000 = 5.33%
    assert not ok and "5%" in why
    assert why == "would be 5.3% > 5% per-name cap"


def test_buy_allowed_under_cap(three_k_portfolio):
    assert buy_allowed("AAPL", 150.0, *three_k_portfolio, Targets()) == (True, "ok")


def test_buy_counts_existing_position(three_k_portfolio):
    holdings, values = with_active(three_k_portfolio, "AAPL", 100.0, 500.0)
    ok, why = buy_allowed("AAPL", 60.0, holdings, values, Targets())  # 160/3000
    assert not ok and why == "would be 5.3% > 5% per-name cap"


def test_buy_refused_above_active_ceiling():
    holdings = [make_holding("VTI", "indexed_equity", 1), make_holding("A", "active", 1),
                make_holding("BND", "bonds", 1), make_holding("CASH", "cash", 1)]
    values = {"VTI": 1200.0, "A": 50.0, "BND": 900.0, "CASH": 850.0}  # total 3000
    t = Targets(name_cap=1.0, active_ceiling=0.05)  # isolate the ceiling rule
    ok, why = buy_allowed("B", 150.0, holdings, values, t)  # active 200/3000 = 6.7% > 5%
    assert not ok and why == "active sleeve would be 6.7% > 5% ceiling"


def test_buy_refused_when_indexed_would_trail_active():
    holdings = [make_holding("VTI", "indexed_equity", 1), make_holding("A", "active", 1),
                make_holding("BND", "bonds", 1), make_holding("CASH", "cash", 1)]
    values = {"VTI": 100.0, "A": 90.0, "BND": 900.0, "CASH": 1910.0}  # total 3000
    t = Targets(name_cap=1.0)
    ok, why = buy_allowed("B", 50.0, holdings, values, t)  # active 140 > indexed 100
    assert not ok and why == "indexed equity would trail active sleeve"


def test_buy_of_index_etf_is_not_a_name(three_k_portfolio):
    assert buy_allowed("VTI", 600.0, *three_k_portfolio, Targets()) == (True, "ok")


def test_fee_cap_violation():
    assert check_instrument(quote(er=0.009), {"longName": "X Fund"}) == ["expense ratio 0.90% >= 0.90% cap"]


def test_fee_just_under_cap_ok_and_none_ok():
    assert check_instrument(quote(er=0.0089), {"longName": "X Fund"}) == []
    assert check_instrument(quote(er=None), {"longName": "X Fund"}) == []


def test_fee_cap_comes_from_targets():
    assert check_instrument(quote(er=0.002), {"longName": "X"}, Targets(fee_cap=0.001)) == [
        "expense ratio 0.20% >= 0.10% cap"]


def test_quote_type_not_allowed():
    assert check_instrument(quote(quote_type="FUTURE"), {"longName": "X"}) == ["quote type FUTURE not allowed"]


def test_leveraged_denied():
    out = check_instrument(quote(), {"longName": "ProShares Leveraged QQQ"})
    assert out == ["denied keyword 'leveraged' (leveraged/inverse product) in name"]


def test_ultrapro_denied_brief_case():
    assert "leveraged" in check_instrument(quote(), {"longName": "ProShares UltraPro QQQ"})[0].lower()


def test_denied_keyword_case_insensitive_and_short_name():
    assert check_instrument(quote(), {"longName": "Daily 3X Bull", "shortName": "X"}) == ["denied keyword '3x' (leveraged/inverse product) in name"]
    assert check_instrument(quote(), {"longName": "", "shortName": "INVERSE S&P"}) == ["denied keyword 'inverse' (leveraged/inverse product) in name"]
    assert check_instrument(quote(), {"longName": "Bear Fund"}) == ["denied keyword 'bear' (leveraged/inverse product) in name"]


def test_denied_keywords_match_whole_words_only():
    assert check_instrument(quote(), {"longName": "Vanguard Short-Term Treasury ETF"}) == []
    assert check_instrument(quote(), {"longName": "Vanguard Short-Term Bond ETF"}) == []


def test_ultra_matches_as_prefix():
    assert check_instrument(quote(), {"longName": "ProShares UltraShort QQQ"}) == [
        "denied keyword 'ultra' (leveraged/inverse product) in name"]


def test_clean_instrument_passes():
    assert check_instrument(quote(), {"longName": "Vanguard Total Stock Market ETF"}) == []


def test_multiple_violations_accumulate():
    out = check_instrument(quote(er=0.02, quote_type="FUTURE"), {"longName": "Inverse Fund"})
    assert len(out) == 3
