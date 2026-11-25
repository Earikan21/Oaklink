"""The feed is the offline market that the weekly run uses where Yahoo Finance is unreachable: a dump
from a source market must reproduce that market's answers for every method the run calls."""
import json
from datetime import date

import pandas as pd
import pytest
from typer.testing import CliRunner

from fundkit.cli import app
from fundkit.fake_market import FakeMarket
from fundkit.feed import dump, load, screen_key
from fundkit.market import MarketUnavailable
from fundkit.models import Quote
from fundkit.screen import MAX_PEERS, UNIVERSE_QUERY
from tests.conftest import make_history


def _q(t, price, qt="EQUITY"):
    return Quote(ticker=t, price=price, bid=price - 0.01, ask=price + 0.01, expense_ratio=0.0003 if qt == "ETF" else None,
                 nav=price if qt == "ETF" else None, quote_type=qt, avg_volume=1e6)


class _Source(FakeMarket):
    """FakeMarket whose screen honours industry queries, like YFMarket does."""
    def screen(self, query):
        if "sector" in query:
            return [t for t, e in self.data.items() if isinstance(e, dict) and (e.get("info") or {}).get("sector") == query["sector"]]
        return list(self.data["__screen__"])


@pytest.fixture
def source():
    stmt = pd.DataFrame({"2025-12-31": [100.0, 20.0], "2024-12-31": [90.0, 18.0]}, index=["Total Revenue", "Operating Income"])
    return _Source({
        "VTI": {"quote": _q("VTI", 300.0, "ETF"), "info": {"longName": "Vanguard Total Stock Market ETF", "quoteType": "ETF", "netExpenseRatio": 0.03},
                "history": make_history(300.0, 400)},
        "BND": {"quote": _q("BND", 75.0, "ETF"), "info": {"longName": "Vanguard Total Bond Market ETF", "quoteType": "ETF", "netExpenseRatio": 0.03},
                "history": make_history(75.0, 400)},
        "ACME": {"quote": _q("ACME", 50.0), "info": {"longName": "Acme", "quoteType": "EQUITY", "sector": "Industrials", "marketCap": 5e8, "beta": 1.1, "nan_field": float("nan")},
                 "history": make_history(50.0, 300, drift=0.001), "income_stmt": stmt,
                 "earnings_dates": pd.DataFrame({"EPS Estimate": [1.0], "Reported EPS": [1.1], "Surprise(%)": [10.0]},
                                                index=pd.DatetimeIndex([pd.Timestamp("2026-10-30 16:30", tz="America/New_York")]))},
        "PEER": {"quote": _q("PEER", 40.0), "info": {"longName": "Peer", "quoteType": "EQUITY", "sector": "Industrials"}},
        "__screen__": ["ACME", "NOPE"],
    })


def test_dump_then_load_reproduces_quotes_frames_and_peer_screens(tmp_path, source):
    meta = dump(source, core=["VTI", "BND"], extra=[], out=tmp_path / "f.json", log=lambda m: None)
    m = FakeMarket(load(tmp_path / "f.json"))
    assert m.quote("VTI") == source.quote("VTI") and m.info("ACME")["sector"] == "Industrials"
    assert m.info("ACME")["nan_field"] is None
    pd.testing.assert_frame_equal(m.history("VTI"), source.history("VTI"), check_freq=False)
    pd.testing.assert_frame_equal(m.income_stmt("ACME"), source.income_stmt("ACME"))
    assert m.earnings_dates("ACME").index[0].to_pydatetime().hour in (16, 20)  # kept as a timestamp, tz may be normalised
    assert m.screen(UNIVERSE_QUERY) == ["ACME", "NOPE"]
    from fundkit.screen import peer_query
    assert m.screen(peer_query(source.info("ACME"))) == ["PEER"]
    assert m.quote("PEER").price == 40.0 and "PEER" not in meta["universe"]
    assert any(e.startswith("NOPE.quote") for e in meta["errors"])
    assert any(e.startswith("^TNX.quote") for e in meta["errors"])  # always requested, absent from this source
    with pytest.raises(MarketUnavailable):
        m.quote("NOPE")
    with pytest.raises(MarketUnavailable):
        m.cashflow("ACME")  # absent from the source → absent from the feed → fails closed


def test_load_still_reads_the_packaged_example_format(tmp_path):
    from fundkit.fake_market import packaged_example
    m = FakeMarket.from_json(packaged_example())
    assert m.quote("VTI").price > 0 and len(m.history("VTI")) >= 2


def test_cli_feed_dump_uses_store_tickers_and_tickers_file(tmp_path, monkeypatch, source):
    from fundkit.store import Store
    s = Store(tmp_path / "data"); s.init()
    from fundkit.models import Holding
    s.save_holdings([Holding(ticker="CASH", sleeve="cash", shares=3000, cost_basis=1, lot_date=date(2026, 11, 24)),
                     Holding(ticker="ACME", sleeve="active", shares=1, cost_basis=50, lot_date=date(2026, 11, 24))])
    (tmp_path / "t.txt").write_text("peer # comment\n\n")
    monkeypatch.setattr("fundkit.cli.YFMarket", lambda cache_dir, ttl_hours=20: source)
    res = CliRunner().invoke(app, ["feed-dump", "--data", str(tmp_path / "data"), "--out", str(tmp_path / "feed.json"),
                                   "--tickers-file", str(tmp_path / "t.txt"), "--universe-limit", "1"])
    assert res.exit_code == 0, res.output
    meta = json.loads((tmp_path / "feed.json").read_text())["__meta__"]
    assert meta["core"] == ["VTI", "BND", "ACME"] and meta["extra"] == ["PEER"] and meta["universe"] == ["ACME"]
    assert "3 core, 1 extra, 1 universe" in res.output


def test_weekly_run_works_from_a_feed_file(tmp_path, source, store_with_cash_3000):
    from fundkit.run import weekly_run
    dump(source, core=["VTI", "BND"], extra=[], out=tmp_path / "f.json", log=lambda m: None)
    r = weekly_run(store_with_cash_3000, FakeMarket.from_json(tmp_path / "f.json"), date(2026, 11, 28), dry_run=True, force_review=True)
    assert [(p.action, p.ticker) for p in r.actions.proposals] == [("BUY", "VTI"), ("BUY", "BND")]


def test_cli_feed_dump_without_data_dir_still_covers_index_and_bond(tmp_path, monkeypatch, source):
    monkeypatch.setattr("fundkit.cli.YFMarket", lambda cache_dir, ttl_hours=20: source)
    res = CliRunner().invoke(app, ["feed-dump", "--data", str(tmp_path / "nope"), "--out", str(tmp_path / "f.json"),
                                   "--universe-limit", "1"])
    assert res.exit_code == 0, res.output
    assert json.loads((tmp_path / "f.json").read_text())["__meta__"]["core"] == ["VTI", "BND"]
