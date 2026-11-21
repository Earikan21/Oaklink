import csv
import os
import tempfile
from importlib.resources import files
from pathlib import Path
from typing import get_args

from fundkit.models import Holding, LedgerRow, Snapshot, Targets, WatchItem

HOLDINGS = "holdings.csv"
TARGETS = "targets.csv"
WATCHLIST = "watchlist.csv"
LEDGER = "ledger.csv"
PERFORMANCE = "performance.csv"

TABLES = {
    HOLDINGS: Holding,
    WATCHLIST: WatchItem,
    LEDGER: LedgerRow,
    PERFORMANCE: Snapshot,
}


def _allows_none(annotation) -> bool:
    return type(None) in get_args(annotation)


def _fields(model) -> list[str]:
    return list(model.model_fields)


def _read_raw(path: Path, model) -> list[dict[str, str]]:
    """Rows as raw strings, after checking the header against `model`. A missing or empty file has no rows.

    Any header other than exactly the model's fields, in order, and any row with a different number of
    fields, raises ValueError("schema mismatch in ..."): a Sheet column was added, dropped or moved.
    """
    if not path.exists() or path.stat().st_size == 0:
        return []
    expected = _fields(model)
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if header is None:
            return []
        if header != expected:
            raise ValueError(f"schema mismatch in {path}: expected {expected}, found {header}")
        rows = []
        for n, rec in enumerate(reader, start=2):
            if not rec:  # blank line
                continue
            if len(rec) != len(expected):
                raise ValueError(f"schema mismatch in {path}: row {n} has {len(rec)} fields, "
                                 f"expected {len(expected)}")
            rows.append(dict(zip(expected, rec)))
    return rows


def _read_table(path: Path, model):
    rows = []
    for raw in _read_raw(path, model):
        row = {
            key: (None if value == "" and _allows_none(model.model_fields[key].annotation) else value)
            for key, value in raw.items()
        }
        rows.append(model.model_validate(row))
    return rows


def _csv_writer(f, model):
    return csv.DictWriter(f, fieldnames=_fields(model), lineterminator="\n")


def _atomic_write_rows(path: Path, model, rows: list[dict]) -> None:
    """Write header + `rows` (dicts keyed by field) to a temp file beside `path`, then os.replace it in."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
            writer = _csv_writer(f, model)
            writer.writeheader()
            writer.writerows(rows)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


class Store:
    """CSV-backed state for the fund: one file per table under `root`."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def init(self, from_examples: bool = True) -> None:
        """Create any missing table files under `root`. Existing files are never overwritten."""
        self.root.mkdir(parents=True, exist_ok=True)
        examples = files("fundkit") / "examples"
        for name, model in TABLES.items():
            dest = self.root / name
            if dest.exists():
                continue
            if from_examples:
                dest.write_bytes(examples.joinpath(name).read_bytes())
            else:
                self._write_table(dest, model, [])
        targets_path = self.root / TARGETS
        if not targets_path.exists():
            if from_examples:
                targets_path.write_bytes(examples.joinpath(TARGETS).read_bytes())
            else:
                self.save_targets(Targets())

    def _write_table(self, path: Path, model, rows) -> None:
        """Full rewrite. An existing file must have the expected header (never silently re-shape a table)."""
        if path.exists():
            _read_raw(path, model)  # header / row-shape check only
        _atomic_write_rows(path, model, [row.model_dump(mode="json") for row in rows])

    def _append_table(self, path: Path, model, rows) -> None:
        """Existing rows (kept verbatim, even ones that would not validate) + `rows`, rewritten atomically."""
        existing = _read_raw(path, model)
        _atomic_write_rows(path, model, existing + [row.model_dump(mode="json") for row in rows])

    def load_holdings(self) -> list[Holding]:
        return _read_table(self.root / HOLDINGS, Holding)

    def save_holdings(self, holdings: list[Holding]) -> None:
        self._write_table(self.root / HOLDINGS, Holding, holdings)

    def load_targets(self) -> Targets:
        path = self.root / TARGETS
        with path.open(newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames != ["key", "value"]:
                raise ValueError(f"schema mismatch in {path}: expected ['key', 'value'], found {reader.fieldnames}")
            pairs = {row["key"]: row["value"] for row in reader}
        unknown = set(pairs) - set(Targets.model_fields)
        if unknown:
            raise ValueError(f"unknown keys in {TARGETS}: {sorted(unknown)}")
        return Targets.model_validate(pairs)

    def save_targets(self, targets: Targets) -> None:
        with (self.root / TARGETS).open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, lineterminator="\n")
            writer.writerow(["key", "value"])
            for key, value in targets.model_dump(mode="json").items():
                writer.writerow([key, value])

    def load_watchlist(self) -> list[WatchItem]:
        return _read_table(self.root / WATCHLIST, WatchItem)

    def save_watchlist(self, items: list[WatchItem]) -> None:
        self._write_table(self.root / WATCHLIST, WatchItem, items)

    def load_ledger(self) -> list[LedgerRow]:
        return _read_table(self.root / LEDGER, LedgerRow)

    def save_ledger(self, rows: list[LedgerRow]) -> None:
        self._write_table(self.root / LEDGER, LedgerRow, rows)

    def append_ledger(self, rows: list[LedgerRow]) -> None:
        self._append_table(self.root / LEDGER, LedgerRow, rows)

    def load_performance(self) -> list[Snapshot]:
        return _read_table(self.root / PERFORMANCE, Snapshot)

    def append_performance(self, snapshot: Snapshot) -> None:
        self._append_table(self.root / PERFORMANCE, Snapshot, [snapshot])
