"""Render the weekly memo (Markdown). Pure function of `MemoContext`; no file I/O.

The jinja template owns the layout; this module owns formatting, the 90-line cap and truncation.
Truncation order: watchlist rows (lowest upside first), then active rows, then earnings bullets.
Sections 2 (compliance), 6 (actions) and 7 (scorecard) are never truncated.
"""
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from fundkit.actions import ActionSet
from fundkit.ips import ComplianceReport
from fundkit.models import Snapshot, Targets, WatchItem
from fundkit.portfolio import Proposal
from fundkit.reconcile import ReconcileResult
from fundkit.research.earnings import EarningsResult
from fundkit.research.etf import EtfPremium

MAX_LINES = 90
NO_ACTION_DEFAULT = "portfolio within IPS bands and no watchlist name passed the gate"
NO_ACTION_FLAGGED = "see IPS compliance flags above"

_env = Environment(loader=FileSystemLoader(str(Path(__file__).parent / "templates")),
                   trim_blocks=True, lstrip_blocks=True, keep_trailing_newline=False,
                   undefined=StrictUndefined, autoescape=False)


@dataclass
class ActiveRow:
    ticker: str
    weight_pct: float
    price_change_pct: float | None
    revision_direction: str | None
    fair_value: float | None
    upside: float | None
    earnings_soon: bool
    earnings_recent: bool


@dataclass
class MemoContext:
    date: date
    review: bool
    reconcile: ReconcileResult
    compliance: ComplianceReport
    etf_rows: list[tuple[str, EtfPremium]]
    active_rows: list[ActiveRow]
    earnings_previews: list[str]
    earnings_recaps: list[EarningsResult]
    watch_summary: list[WatchItem]
    actions: ActionSet
    snapshot: Snapshot
    unpriced: list[str]
    ai_notes: dict[str, str] = field(default_factory=dict)  # keys "3".."7"
    etf_notes: list[str] = field(default_factory=list)      # ETFs that could not be checked
    targets: Targets = field(default_factory=Targets)        # the run's targets; non-defaults are listed in §2


def _money(x: float) -> str:
    return f"-${abs(x):,.0f}" if round(x) < 0 else f"${x:,.0f}"


