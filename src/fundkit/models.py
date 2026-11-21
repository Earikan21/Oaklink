from datetime import date
from typing import Literal

from pydantic import BaseModel, field_validator, model_validator

Sleeve = Literal["indexed_equity", "active", "bonds", "cash"]


class Holding(BaseModel):
    ticker: str
    sleeve: Sleeve
    shares: float  # for ticker "CASH" this is dollars, not shares
    cost_basis: float
    lot_date: date


IPS_FEE_CAP = 0.009


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

    @field_validator("stocks", "bonds", "cash", "band", "active_ceiling", "name_cap", "trim_threshold",
                     "review_trim", "fee_cap", "min_upside", "max_corr", "max_spread", "reval_move")
    @classmethod
    def _fraction(cls, v: float, info) -> float:
        # catches a Sheet value typed as a percent (5 for 5%) or left blank/zero
        if not 0 < v <= 1:
            raise ValueError(f"{info.field_name} must be in (0, 1], got {v}")
        return v

    @field_validator("review_every")
    @classmethod
    def _positive_cadence(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"review_every must be >= 1, got {v}")
        return v

    @model_validator(mode="after")
    def _allocation_sums_to_one(self) -> "Targets":
        total = self.stocks + self.bonds + self.cash
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"stocks + bonds + cash must equal 1.0, got {total}")
        return self

    @model_validator(mode="after")
    def _ips_ordering(self) -> "Targets":
        if not self.name_cap <= self.active_ceiling <= self.stocks:
            raise ValueError(f"need name_cap <= active_ceiling <= stocks, got {self.name_cap}, "
                             f"{self.active_ceiling}, {self.stocks}")
        if not self.trim_threshold >= self.review_trim >= self.name_cap:
            raise ValueError(f"need trim_threshold >= review_trim >= name_cap, got {self.trim_threshold}, "
                             f"{self.review_trim}, {self.name_cap}")
        if self.fee_cap > IPS_FEE_CAP:
            raise ValueError(f"fee_cap must be <= {IPS_FEE_CAP} (IPS), got {self.fee_cap}")
        return self

    def differences(self) -> list[str]:
        """'field=value' for every field that differs from the IPS defaults, in field order."""
        default = Targets().model_dump(mode="json")
        return [f"{k}={v}" for k, v in self.model_dump(mode="json").items() if v != default[k]]


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
    unpriced: str = ""  # ';'-joined tickers excluded from total because they had no price


class Quote(BaseModel):
    ticker: str
    price: float
    bid: float | None
    ask: float | None
    expense_ratio: float | None
    nav: float | None
    quote_type: str
    avg_volume: float | None
