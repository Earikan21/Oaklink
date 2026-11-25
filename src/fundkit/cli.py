"""`fundkit` command line: the surface the scheduled task calls."""
import json
import os
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Annotated, Optional

import typer

from fundkit.fake_market import FakeMarket, packaged_example
from fundkit.models import Holding, Targets
from fundkit.market import MarketData, MarketUnavailable, YFMarket
from fundkit.store import Store

app = typer.Typer(add_completion=False, no_args_is_help=True,
                  help="Personal investment policy toolkit (IPS as code).")

DataOpt = Annotated[Optional[Path], typer.Option("--data", help="Data dir (default: $FUNDKIT_DATA or ./data).")]
TodayOpt = Annotated[Optional[str], typer.Option("--today", help="YYYY-MM-DD (default: today).")]


def _data_dir(data: Path | None) -> Path:
    return Path(data) if data is not None else Path(os.environ.get("FUNDKIT_DATA") or "data")


def _today(today: str | None) -> date:
    if today is None:
        return date.today()
    try:
        return date.fromisoformat(today)
    except ValueError:
        raise typer.BadParameter(f"--today must be YYYY-MM-DD, got {today!r}")


def make_market(data_dir: Path) -> MarketData:
    if os.environ.get("FUNDKIT_MARKET") == "fake":
        return FakeMarket.from_json(os.environ.get("FUNDKIT_FAKE_MARKET_JSON") or packaged_example())
    return YFMarket(cache_dir=data_dir / "cache")


def _existing_store(data_dir: Path) -> Store:
    if not data_dir.is_dir():
        typer.echo(f"error: data dir {data_dir} not found; run `fundkit init` first", err=True)
        raise typer.Exit(2)
    return Store(data_dir)


@contextmanager
def _errors():
    """Expected failures become `error: ...` + exit code; anything else is a bug and propagates."""
    try:
        yield
    except (MarketUnavailable, ValueError, FileNotFoundError) as e:
        typer.echo(f"error: {type(e).__name__}: {e}" if not str(e) else f"error: {e}", err=True)
        raise typer.Exit(1)


@app.command()
def init(
    data: DataOpt = None,
    cash: Annotated[Optional[float], typer.Option("--cash", help="Seed a CASH holding (only into an empty book).")] = None,
) -> None:
    """Create the data tables (existing files are never overwritten)."""
    d = _data_dir(data)
    store = Store(d)
    store.init()
    typer.echo(str(d))
    if cash is not None and cash > 0:
        if store.load_holdings():
            typer.echo("holdings already exist; --cash ignored")
        else:
            store.save_holdings([Holding(ticker="CASH", sleeve="cash", shares=cash, cost_basis=1.0,
                                         lot_date=date.today())])
            typer.echo(f"seeded ${cash:,.0f} cash")


@app.command("sheet-import")
def sheet_import(
    xlsx: Annotated[Path, typer.Argument(help="Workbook exported from the Google Sheet (.xlsx).")],
    data: DataOpt = None,
) -> None:
    """Write the workbook's five tabs to data/*.csv (replaces the CSVs; validates headers)."""
    from fundkit.sheet import import_workbook
    with _errors():
        counts = import_workbook(Path(xlsx), _data_dir(data))
    for name, n in counts.items():
        typer.echo(f"{name}.csv: {n} rows")


@app.command("sheet-export")
def sheet_export(
    xlsx: Annotated[Path, typer.Argument(help="Workbook to write (.xlsx) for upload to the Sheet.")],
    data: DataOpt = None,
) -> None:
    """Write ledger, watchlist, holdings and performance CSVs into a workbook (never targets)."""
    from fundkit.sheet import export_workbook
    d = _existing_store(_data_dir(data)).root
    with _errors():
        export_workbook(d, Path(xlsx))
    typer.echo(str(xlsx))


@app.command("feed-dump")
def feed_dump(
    out: Annotated[Path, typer.Option("--out", help="Feed file to write (JSON).")] = Path("feed/market.json"),
    data: DataOpt = None,
    tickers: Annotated[str, typer.Option("--tickers", help="Extra tickers, comma-separated.")] = "",
    tickers_file: Annotated[Optional[Path], typer.Option("--tickers-file", help="File with one ticker per line (# comments ok).")] = None,
    universe_limit: Annotated[int, typer.Option("--universe-limit")] = 30,
) -> None:
    """Fetch market data for the index/bond ETFs, held and watchlist names, extra tickers and the Graham
    universe into one JSON file that `FUNDKIT_MARKET=fake FUNDKIT_FAKE_MARKET_JSON=<file>` can run from.
    Needs network (run it where Yahoo Finance is reachable). Uses the live market even if FUNDKIT_MARKET=fake."""
    from fundkit.feed import dump
    d = _data_dir(data)
    core: list[str] = []
    extra: list[str] = []
    if d.is_dir():
        store = Store(d)
        t = store.load_targets()
        core += [t.index_etf, t.bond_etf]
        core += [h.ticker for h in store.load_holdings() if h.ticker != "CASH"]
        try:
            extra += [w.ticker for w in store.load_watchlist()]
        except ValueError:
            pass
    else:  # no data dir (e.g. the GitHub Action): the IPS defaults still name the two ETFs
        t = Targets()
        core += [t.index_etf, t.bond_etf]
    extra += [x.strip().upper() for x in tickers.split(",") if x.strip()]
    if tickers_file and tickers_file.exists():
        for line in tickers_file.read_text().splitlines():
            line = line.split("#", 1)[0].strip().upper()
            if line:
                extra.append(line)
    market = YFMarket(cache_dir=(d if d.is_dir() else out.parent) / "cache", ttl_hours=0)
    with _errors():
        meta = dump(market, core, extra, out, universe_limit=universe_limit, log=lambda m: typer.echo(m, err=True))
    typer.echo(f"wrote {out}: {len(meta['core'])} core, {len(meta['extra'])} extra, "
               f"{len(meta['universe'])} universe, {len(meta['errors'])} errors")
    for e in meta["errors"][:20]:
        typer.echo(f"  {e}", err=True)


