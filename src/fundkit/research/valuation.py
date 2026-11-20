"""Company valuation: 5-year DCF (Gordon + exit multiple) blended with peer-multiple relative value."""
import math
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel

from fundkit.market import MarketData, MarketUnavailable

HORIZON = 5
G_TERMINAL = 0.025
ERP = 0.055
DEFAULT_RF = 0.045
DEFAULT_KD = 0.055
DEFAULT_TAX = 0.21
EXIT_MULTIPLE = 15.0
WACC_OFFSETS = (-0.01, -0.005, 0.0, 0.005, 0.01)
SENS_G = (0.015, 0.020, 0.025, 0.030, 0.035)


class Valuation(BaseModel):
    ticker: str
    price: float
    dcf: float | None
    relative: float | None
    blended: float
    upside: float
    wacc: float
    g: float
    tv_share: float
    # Keys are f"{wacc:.4f}|{g:.4f}" (pydantic cannot serialize tuple keys to JSON).
    # Always 25 entries; a cell with wacc <= g holds NaN.
    sensitivity: dict[str, float]
    flags: list[str]
    confidence: Literal["high", "medium", "low"]


def _finite(values) -> list[float]:
    return [float(x) for x in values if x is not None and math.isfinite(float(x))]


def _median(values, default: float) -> float:
    vals = _finite(values)
    return float(np.median(vals)) if vals else default


def _row(df: pd.DataFrame, label: str) -> pd.Series | None:
    if df is None or label not in df.index:
        return None
    return pd.to_numeric(df.loc[label], errors="coerce").astype(float)


def _num(x, default=None):
    try:
        f = float(x)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def key(wacc: float, g: float) -> str:
    return f"{wacc:.4f}|{g:.4f}"


