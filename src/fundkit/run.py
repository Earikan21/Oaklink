"""Weekly run orchestrator: reconcile → value → actions → research rows → watchlist → snapshot → memo.

This is what the Saturday scheduled task calls. Two guarantees:

* Research failures never crash the run. Each research call is wrapped and a failure becomes a note in
  `ActionSet.notes`, which the memo prints under Actions. The only errors that propagate are those
  `build_actions` raises by design (index/bond ETF unpriceable on the first run) and failures of the
  performance snapshot (no benchmark history), which leave nothing meaningful to report.
* A dry run writes nothing of its own. The one exception is `reconcile`, which always runs against the
  store: it only records Emmet's own responses (fills, expired pending rows) from earlier weeks, and the
  rest of the run must see that state.

A live run is idempotent per day: pending ledger rows whose (action, ticker) already has a pending row
dated today, and a second snapshot for today, are not appended again. The run number (for the review
cadence) ignores a snapshot already recorded today, so a same-day re-render keeps its review flag.
"""
from dataclasses import dataclass
from datetime import date

import fundkit.research.earnings as earnings
from fundkit.actions import ActionSet, build_actions
from fundkit.market import MarketData
from fundkit.memo import ActiveRow, MemoContext, render
from fundkit.models import Holding, LedgerRow, Snapshot, Targets, WatchItem
from fundkit.performance import snapshot
from fundkit.portfolio import CASH_TICKER, name_weights, value_holdings
from fundkit.reconcile import reconcile
from fundkit.research.estimates import revision_summary
from fundkit.research.etf import EtfPremium, premium
from fundkit.research.valuation import value_company
from fundkit.screen import peers_for, refresh_watchlist
from fundkit.store import Store

PRICE_CHANGE_SESSIONS = 5  # Close[-1] vs Close[-6]: one trading week
EARNINGS_WINDOW_DAYS = 7


@dataclass
class RunResult:
    memo_md: str
    actions: ActionSet
    snapshot: Snapshot
    review: bool
    written: bool
    context: MemoContext  # kept so the caller can re-render with AI notes


def _err(e: Exception) -> str:
    return type(e).__name__


def weekly_run(store: Store, market: MarketData, today: date, dry_run: bool = False,
               force_review: bool | None = None, ai_notes: dict[str, str] | None = None) -> RunResult:
    # (1) reconcile writes even on a dry run (see module docstring)
    rec = reconcile(store, today)

    # (2) value the post-reconcile book
    holdings = store.load_holdings()
    targets = store.load_targets()
    values, unpriced = value_holdings(holdings, market)

    # (3) review cadence; a snapshot already recorded today does not count as an earlier run
    performance = store.load_performance()
    run_number = sum(1 for s in performance if s.date != today) + 1
    review = force_review if force_review is not None else (run_number % targets.review_every == 0)

    # (4) actions (also carries the compliance report used by the memo)
    actions = build_actions(store, market, targets, today, review)
    notes = actions.notes

    # (5) ETF premium/discount
    etf_rows, etf_notes = _etf_rows(holdings, market)

    # (6) active sleeve rows, with revaluation of held names applied to `watch`
    watch = store.load_watchlist()
    active_rows, previews, recaps = _active_rows(holdings, values, watch, market, targets, today, notes)

    # (7) watchlist refresh on review runs only
    if review:
        try:
            watch = refresh_watchlist(watch, market, targets, today)
        except Exception as e:
            notes.append(f"watchlist refresh failed ({_err(e)}); kept last week's values")

    # (8) performance snapshot
    total = sum(values.values())
    inception, inception_value = (performance[0].date, performance[0].total) if performance else (today, total)
    snap = snapshot(store, market, targets, today, inception, inception_value)

    # (9) render
    ctx = MemoContext(date=today, review=review, reconcile=rec, compliance=actions.compliance, etf_rows=etf_rows,
                      active_rows=active_rows, earnings_previews=previews, earnings_recaps=recaps,
                      watch_summary=watch, actions=actions, snapshot=snap, unpriced=unpriced,
                      ai_notes=dict(ai_notes or {}), etf_notes=etf_notes)
    memo_md = render(ctx)

    written = False
    if not dry_run:
        _write(store, actions, snap, watch, today, performance)
        written = True
    return RunResult(memo_md=memo_md, actions=actions, snapshot=snap, review=review, written=written, context=ctx)


def _etf_rows(holdings: list[Holding], market: MarketData) -> tuple[list[tuple[str, EtfPremium]], list[str]]:
    """Premium rows for held ETFs. Non-active holdings that cannot be quoted are assumed to be ETFs and noted;
    active names that cannot be quoted are already reported as unpriced."""
    rows: list[tuple[str, EtfPremium]] = []
    notes: list[str] = []
    sleeve_of: dict[str, str] = {}
    for h in holdings:
        sleeve_of.setdefault(h.ticker, h.sleeve)
    for t, sleeve in sleeve_of.items():
        if t == CASH_TICKER:
            continue
        try:
            if market.quote(t).quote_type != "ETF":
                continue
        except Exception:
            if sleeve != "active":
                notes.append(f"{t}: premium check unavailable")
            continue
        try:
            rows.append((t, premium(t, market)))
        except Exception:
            notes.append(f"{t}: premium check unavailable")
    return rows, notes