@app.command("reconcile")
def reconcile_cmd(today: TodayOpt = None, data: DataOpt = None) -> None:
    """Apply filled ledger rows to holdings and expire stale pending rows."""
    from fundkit.reconcile import reconcile
    store = _existing_store(_data_dir(data))
    with _errors():
        r = reconcile(store, _today(today))
    for name in ("applied", "carried", "dropped", "incomplete", "warnings"):
        items = getattr(r, name)
        typer.echo(f"{name}: {len(items)}" + (f" ({', '.join(map(str, items))})" if items else ""))


@app.command()
def run(
    today: TodayOpt = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Write nothing (reconcile still runs).")] = False,
    review: Annotated[Optional[bool], typer.Option("--review/--no-review", help="Force/suppress review run.")] = None,
    notes: Annotated[Optional[Path], typer.Option("--notes", help="JSON dict of AI notes keyed '1'..'7'.")] = None,
    out: Annotated[Optional[Path], typer.Option("--out", help="Write the memo to this file.")] = None,
    json_out: Annotated[Optional[Path], typer.Option("--json", help="Write the run result as JSON.")] = None,
    data: DataOpt = None,
) -> None:
    """Run the weekly cycle and print the memo."""
    from fundkit.run import weekly_run
    d = _data_dir(data)
    store = _existing_store(d)
    with _errors():
        ai_notes = json.loads(notes.read_text()) if notes is not None else None
        if ai_notes is not None and not isinstance(ai_notes, dict):
            raise ValueError(f"{notes} must contain a JSON object")
        result = weekly_run(store, make_market(d), _today(today), dry_run=dry_run,
                            force_review=review, ai_notes=ai_notes)
        typer.echo(result.memo_md)
        if out is not None:
            out.write_text(result.memo_md)
        if json_out is not None:
            json_out.write_text(json.dumps({
                "memo_md": result.memo_md, "review": result.review, "written": result.written,
                "actions": result.actions.model_dump(mode="json"),
                "snapshot": result.snapshot.model_dump(mode="json"),
            }, indent=2))


@app.command()
def screen(
    limit: Annotated[int, typer.Option("--limit", help="Max tickers to evaluate.")] = 20,
    data: DataOpt = None,
) -> None:
    """Print the screened universe with quality results."""
    from fundkit.screen import quality_ok, universe
    d = _data_dir(data)
    with _errors():
        market = make_market(d)
        tickers = universe(market)[:limit]
        typer.echo(f"{'ticker':<10}{'quality ok':<12}reasons")
        for t in tickers:
            try:
                ok, reasons = quality_ok(t, market)
            except MarketUnavailable:
                ok, reasons = False, ["data unavailable"]
            typer.echo(f"{t:<10}{'yes' if ok else 'no':<12}{'; '.join(reasons)}")


@app.command()
def value(
    ticker: str,
    peers: Annotated[Optional[str], typer.Option("--peers", help="Comma-separated peer tickers.")] = None,
    data: DataOpt = None,
) -> None:
    """Value one company and print the summary."""
    from fundkit.research.valuation import value_company
    from fundkit.screen import peers_for
    d = _data_dir(data)
    with _errors():
        market = make_market(d)
        peer_list = [p.strip() for p in peers.split(",") if p.strip()] if peers else peers_for(ticker, market)
        v = value_company(ticker, market, peer_list)
    f = lambda x: "n/a" if x is None else f"{x:.2f}"  # noqa: E731
    typer.echo(f"{v.ticker}  price {v.price:.2f}")
    typer.echo(f"blended: {v.blended:.2f}")
    typer.echo(f"dcf: {f(v.dcf)}")
    typer.echo(f"relative: {f(v.relative)}")
    typer.echo(f"upside: {v.upside:.1%}")
    typer.echo(f"confidence: {v.confidence}")
    typer.echo(f"flags: {', '.join(v.flags) if v.flags else 'none'}")
