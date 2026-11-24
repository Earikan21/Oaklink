from datetime import date, datetime
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook
from typer.testing import CliRunner

from fundkit.cli import app
from fundkit.sheet import TABS, export_workbook, import_workbook
from fundkit.store import Store


def _workbook(path: Path, holdings_rows=(), ledger_rows=(), mutate=None) -> Path:
    wb = Workbook(); wb.remove(wb.active)
    for title, name, header in TABS:
        ws = wb.create_sheet(title); ws.append(header)
        if name == "targets":
            for k, v in [("stocks", 0.5), ("bonds", 0.3), ("cash", 0.2), ("band", 0.05), ("active_ceiling", 0.3),
                         ("name_cap", 0.05), ("trim_threshold", 0.075), ("review_trim", 0.06), ("fee_cap", 0.009),
                         ("min_upside", 0.25), ("max_corr", 0.8), ("max_spread", 0.005), ("reval_move", 0.15),
                         ("review_every", 6), ("index_etf", "VTI"), ("bond_etf", "BND")]:
                ws.append([k, v])
        if name == "holdings":
            ws["G1"] = "a note to the right of the header"
            for r in holdings_rows: ws.append(list(r))
        if name == "ledger":
            for r in ledger_rows: ws.append(list(r))
    if mutate: mutate(wb)
    wb.save(path); return path


def test_import_writes_normalised_csvs_and_store_loads_them(tmp_path):
    xlsx = _workbook(tmp_path / "s.xlsx",
                     holdings_rows=[("CASH", "cash", 3000, 1, datetime(2026, 11, 24)),
                                    ("VTI", "indexed_equity", 5.0, 300.5, date(2026, 11, 22))],
                     ledger_rows=[("20261122-00-BUY-VTI", date(2026, 11, 22), "BUY", "VTI", 1500, "x", "PASS",
                                   "done", 5, 300.5, None)])
    counts = import_workbook(xlsx, tmp_path / "data")
    assert counts == {"holdings": 2, "targets": 16, "watchlist": 0, "ledger": 1, "performance": 0}
    text = (tmp_path / "data" / "holdings.csv").read_text()
    assert "CASH,cash,3000,1,2026-11-24" in text and "VTI,indexed_equity,5,300.5,2026-11-22" in text
    s = Store(tmp_path / "data")
    assert s.load_targets().review_every == 6 and s.load_targets().index_etf == "VTI"
    assert s.load_ledger()[0].fill_price == 300.5 and s.load_ledger()[0].est_tax is None
    assert [h.ticker for h in s.load_holdings()] == ["CASH", "VTI"]


def test_import_rejects_moved_column_and_missing_tab(tmp_path):
    def swap(wb):
        ws = wb["Decision Ledger"]; ws["A1"], ws["B1"] = "date", "id"
    with pytest.raises(ValueError, match="schema mismatch in tab 'Decision Ledger'"):
        import_workbook(_workbook(tmp_path / "a.xlsx", mutate=swap), tmp_path / "d1")
    def drop(wb): wb.remove(wb["Watchlist"])
    with pytest.raises(ValueError, match="tab 'Watchlist' missing"):
        import_workbook(_workbook(tmp_path / "b.xlsx", mutate=drop), tmp_path / "d2")


def test_export_roundtrip_excludes_targets(tmp_path):
    import_workbook(_workbook(tmp_path / "s.xlsx", holdings_rows=[("CASH", "cash", 3000, 1, "2026-11-24")]), tmp_path / "data")
    export_workbook(tmp_path / "data", tmp_path / "out.xlsx")
    wb = load_workbook(tmp_path / "out.xlsx")
    assert wb.sheetnames == ["Holdings", "Watchlist", "Decision Ledger", "Performance"]
    assert [c.value for c in wb["Holdings"][2]] == ["CASH", "cash", "3000", "1", "2026-11-24"]


def test_cli_sheet_import_reports_schema_error(tmp_path):
    def swap(wb):
        ws = wb["Holdings"]; ws["A1"], ws["B1"] = "sleeve", "ticker"
    res = CliRunner().invoke(app, ["sheet-import", str(_workbook(tmp_path / "s.xlsx", mutate=swap)), "--data", str(tmp_path / "d")])
    assert res.exit_code == 1 and "error: schema mismatch" in (res.output + (res.stderr or ""))
    ok = CliRunner().invoke(app, ["sheet-import", str(_workbook(tmp_path / "t.xlsx")), "--data", str(tmp_path / "d")])
    assert ok.exit_code == 0 and "targets.csv: 16 rows" in ok.output