def _active_rows(holdings: list[Holding], values: dict[str, float], watch: list[WatchItem], market: MarketData,
                 targets: Targets, today: date, notes: list[str]) -> tuple[list[ActiveRow], list[str], list]:
    """One row per active ticker. Held WatchItems are revalued in place in `watch` when due."""
    weights = name_weights(holdings, values)
    rows: list[ActiveRow] = []
    previews: list[str] = []
    recaps: list = []
    for t in sorted({h.ticker for h in holdings if h.sleeve == "active"}):
        change = _price_change(t, market, notes)

        direction = None
        try:
            direction = revision_summary(t, market).direction
        except Exception as e:
            notes.append(f"{t}: revisions unavailable ({_err(e)})")

        soon = None
        try:
            soon = earnings.upcoming_within(t, market, today, days=EARNINGS_WINDOW_DAYS)
        except Exception as e:
            notes.append(f"{t}: earnings calendar unavailable ({_err(e)})")
        if soon is not None:
            previews.append(f"{t} reports {soon:%Y-%m-%d} ({'after close' if soon.hour >= 16 else 'before open'})")

        recent = None
        try:
            recent = earnings.reported_within(t, market, today, days=EARNINGS_WINDOW_DAYS)
        except Exception as e:
            notes.append(f"{t}: earnings results unavailable ({_err(e)})")
        if recent is not None:
            recaps.append(recent)

        i = next((k for k, w in enumerate(watch) if w.ticker == t and w.status == "held"), None)
        if i is not None and _due(watch[i], recent is not None, market, targets, notes):
            try:
                v = value_company(t, market, peers_for(t, market))
                watch[i] = watch[i].model_copy(update={"fair_value": v.blended, "upside": v.upside,
                                                       "last_valued": today})
            except Exception as e:
                last = watch[i].last_valued.isoformat() if watch[i].last_valued else "never"
                notes.append(f"{t}: revaluation failed ({_err(e)}); fair value kept (last valued {last})")
        item = watch[i] if i is not None else None

        rows.append(ActiveRow(
            ticker=t, weight_pct=weights.get(t, 0.0) * 100, price_change_pct=change, revision_direction=direction,
            fair_value=item.fair_value if item else None, upside=item.upside if item else None,
            earnings_soon=soon is not None, earnings_recent=recent is not None))
    return rows, previews, recaps


def _price_change(t: str, market: MarketData, notes: list[str]) -> float | None:
    """One-week price change in PERCENT (memo convention), from the last 6 closes."""
    try:
        close = market.history(t, period="1mo")["Close"].dropna()
        if len(close) <= PRICE_CHANGE_SESSIONS:
            raise ValueError("too few closes")
        return (float(close.iloc[-1]) / float(close.iloc[-1 - PRICE_CHANGE_SESSIONS]) - 1) * 100
    except Exception as e:
        notes.append(f"{t}: price change unavailable ({_err(e)})")
        return None


def _due(item: WatchItem, reported: bool, market: MarketData, targets: Targets, notes: list[str]) -> bool:
    """Revalue after an earnings report, when no usable valuation exists, or when the price has moved more
    than `reval_move` since the last valuation (price then = fair_value / (1 + upside), as in screen.py)."""
    if reported:
        return True
    fv, up = item.fair_value, item.upside
    if item.last_valued is None or fv is None or up is None or fv <= 0 or 1 + up <= 0:
        return True
    try:
        price = market.quote(item.ticker).price
    except Exception as e:
        notes.append(f"{item.ticker}: price unavailable for revaluation check ({_err(e)})")
        return False
    return abs(price / (fv / (1 + up)) - 1) > targets.reval_move


def _write(store: Store, actions: ActionSet, snap: Snapshot, watch: list[WatchItem], today: date,
           performance: list[Snapshot]) -> None:
    already = {(r.action, r.ticker) for r in store.load_ledger() if r.response == "pending" and r.date == today}
    rows: list[LedgerRow] = []
    for i, p in enumerate([*actions.sells, *actions.proposals], start=1):
        if (p.action, p.ticker) in already:
            continue
        rows.append(LedgerRow(id=f"{today:%Y%m%d}-{i:02d}-{p.action}-{p.ticker}", date=today, action=p.action,
                              ticker=p.ticker, amount=p.dollars, rationale=p.reason, ips_check="PASS",
                              fill_shares=None, fill_price=None, est_tax=p.est_tax))
    store.append_ledger(rows + list(actions.ledger_notes))
    if not any(s.date == today for s in performance):
        store.append_performance(snap)
    store.save_watchlist(watch)
