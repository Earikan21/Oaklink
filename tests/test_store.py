from datetime import date

import pytest

from fundkit.models import Holding, LedgerRow, Snapshot, Targets, WatchItem
from fundkit.store import Store


def test_roundtrip_holdings(tmp_path):
    s = Store(tmp_path); s.init()
    rows = [Holding(ticker="VTI", sleeve="indexed_equity", shares=5.25, cost_basis=285.0, lot_date=date(2026,11,22))]
    s.save_holdings(rows)
    assert s.load_holdings() == rows

def test_init_does_not_overwrite(tmp_path):
    s = Store(tmp_path); s.init()
    s.save_targets(Targets(index_etf="ITOT"))
    s.init()
    assert s.load_targets().index_etf == "ITOT"

def test_append_ledger_preserves_order(tmp_path):
    s = Store(tmp_path); s.init()
    s.append_ledger([LedgerRow(id="a", date=date(2026,11,22), action="NOTE", ticker="", amount=0, rationale="x", ips_check="PASS")])
    s.append_ledger([LedgerRow(id="b", date=date(2026,11,29), action="NOTE", ticker="", amount=0, rationale="y", ips_check="PASS")])
    assert [r.id for r in s.load_ledger()] == ["a","b"]


def test_init_seeds_default_targets(tmp_path):
    s = Store(tmp_path); s.init()
    assert s.load_targets() == Targets()

def test_init_without_examples_writes_empty_tables(tmp_path):
    s = Store(tmp_path); s.init(from_examples=False)
    assert s.load_holdings() == []
    assert s.load_watchlist() == []
    assert s.load_ledger() == []
    assert s.load_performance() == []
    assert s.load_targets() == Targets()

def test_save_holdings_overwrites_previous_rows(tmp_path):
    s = Store(tmp_path); s.init()
    first = Holding(ticker="VTI", sleeve="indexed_equity", shares=1.0, cost_basis=1.0, lot_date=date(2026,11,1))
    second = Holding(ticker="BND", sleeve="bonds", shares=2.0, cost_basis=2.0, lot_date=date(2026,11,2))
    s.save_holdings([first])
    s.save_holdings([second])
    assert s.load_holdings() == [second]

def test_targets_round_trip_keeps_types(tmp_path):
    s = Store(tmp_path); s.init()
    t = Targets(stocks=0.6, bonds=0.25, cash=0.15, review_every=4, index_etf="ITOT")
    s.save_targets(t)
    loaded = s.load_targets()
    assert loaded == t
    assert isinstance(loaded.review_every, int)

def test_load_targets_rejects_unknown_key(tmp_path):
    s = Store(tmp_path); s.init()
    (tmp_path / "targets.csv").write_text("key,value\nstokcs,0.6\n", encoding="utf-8")
    with pytest.raises(ValueError, match="stokcs"):
        s.load_targets()

def test_watchlist_round_trips_none_and_values(tmp_path):
    s = Store(tmp_path); s.init()
    rows = [
        WatchItem(ticker="ACME", thesis="cash generator", last_valued=None, fair_value=None, upside=None, status="candidate"),
        WatchItem(ticker="BETA", thesis="a, \"quoted\" thesis", last_valued=date(2026,11,15), fair_value=42.5, upside=0.31, status="held"),
    ]
    s.save_watchlist(rows)
    assert s.load_watchlist() == rows

def test_ledger_round_trips_optional_fields_and_free_text(tmp_path):
    s = Store(tmp_path); s.init()
    row = LedgerRow(
        id="c", date=date(2026,11,22), action="BUY", ticker="VTI", amount=1500.0,
        rationale="line one, with a comma\nline two \"quoted\"", ips_check="PASS",
        response="done", fill_shares=5.25, fill_price=285.0, est_tax=0.0,
    )
    s.append_ledger([row])
    assert s.load_ledger() == [row]

def test_ledger_round_trips_empty_optional_fields(tmp_path):
    s = Store(tmp_path); s.init()
    row = LedgerRow(id="d", date=date(2026,11,22), action="NOTE", ticker="", amount=0, rationale="", ips_check="FAIL")
    s.append_ledger([row])
    loaded = s.load_ledger()[0]
    assert loaded == row
    assert loaded.fill_shares is None and loaded.est_tax is None

def test_append_ledger_creates_header_when_file_missing(tmp_path):
    s = Store(tmp_path)
    row = LedgerRow(id="e", date=date(2026,11,22), action="NOTE", ticker="", amount=0, rationale="x", ips_check="PASS")
    s.append_ledger([row])
    assert s.load_ledger() == [row]

def test_performance_round_trips_and_keeps_order(tmp_path):
    s = Store(tmp_path); s.init()
    a = Snapshot(date=date(2026,11,22), total=100.0, indexed_equity=50.0, active=20.0, bonds=20.0, cash=10.0,
                 bench_total=100.0, active_vs_index=None, turnover_qtd=0.0)
    b = Snapshot(date=date(2026,11,29), total=103.5, indexed_equity=51.0, active=21.5, bonds=20.0, cash=11.0,
                 bench_total=101.0, active_vs_index=0.02, turnover_qtd=0.01)
    s.append_performance(a)
    s.append_performance(b)
    assert s.load_performance() == [a, b]

