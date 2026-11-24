"""Convert the "Oaklink Fund Ledger" workbook (exported from Google Sheets as .xlsx) into the
package's CSV tables, and the tables back into a workbook for upload.

The Sheet is the source of truth Emmet edits; the package only ever reads CSVs. Cell values are
normalised so that what reaches the CSV is what the models expect: ISO dates, plain numbers,
empty strings for blanks. Nothing here validates the rows — the Store does that on load.
"""
from __future__ import annotations

import csv
from datetime import date, datetime
from pathlib import Path

from openpyxl import Workbook, load_workbook

from fundkit.models import Holding, LedgerRow, Snapshot, Targets, WatchItem

# (tab title, csv name, header)
TABS: list[tuple[str, str, list[str]]] = [
    ("Holdings", "holdings", list(Holding.model_fields)),
    ("Targets", "targets", ["key", "value"]),
    ("Watchlist", "watchlist", list(WatchItem.model_fields)),
    ("Decision Ledger", "ledger", list(LedgerRow.model_fields)),
    ("Performance", "performance", list(Snapshot.model_fields)),
]
TARGET_KEYS = list(Targets.model_fields)


def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, bool):
        return str(v).lower()
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else repr(v)
    return str(v).strip()


def import_workbook(xlsx: Path, data_dir: Path) -> dict[str, int]:
    """Write one CSV per tab into `data_dir`; return {csv name: data rows written}.

    Raises ValueError when a tab is missing or its header (first N cells) differs from the model —
    extra columns to the right of the header (notes) are ignored.
    """
    wb = load_workbook(xlsx, read_only=True, data_only=True)
    counts: dict[str, int] = {}
    data_dir.mkdir(parents=True, exist_ok=True)
    for title, name, header in TABS:
        if title not in wb.sheetnames:
            raise ValueError(f"schema mismatch in workbook: tab {title!r} missing")
        rows = [[_cell(c) for c in r] for r in wb[title].iter_rows(values_only=True)]
        if not rows or rows[0][: len(header)] != header:
            found = rows[0][: len(header)] if rows else []
            raise ValueError(f"schema mismatch in tab {title!r}: expected {header}, found {found}")
        body = [r[: len(header)] + [""] * (len(header) - len(r)) for r in rows[1:]]
        body = [r for r in body if any(x != "" for x in r)]
        with (data_dir / f"{name}.csv").open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(header)
            w.writerows(body)
        counts[name] = len(body)
    return counts


def export_workbook(data_dir: Path, xlsx: Path, tables: tuple[str, ...] = ("ledger", "watchlist", "holdings", "performance")) -> None:
    """Write the named CSV tables into a workbook with the Sheet's tab titles (for upload)."""
    wb = Workbook()
    wb.remove(wb.active)
    for title, name, header in TABS:
        if name not in tables:
            continue
        ws = wb.create_sheet(title)
        with (data_dir / f"{name}.csv").open(newline="") as f:
            for row in csv.reader(f):
                ws.append(row)
    wb.save(xlsx)
