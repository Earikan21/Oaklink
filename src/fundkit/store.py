import csv
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


def _read_table(path: Path, model):
    with path.open(newline="", encoding="utf-8") as f:
        rows = []
        for raw in csv.DictReader(f):
            row = {
                key: (None if value == "" and _allows_none(model.model_fields[key].annotation) else value)
                for key, value in raw.items()
            }
            rows.append(model.model_validate(row))
    return rows


def _csv_writer(f, model):
    return csv.DictWriter(f, fieldnames=list(model.model_fields), lineterminator="\n")


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
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = _csv_writer(f, model)
            writer.writeheader()
            for row in rows:
                writer.writerow(row.model_dump(mode="json"))

    def _append_table(self, path: Path, model, rows) -> None:
        write_header = not path.exists() or path.stat().st_size == 0
        with path.open("a", newline="", encoding="utf-8") as f:
            writer = _csv_writer(f, model)
            if write_header:
                writer.writeheader()
            for row in rows:
                writer.writerow(row.model_dump(mode="json"))

    def load_holdings(self) -> list[Holding]:
        return _read_table(self.root / HOLDINGS, Holding)

    def save_holdings(self, holdings: list[Holding]) -> None:
        self._write_table(self.root / HOLDINGS, Holding, holdings)

    def load_targets(self) -> Targets:
        with (self.root / TARGETS).open(newline="", encoding="utf-8") as f:
            pairs = {row["key"]: row["value"] for row in csv.DictReader(f)}
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

    def append_ledger(self, rows: list[LedgerRow]) -> None:
        self._append_table(self.root / LEDGER, LedgerRow, rows)

    def load_performance(self) -> list[Snapshot]:
        return _read_table(self.root / PERFORMANCE, Snapshot)

    def append_performance(self, snapshot: Snapshot) -> None:
        self._append_table(self.root / PERFORMANCE, Snapshot, [snapshot])