def test_save_ledger_rewrites_all_rows(tmp_path):
    s = Store(tmp_path); s.init()
    a = LedgerRow(id="a", date=date(2026,11,22), action="NOTE", ticker="", amount=0, rationale="x", ips_check="PASS")
    b = LedgerRow(id="b", date=date(2026,11,29), action="BUY", ticker="VTI", amount=10, rationale="y", ips_check="PASS")
    s.append_ledger([a, b])
    s.save_ledger([a, b.model_copy(update={"response": "skipped"})])
    assert [(r.id, r.response) for r in s.load_ledger()] == [("a", "pending"), ("b", "skipped")]


# ---------------------------------------------------------------- C3: schema checks and atomic rewrites
LEDGER_HEADER = "id,date,action,ticker,amount,rationale,ips_check,response,fill_shares,fill_price,est_tax"


def _note(id_: str) -> LedgerRow:
    return LedgerRow(id=id_, date=date(2026, 11, 22), action="NOTE", ticker="", amount=0, rationale="x",
                     ips_check="PASS")


def test_append_to_header_only_file_without_trailing_newline(tmp_path):
    s = Store(tmp_path)
    (tmp_path / "ledger.csv").write_text(LEDGER_HEADER, encoding="utf-8")  # as a Sheet export may leave it
    s.append_ledger([_note("a")])
    assert s.load_ledger() == [_note("a")]
    assert (tmp_path / "ledger.csv").read_text(encoding="utf-8").splitlines()[0] == LEDGER_HEADER


def test_append_to_rows_without_trailing_newline_keeps_rows_separate(tmp_path):
    s = Store(tmp_path)
    s.append_ledger([_note("a")])
    p = tmp_path / "ledger.csv"
    p.write_bytes(p.read_bytes().rstrip(b"\n"))
    s.append_ledger([_note("b")])
    assert [r.id for r in s.load_ledger()] == ["a", "b"]


@pytest.mark.parametrize("header", [
    "date,id,action,ticker,amount,rationale,ips_check,response,fill_shares,fill_price,est_tax",  # reordered
    LEDGER_HEADER + ",extra",                                                                     # extra column
    "id,date,action,ticker,amount,rationale,ips_check,response,fill_shares,fill_price",           # missing column
])
def test_schema_mismatch_raises_value_error_on_read_and_append(tmp_path, header):
    s = Store(tmp_path)
    p = tmp_path / "ledger.csv"
    p.write_text(header + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"^schema mismatch in .*ledger\.csv: expected \['id', 'date'"):
        s.load_ledger()
    with pytest.raises(ValueError, match="schema mismatch"):
        s.append_ledger([_note("a")])
    assert p.read_text(encoding="utf-8") == header + "\n"  # nothing written


def test_row_with_extra_field_is_value_error_not_key_error(tmp_path):
    s = Store(tmp_path)
    (tmp_path / "ledger.csv").write_text(
        LEDGER_HEADER + "\na,2026-11-22,NOTE,,0,x,PASS,pending,,,,surplus\n", encoding="utf-8")
    with pytest.raises(ValueError, match="schema mismatch"):
        s.load_ledger()


def test_row_with_missing_field_is_value_error(tmp_path):
    s = Store(tmp_path)
    (tmp_path / "ledger.csv").write_text(LEDGER_HEADER + "\na,2026-11-22,NOTE\n", encoding="utf-8")
    with pytest.raises(ValueError, match="schema mismatch"):
        s.load_ledger()


def test_append_preserves_existing_unvalidated_rows_verbatim(tmp_path):
    s = Store(tmp_path)
    s.append_ledger([_note("a")])
    p = tmp_path / "ledger.csv"
    corrupt = p.read_bytes() + b"bad,notadate,BUY,VTI,1,x,PASS,pending,,,\n"
    p.write_bytes(corrupt)
    s.append_ledger([_note("b")])
    assert p.read_bytes().startswith(corrupt)
    assert p.read_bytes().splitlines()[-1].split(b",")[0] == b"b"


def test_writes_leave_no_temp_files(tmp_path):
    s = Store(tmp_path)
    s.init()
    s.append_ledger([_note("a")])
    s.save_holdings([])
    assert sorted(x.name for x in tmp_path.iterdir()) == [
        "holdings.csv", "ledger.csv", "performance.csv", "targets.csv", "watchlist.csv"]


def test_load_targets_wrong_header_is_value_error(tmp_path):
    s = Store(tmp_path)
    s.init()
    (tmp_path / "targets.csv").write_text("name,val\nstocks,0.5\n", encoding="utf-8")
    with pytest.raises(ValueError, match="schema mismatch"):
        s.load_targets()
