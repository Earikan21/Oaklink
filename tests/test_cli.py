import json
from datetime import date

import pytest
from typer.testing import CliRunner

from fundkit import cli
from fundkit.cli import app
from fundkit.fake_market import FakeMarket, packaged_example
from fundkit.models import Holding
from fundkit.store import Store

runner = CliRunner()
TODAY = "2026-11-29"


@pytest.fixture
def fake_env(monkeypatch):
    monkeypatch.setenv("FUNDKIT_MARKET", "fake")
    monkeypatch.delenv("FUNDKIT_FAKE_MARKET_JSON", raising=False)
    monkeypatch.delenv("FUNDKIT_DATA", raising=False)


@pytest.fixture
def data_dir(tmp_path, fake_env):
    """Initialised store seeded with $3000 cash (an empty book has nothing to allocate)."""
    d = tmp_path / "data"
    assert runner.invoke(app, ["init", "--data", str(d)]).exit_code == 0
    Store(d).save_holdings([Holding(ticker="CASH", sleeve="cash", shares=3000.0, cost_basis=1.0,
                                    lot_date=date(2026, 11, 22))])
    return d


def test_init_creates_tables(tmp_path):
    result = runner.invoke(app, ["init", "--data", str(tmp_path)])
    assert result.exit_code == 0 and (tmp_path / "targets.csv").exists()
    assert str(tmp_path) in result.stdout


def test_run_dry_run_prints_memo(data_dir):
    result = runner.invoke(app, ["run", "--dry-run", "--review", "--today", TODAY, "--data", str(data_dir)])
    assert result.exit_code == 0, result.output
    assert "## 6. Actions" in result.stdout
    assert "BUY — VTI — $1,500" in result.stdout and "BUY — BND — $900 (12 sh)" in result.stdout


def test_run_exit_1_when_index_etf_unpriceable(data_dir, tmp_path, monkeypatch):
    empty = tmp_path / "empty.json"
    empty.write_text("{}")
    monkeypatch.setenv("FUNDKIT_FAKE_MARKET_JSON", str(empty))
    result = runner.invoke(app, ["run", "--dry-run", "--today", TODAY, "--data", str(data_dir)])
    assert result.exit_code == 1
    assert "error:" in result.output and "Traceback" not in result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)


def test_value_prints_blended(monkeypatch, fake_market_acme, tmp_path):
    monkeypatch.setattr(cli, "make_market", lambda data: fake_market_acme)
    result = runner.invoke(app, ["value", "ACME", "--peers", "P1,P2,P3", "--data", str(tmp_path)])
    assert result.exit_code == 0, result.output
    for word in ("blended", "dcf", "relative", "upside", "confidence", "flags"):
        assert word in result.stdout


def test_fundkit_data_env_honoured(tmp_path, monkeypatch):
    target = tmp_path / "envdata"
    monkeypatch.setenv("FUNDKIT_DATA", str(target))
    assert runner.invoke(app, ["init"]).exit_code == 0
    assert (target / "targets.csv").exists()


def test_data_flag_overrides_env(tmp_path, monkeypatch):
    monkeypatch.setenv("FUNDKIT_DATA", str(tmp_path / "env"))
    flag = tmp_path / "flag"
    assert runner.invoke(app, ["init", "--data", str(flag)]).exit_code == 0
    assert (flag / "targets.csv").exists() and not (tmp_path / "env").exists()


def test_run_json_out_and_notes(data_dir, tmp_path):
    notes = tmp_path / "notes.json"
    notes.write_text(json.dumps({"6": "UNIQUE-NOTE-TEXT"}))
    out, js = tmp_path / "memo.md", tmp_path / "run.json"
    result = runner.invoke(app, ["run", "--dry-run", "--review", "--today", TODAY, "--data", str(data_dir),
                                 "--notes", str(notes), "--out", str(out), "--json", str(js)])
    assert result.exit_code == 0, result.output
    assert "## 6. Actions" in out.read_text()
    payload = json.loads(js.read_text())
    assert {"memo_md", "review", "written", "actions", "snapshot"} <= set(payload)
    assert "proposals" in payload["actions"] and "total" in payload["snapshot"]
    assert payload["review"] is True and payload["written"] is False
    assert "context" not in payload