def _num(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else f"{x:g}"


def _pct(x: float | None, fmt: str) -> str:
    return "n/a" if x is None else format(x, fmt)


def _ids(label: str, ids: list[str]) -> str:
    return f"{label}: {len(ids)}" + (f" ({', '.join(ids)})" if ids else "")


def _action_line(p: Proposal) -> str:
    shares = f" ({_num(p.shares)} sh)" if p.shares is not None else ""
    tax = f" — est. tax ${p.est_tax:,.0f}" if p.est_tax is not None else ""
    # build_actions pre-checks every proposal/sell against the IPS (picks through the gate, index/bond ETF
    # buys through check_instrument), so anything listed here passes.
    return f"- [ ] {p.action} — {p.ticker} — ${p.dollars:,.0f}{shares} — {p.reason} — IPS check: PASS{tax}"


def _active_line(r: ActiveRow) -> str:
    earn = "soon" if r.earnings_soon else ("recent" if r.earnings_recent else "")
    return (f"| {r.ticker} | {r.weight_pct:.1f}% | {_pct(r.price_change_pct, '+.1f')}"
            f"{'' if r.price_change_pct is None else '%'} | {r.revision_direction or '—'} | "
            f"{'—' if r.fair_value is None else _money(r.fair_value)} | {_pct(r.upside, '+.0%')} | {earn} |")


def _watch_line(w: WatchItem) -> str:
    return (f"| {w.ticker} | {w.status} | {_pct(w.upside, '+.0%')} | "
            f"{w.last_valued.isoformat() if w.last_valued else 'never'} |")


def _etf_line(t: str, e: EtfPremium) -> str:
    spread = f" (spread {e.spread_pct:.2f}%)" if e.spread_pct is not None else ""
    return f"- {t}: premium {e.premium_pct:+.2f}%{spread} — {'meaningful' if e.meaningful else 'noise'}"


def _recap_line(r: EarningsResult) -> str:
    return (f"Earnings {r.report_ts:%Y-%m-%d}: EPS {_pct(r.eps_actual, '.2f')} vs est {_pct(r.eps_est, '.2f')}, "
            f"surprise {_pct(r.surprise_pct, '+.1f')}%, reaction {_pct(r.reaction_pct, '+.1f')}%")


def _attention(ctx: MemoContext) -> str:
    parts = []
    if ctx.unpriced:
        parts.append(f"unpriced tickers: {', '.join(ctx.unpriced)}")
    if ctx.reconcile.incomplete:
        parts.append(f"ledger rows missing fill data: {', '.join(ctx.reconcile.incomplete)}")
    return "; ".join(parts)


def _flag_lines(c: ComplianceReport) -> list[str]:
    flags = [*c.sleeve_flags, *([c.active_ceiling_flag] if c.active_ceiling_flag else []), *c.trim_flags,
             *c.fund_flags]
    lines = [f"- {f}" for f in flags]
    if c.indexed_lt_active:
        lines.append("- indexed equity below active")
    return lines


def _compliance_lines(c: ComplianceReport, targets: Targets) -> list[str]:
    lines = _flag_lines(c) or ["All green."]
    diff = targets.differences()
    if diff:  # informational, not a flag
        lines.append(f"Targets differ from IPS defaults: {', '.join(diff)}")
    return lines


def _action_lines(a: ActionSet, c: ComplianceReport) -> list[str]:
    """Notes are listed separately below these lines, so the "No action" reason never repeats one."""
    lines = [_action_line(p) for p in [*a.sells, *a.proposals]]
    if not lines:
        lines = [f"No action — {NO_ACTION_FLAGGED if _flag_lines(c) else NO_ACTION_DEFAULT}"]
    return lines


def _build(ctx: MemoContext, n_watch: int, n_active: int, n_earn: int,
           watch: list[WatchItem], earn: list[str]) -> str:
    snap, r = ctx.snapshot, ctx.reconcile
    etf_lines = [_etf_line(t, e) for t, e in ctx.etf_rows] + [f"- {n}" for n in ctx.etf_notes]
    shown_active = ctx.active_rows[:n_active]
    values = dict(
        title=f"# Oaklink weekly memo — {ctx.date:%Y-%m-%d}" + (" · REVIEW RUN" if ctx.review else ""), attention=_attention(ctx),
        last_week=[" · ".join([_ids("Applied", r.applied), _ids("Carried", r.carried),
                               _ids("Dropped", r.dropped), _ids("Incomplete", r.incomplete)])],
        warnings=r.warnings, compliance=_compliance_lines(ctx.compliance, ctx.targets),
        etf_lines=etf_lines or ["Nothing to do."],
        active_rows=[_active_line(x) for x in shown_active], active_more=len(ctx.active_rows) - len(shown_active),
        earnings_lines=earn[:n_earn], earnings_more=len(earn) - len(earn[:n_earn]),
        watch_rows=[_watch_line(w) for w in watch[:n_watch]], watch_more=len(watch) - len(watch[:n_watch]),
        action_lines=_action_lines(ctx.actions, ctx.compliance), action_notes=ctx.actions.notes,
        total=_money(snap.total), bench=_money(snap.bench_total), delta=_money(snap.total - snap.bench_total),
        avi="n/a" if snap.active_vs_index is None else _money(snap.active_vs_index),
        turnover=f"{snap.turnover_qtd:.1%}",
        ai={k: (f"\n{ctx.ai_notes[k]}\n" if k in ctx.ai_notes else "") for k in "1234567"},
    )
    return _env.get_template("memo.md.j2").render(**values)


def render(ctx: MemoContext) -> str:
    watch = sorted(ctx.watch_summary, key=lambda w: (w.upside is None, -(w.upside or 0.0)))
    earn = [*ctx.earnings_previews, *(_recap_line(x) for x in ctx.earnings_recaps)]
    n_watch, n_active, n_earn = len(watch), len(ctx.active_rows), len(earn)
    while True:
        out = _build(ctx, n_watch, n_active, n_earn, watch, earn).rstrip("\n") + "\n"
        if len(out.splitlines()) <= MAX_LINES or not (n_watch or n_active or n_earn):
            return out
        if n_watch:
            n_watch -= 1
        elif n_active:
            n_active -= 1
        else:
            n_earn -= 1
