from datetime import date
from typing import Literal

from pydantic import BaseModel, model_validator

Sleeve = Literal["indexed_equity", "active", "bonds", "cash"]


class Holding(BaseModel):
    ticker: str
    sleeve: Sleeve
    shares: float  # for ticker "CASH" this is dollars, not shares
    cost_basis: float
    lot_date: date


class Targets(BaseModel):
    stocks: float = 0.50
    bonds: float = 0.30
    cash: float = 0.20
    band: float = 0.05
    active_ceiling: float = 0.30
    name_cap: float = 0.05
    trim_threshold: float = 0.075
    review_trim: float = 0.06
    fee_cap: float = 0.009
    min_upside: float = 0.25
    max_corr: float = 0.80
    max_spread: float = 0.005
    reval_move: float = 0.15
    review_every: int = 6
    index_etf: str = "VTI"
    bond_etf: str = "BND"

    @model_validator(mode="after")
    def _allocation_sums_to_one(self) -> "Targets":
        total = self.stocks + self.bonds + self.cash
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"stocks + bonds + cash must equal 1.0, got {total}")
        return self


class WatchItem(BaseModel):
    ticker: str
    thesis: str
    last_valued: date | None
    fair_value: float | None
    upside: float | None
    status: Literal["candidate", "passed", "rejected", "held"]


class LedgerRow(BaseModel):
    id: str
    date: date
    action: Literal["BUY", "SELL", "REBALANCE", "NOTE"]
    ticker: str
    amount: float
    rationale: str
    ips_check: Literal["PASS", "FAIL"]
    response: Literal["pending", "done", "applied", "skipped", "modified"] = "pending"
    fill_shares: float | None = None
    fill_price: float | None = None
    est_tax: float | None = None


class Snapshot(BaseModel):
    date: date
    total: float
    indexed_equity: float
    active: float
    bonds: float
    cash: float
    bench_total: float
    active_vs_index: float | None
    turnover_qtd: float


class Quote(BaseModel):
    ticker: str
    price: float
    bid: float | None
    ask: float | None
    expense_ratio: float | None
    nav: float | None
    quote_type: str
    avg_volume: float | None