def test_notes_content_appears_in_memo(data_dir, tmp_path):
    base = runner.invoke(app, ["run", "--dry-run", "--review", "--today", TODAY, "--data", str(data_dir)])
    notes = tmp_path / "n.json"
    notes.write_text(json.dumps({"6": "UNIQUE-NOTE-TEXT"}))
    result = runner.invoke(app, ["run", "--dry-run", "--review", "--today", TODAY, "--data", str(data_dir),
                                 "--notes", str(notes)])
    assert result.exit_code == 0, result.output
    assert "UNIQUE-NOTE-TEXT" in result.stdout and "UNIQUE-NOTE-TEXT" not in base.stdout


def test_run_out_writes_memo(data_dir, tmp_path):
    out = tmp_path / "sub-memo.md"
    result = runner.invoke(app, ["run", "--dry-run", "--today", TODAY, "--data", str(data_dir), "--out", str(out)])
    assert result.exit_code == 0 and "## 6. Actions" in out.read_text()


def test_missing_data_dir_exits_2(tmp_path, fake_env):
    result = runner.invoke(app, ["run", "--dry-run", "--data", str(tmp_path / "nope")])
    assert result.exit_code == 2
    assert "fundkit init" in result.output and "Traceback" not in result.output


def test_reconcile_prints_counts(data_dir):
    result = runner.invoke(app, ["reconcile", "--today", TODAY, "--data", str(data_dir)])
    assert result.exit_code == 0, result.output
    for word in ("applied", "carried", "dropped", "incomplete", "warnings"):
        assert word in result.stdout


def test_screen_prints_fake_universe(tmp_path, data_dir, monkeypatch):
    fm = tmp_path / "fm.json"
    fm.write_text(json.dumps({"AAA": {}, "BBB": {}, "__screen__": ["AAA", "BBB", "CCC"]}))
    monkeypatch.setenv("FUNDKIT_FAKE_MARKET_JSON", str(fm))
    result = runner.invoke(app, ["screen", "--limit", "2", "--data", str(data_dir)])
    assert result.exit_code == 0, result.output
    assert "AAA" in result.stdout and "BBB" in result.stdout and "CCC" not in result.stdout
    assert "ticker" in result.stdout


def test_from_json_round_trips_packaged_example():
    m = FakeMarket.from_json(packaged_example())
    assert m.quote("VTI").price == 300.0 and m.quote("BND").price == 75.0
    assert m.quote("VTI").nav == 299.8
    h = m.history("VTI")
    assert len(h) == 300 and (h["Close"] == 300.0).all()
    assert (h["Open"] == h["High"]).all() and (h["Low"] == h["Close"]).all()
    assert m.info("VTI")["netExpenseRatio"] == 0.03
    assert m.screen({}) == []


def test_init_cash_then_run_proposes_first_run_buys(tmp_path, fake_env):
    d = tmp_path / "d"
    r = runner.invoke(app, ["init", "--data", str(d), "--cash", "3000"])
    assert r.exit_code == 0 and "seeded $3,000 cash" in r.stdout
    out = runner.invoke(app, ["run", "--dry-run", "--review", "--today", TODAY, "--data", str(d)]).stdout
    assert "BUY — VTI — $1,500" in out and "BUY — BND — $900 (12 sh)" in out


def test_init_cash_does_not_touch_existing_holdings(data_dir):
    before = Store(data_dir).load_holdings()
    r = runner.invoke(app, ["init", "--data", str(data_dir), "--cash", "9999"])
    assert r.exit_code == 0 and "ignored" in r.stdout
    assert Store(data_dir).load_holdings() == before