def value_company(ticker: str, market: MarketData, peers: list[str], rf: float | None = None) -> Valuation:
    flags: list[str] = []
    price = market.quote(ticker).price
    info = market.info(ticker)
    for req in ("marketCap", "sharesOutstanding"):
        if _num(info.get(req)) is None:
            raise ValueError(f"valuation impossible: {ticker} info missing {req}")
    mcap, shares = float(info["marketCap"]), float(info["sharesOutstanding"])
    debt = _num(info.get("totalDebt"), 0.0)
    cash = _num(info.get("totalCash"), 0.0)

    if rf is None:
        try:
            rf = market.quote("^TNX").price / 100
        except MarketUnavailable:
            rf = DEFAULT_RF
            flags.append("stale_rf")
    beta = _num(info.get("beta"))
    if beta is None:
        beta = 1.0
        flags.append("beta_default")

    income = market.income_stmt(ticker)
    cashflow = market.cashflow(ticker)
    years = income.shape[1]

    # --- cost of capital -------------------------------------------------
    rev_row = _row(income, "Total Revenue")
    tax_p, pretax = _row(income, "Tax Provision"), _row(income, "Pretax Income")
    if tax_p is not None and pretax is not None:
        rates = [t / p for t, p in zip(tax_p.iloc[:3], pretax.iloc[:3]) if _num(p) and p > 0]
        tax = min(max(_median(rates, DEFAULT_TAX), 0.15), 0.30)
    else:
        tax = DEFAULT_TAX
    interest = _row(income, "Interest Expense")
    int_exp = _num(interest.iloc[0]) if interest is not None else None
    kd = int_exp / debt if int_exp and int_exp > 0 and debt > 0 else DEFAULT_KD
    ke = rf + beta * ERP
    e_v = mcap / (mcap + debt)
    wacc = e_v * ke + (1 - e_v) * kd * (1 - tax)
    g = G_TERMINAL
    if wacc <= g:
        g = wacc - 0.005
        flags.append("g_capped")

    # --- DCF -------------------------------------------------------------
    dcf, tv_share, sensitivity = None, float("nan"), {}
    ebit_row = _row(income, "Operating Income")
    if ebit_row is None:
        ebit_row = _row(income, "EBIT")
    if ebit_row is None:
        flags.append("missing:Operating Income")
    rev_hist = _finite(rev_row.iloc[:HORIZON][::-1]) if rev_row is not None else []
    if ebit_row is None or len(rev_hist) < 2 or rev_hist[0] <= 0 or rev_hist[-1] <= 0:
        flags.append("no_dcf")
    else:
        def ratio(df: pd.DataFrame, label: str) -> float:
            row = _row(df, label)
            if row is None:
                flags.append(f"missing:{label}")
                return 0.0
            row = row.reindex(income.columns)
            return _median((row / rev_row).iloc[:3], 0.0)

        ebit_m = _median((ebit_row / rev_row).iloc[:3], 0.0)
        da_m = ratio(cashflow, "Depreciation And Amortization")
        capex_m = ratio(cashflow, "Capital Expenditure")  # negative
        nwc_m = ratio(cashflow, "Change In Working Capital")  # cash-flow sign

        y1 = (rev_hist[-1] / rev_hist[0]) ** (1 / (len(rev_hist) - 1)) - 1
        clamped = min(max(y1, -0.20), 0.40)
        if clamped != y1:
            flags.append("growth_clamped")
        growth_path = np.linspace(clamped, g + 0.01, HORIZON)

        rev, fcffs = rev_hist[-1], []
        for gr in growth_path:
            rev *= 1 + gr
            ebit, da = ebit_m * rev, da_m * rev
            nopat = ebit - tax * max(ebit, 0.0)
            fcffs.append(nopat + da + capex_m * rev + nwc_m * rev)
        net_debt_adj = cash - debt

        def pv_fcff(w: float) -> float:
            return sum(f / (1 + w) ** t for t, f in enumerate(fcffs, start=1))

        def gordon(w: float, gg: float) -> float:
            return fcffs[-1] * (1 + gg) / (w - gg)

        exit_tv = (ebit + da) * EXIT_MULTIPLE
        tv_base = (gordon(wacc, g) + exit_tv) / 2
        pv_tv = tv_base / (1 + wacc) ** HORIZON
        ev = pv_fcff(wacc) + pv_tv
        dcf = (ev + net_debt_adj) / shares
        tv_share = pv_tv / ev if ev else float("nan")
        if not 0.45 <= tv_share <= 0.85:
            flags.append("tv_share")

        for dw in WACC_OFFSETS:
            w = wacc + dw
            for gg in SENS_G:
                if w <= gg:
                    sensitivity[key(w, gg)] = float("nan")
                else:
                    sensitivity[key(w, gg)] = (pv_fcff(w) + gordon(w, gg) / (1 + w) ** HORIZON
                                               + net_debt_adj) / shares

    # --- relative --------------------------------------------------------
    relative = None
    if not peers:
        flags.append("no_peers")
    else:
        pes, evrs, eves = [], [], []
        for p in peers:
            try:
                pi = market.info(p)
            except MarketUnavailable:
                flags.append(f"peer_dropped:{p}")
                continue
            pes.append(_num(pi.get("forwardPE"), float("nan")))
            evrs.append(_num(pi.get("enterpriseToRevenue"), float("nan")))
            eves.append(_num(pi.get("enterpriseToEbitda"), float("nan")))

        eps = _num(info.get("trailingEps"))
        if eps is None and (r := _row(income, "Diluted EPS")) is not None:
            eps = _num(r.iloc[0])
        rev_ttm = _num(info.get("totalRevenue"))
        if rev_ttm is None and rev_row is not None:
            rev_ttm = _num(rev_row.iloc[0])
        ebitda = _num(info.get("ebitda"))
        if ebitda is None and ebit_row is not None:
            da0 = _row(cashflow, "Depreciation And Amortization")
            ebitda = _num(ebit_row.iloc[0], 0.0) + (_num(da0.iloc[0], 0.0) if da0 is not None else 0.0)

        net_debt = debt - cash
        implied: list[float] = []

        def med(vals: list[float]) -> float:
            f = _finite(vals)
            return float(np.median(f)) if f else float("nan")

        if eps is None or eps <= 0:
            flags.append("pe_skipped")
        else:
            implied.append(med(pes) * eps)
        if rev_ttm is not None:
            implied.append((med(evrs) * rev_ttm - net_debt) / shares)
        if ebitda is None or ebitda <= 0:
            flags.append("ev_ebitda_skipped")
        else:
            implied.append((med(eves) * ebitda - net_debt) / shares)
        implied = _finite(implied)
        if implied:
            relative = float(np.median(implied))
        else:
            flags.append("no_relative")

    # --- blend -----------------------------------------------------------
    if dcf is not None and relative is not None:
        blended = 0.5 * dcf + 0.5 * relative
    else:
        blended = dcf if dcf is not None else relative
    if blended is None or math.isnan(blended):
        raise ValueError(f"valuation impossible: no usable DCF or relative value for {ticker}")

    skipped = {"pe_skipped", "ev_ebitda_skipped", "no_relative", "no_peers", "no_dcf"}
    failures = (years < 3) + bool(skipped & set(flags))
    if any(f.startswith("missing:") for f in flags) or failures == 2:
        confidence = "low"
    else:
        confidence = "medium" if failures == 1 else "high"

    return Valuation(
        ticker=ticker, price=price, dcf=dcf, relative=relative, blended=blended,
        upside=blended / price - 1, wacc=wacc, g=g, tv_share=tv_share,
        sensitivity=sensitivity, flags=flags, confidence=confidence,
    )
