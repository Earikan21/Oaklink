from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from fundkit.actions import ActionSet
from fundkit.ips import ComplianceReport
from fundkit.memo import ActiveRow, MemoContext, render
from fundkit.models import Snapshot, WatchItem
from fundkit.portfolio import Proposal
from fundkit.reconcile import ReconcileResult
from fundkit.research.earnings import EarningsResult
from fundkit.research.etf import EtfPremium


def _snapshot(**kw):
    base = dict(date=date(2026, 11, 29), total=3000.0, indexed_equity=0.0, active=0.0, bonds=0.0,
                cash=3000.0, bench_total=3000.0, active_vs_index=None, turnover_qtd=0.0)
    base.update(kw)
    return Snapshot(**base)


def _ctx(**kw):
    base = dict(date=date(2026, 11, 29), review=False, reconcile=ReconcileResult(),
                compliance=ComplianceReport(), etf_rows=[], active_rows=[], earnings_previews=[],
                earnings_recaps=[], watch_summary=[], actions=ActionSet(compliance=ComplianceReport()),
                snapshot=_snapshot(), unpriced=[])
    base.update(kw)
    return MemoContext(**base)


def _watch(i, upside):
    return WatchItem(ticker=f"W{i:02d}", thesis="t", last_valued=date(2026, 11, 1), fair_value=100.0,
                     upside=upside, status="candidate")


def _arow(i):
    return ActiveRow(ticker=f"A{i:02d}", weight_pct=2.5, price_change_pct=1.2, revision_direction="up",
                     fair_value=50.0, upside=0.3, earnings_soon=False, earnings_recent=False)


def _section(out, n):
    lines = out.splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith(f"## {n}."))
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return lines[start:end]


@pytest.fixture
def minimal_ctx():
    return _ctx()


@pytest.fixture
def ctx_one_buy():
    a = ActionSet(compliance=ComplianceReport(),
                  proposals=[Proposal(action="BUY", ticker="VTI", dollars=1500.0, reason="initial allocation")])
    return _ctx(actions=a)


@pytest.fixture
def ctx_with_30_watch_items():
    return _ctx(watch_summary=[_watch(i, i / 100) for i in range(30)],
                active_rows=[_arow(i) for i in range(30)],
                earnings_previews=[f"P{i} reports soon" for i in range(10)])


@pytest.fixture
def ctx_with_unpriced():
    return _ctx(unpriced=["ZZZZ"])


def test_memo_always_has_compliance_and_actions(minimal_ctx):
    out = render(minimal_ctx)
    assert "## 2. IPS compliance" in out and "## 6. Actions" in out


def test_no_action_line_when_empty(minimal_ctx):
    assert ("No action — portfolio within IPS bands and no watchlist name passed the gate"
            in render(minimal_ctx))


def test_no_action_reason_is_fixed_and_notes_are_not_repeated():
    a = ActionSet(compliance=ComplianceReport(), notes=["market closed", "other"])
    sec = _section(render(_ctx(actions=a)), 6)
    assert "No action — portfolio within IPS bands and no watchlist name passed the gate" in sec
    assert sec.count("- market closed") == 1 and not any("No action — market closed" in l for l in sec)
    assert "- other" in sec


def test_no_action_points_at_compliance_flags_when_present():
    c = ComplianceReport(active_ceiling_flag="active 35.0% > 30% ceiling")
    out = render(_ctx(compliance=c, actions=ActionSet(compliance=c, notes=["n1"])))
    assert "No action — see IPS compliance flags above" in "\n".join(_section(out, 6))
    c2 = ComplianceReport(fund_flags=["BND: expense ratio unavailable"])
    assert "No action — see IPS compliance flags above" in render(_ctx(compliance=c2))


def test_action_line_format(ctx_one_buy):
    assert "- [ ] BUY — VTI — $1,500 — initial allocation — IPS check: PASS\n" in render(ctx_one_buy) + "\n"


def test_memo_under_90_lines(ctx_with_30_watch_items):
    out = render(ctx_with_30_watch_items)
    assert len(out.splitlines()) <= 90 and "more in Sheet" in out


def test_unpriced_tickers_named(ctx_with_unpriced):
    assert "ZZZZ" in render(ctx_with_unpriced)


def test_review_run_badge():
    assert render(_ctx(review=True)).splitlines()[0] == "# Oaklink weekly memo — 2026-11-29 · REVIEW RUN"
    assert render(_ctx()).splitlines()[0] == "# Oaklink weekly memo — 2026-11-29"


def test_attention_line_absent_when_clean(minimal_ctx):
    assert "**Attention:**" not in render(minimal_ctx)


def test_attention_line_parts():
    out = render(_ctx(unpriced=["A", "B"], reconcile=ReconcileResult(incomplete=["id1"])))
    assert "**Attention:** unpriced tickers: A, B; ledger rows missing fill data: id1" in out
    only_inc = render(_ctx(reconcile=ReconcileResult(incomplete=["id1"])))
    assert "**Attention:** ledger rows missing fill data: id1" in only_inc
    assert "unpriced tickers" not in only_inc


def test_whole_and_fractional_shares_formatting():
    a = ActionSet(compliance=ComplianceReport(), proposals=[
        Proposal(action="BUY", ticker="BND", dollars=900.0, shares=12.0, reason="r"),
        Proposal(action="BUY", ticker="XYZ", dollars=100.0, shares=1.5, reason="r")])
    out = render(_ctx(actions=a))
    assert "- [ ] BUY — BND — $900 (12 sh) — r — IPS check: PASS" in out
    assert "$100 (1.5 sh)" in out


def test_est_tax_suffix():
    a = ActionSet(compliance=ComplianceReport(),
                  sells=[Proposal(action="SELL", ticker="AAA", dollars=2000.0, reason="trim", est_tax=123.4)])
    assert "- [ ] SELL — AAA — $2,000 — trim — IPS check: PASS — est. tax $123" in render(_ctx(actions=a))


def test_sells_before_proposals_and_notes_after():
    a = ActionSet(compliance=ComplianceReport(),
                  proposals=[Proposal(action="BUY", ticker="VTI", dollars=1.0, reason="r")],
                  sells=[Proposal(action="SELL", ticker="AAA", dollars=2.0, reason="r")],
                  notes=["tax estimate assumes flat rates"])
    sec = "\n".join(_section(render(_ctx(actions=a)), 6))
    assert sec.index("SELL — AAA") < sec.index("BUY — VTI") < sec.index("- tax estimate assumes flat rates")
    assert "No action" not in sec


def test_ai_notes_under_right_heading():
    out = render(_ctx(ai_notes={"3": "NOTE THREE", "6": "NOTE SIX"}))
    assert "NOTE THREE" in "\n".join(_section(out, 3))
    assert "NOTE SIX" in "\n".join(_section(out, 6))
    assert "NOTE THREE" not in "\n".join(_section(out, 4))


def test_etf_noise_vs_meaningful():
    rows = [("VTI", EtfPremium(price=100, nav=99.88, premium_pct=0.12, spread_pct=0.05, meaningful=True)),
            ("BND", EtfPremium(price=70, nav=70.0, premium_pct=-0.02, spread_pct=None, meaningful=False))]
    sec = "\n".join(_section(render(_ctx(etf_rows=rows)), 3))
    assert "- VTI: premium +0.12% (spread 0.05%) — meaningful" in sec
    assert "- BND: premium -0.02% — noise" in sec
    assert "Nothing to do." not in sec
    assert "Nothing to do." in "\n".join(_section(render(_ctx()), 3))


def test_etf_notes_listed():
    assert "- BND: no NAV" in render(_ctx(etf_notes=["BND: no NAV"]))


def test_compliance_flags_and_green():
    assert "All green." in "\n".join(_section(render(_ctx()), 2))
    c = ComplianceReport(sleeve_flags=["stocks over band"], active_ceiling_flag="active over ceiling",
                         trim_flags=["AAA 8%"], indexed_lt_active=True)
    sec = "\n".join(_section(render(_ctx(compliance=c)), 2))
    for s in ("- stocks over band", "- active over ceiling", "- AAA 8%", "indexed equity below active"):
        assert s in sec
    assert "All green." not in sec


def test_fund_flags_listed_in_compliance_section():
    c = ComplianceReport(fund_flags=["XYZ: expense ratio 0.95% >= 0.90% cap"])
    sec = "\n".join(_section(render(_ctx(compliance=c)), 2))
    assert "- XYZ: expense ratio 0.95% >= 0.90% cap" in sec
    assert "All green." not in sec


def test_reconcile_section():
    r = ReconcileResult(applied=["a1", "a2"], carried=["c1"], warnings=["thesis stale"])
    sec = "\n".join(_section(render(_ctx(reconcile=r)), 1))
    assert "Applied: 2 (a1, a2)" in sec and "Carried: 1 (c1)" in sec and "Dropped: 0" in sec
    assert "- thesis stale" in sec


def test_active_table_and_empty():
    out = render(_ctx(active_rows=[_arow(1)], earnings_previews=["A01 reports Tue"]))
    sec = "\n".join(_section(out, 4))
    assert "| Ticker | Weight | 1w Δ | Revisions | Fair value | Upside | Earnings |" in sec
    assert "| A01 | 2.5% | +1.2% | up | $50 | +30% |" in sec
    assert "- A01 reports Tue" in sec
    assert "Empty." in "\n".join(_section(render(_ctx()), 4))


def test_earnings_recap_bullet():
    er = EarningsResult(report_ts=datetime(2026, 11, 25, 16, tzinfo=ZoneInfo("America/New_York")),
                        eps_est=1.0, eps_actual=1.1, surprise_pct=10.0, reaction_pct=-3.0)
    assert "2026-11-25" in "\n".join(_section(render(_ctx(earnings_recaps=[er])), 4))


def test_scorecard():
    s = _snapshot(total=3120.0, bench_total=3000.0, active_vs_index=-50.0, turnover_qtd=0.125)
    sec = "\n".join(_section(render(_ctx(snapshot=s)), 7))
    assert "total $3,120" in sec and "benchmark $3,000 (Δ $120)" in sec
    assert "active vs index: -$50" in sec and "turnover QTD: 12.5%" in sec
    assert "active vs index: n/a" in "\n".join(_section(render(_ctx()), 7))


def test_watch_table_keeps_top_upside_when_truncated(ctx_with_30_watch_items):
    out = render(ctx_with_30_watch_items)
    assert "W29" in out and "W00" not in out
    assert "(… " in out


def test_sections_2_6_7_never_truncated_at_30_watch_items():
    comp = ComplianceReport(sleeve_flags=[f"flag{i}" for i in range(5)])
    props = [Proposal(action="BUY", ticker=f"B{i}", dollars=10.0, reason="r") for i in range(4)]
    ctx = _ctx(watch_summary=[_watch(i, i / 100) for i in range(30)], active_rows=[_arow(i) for i in range(20)],
               compliance=comp, actions=ActionSet(compliance=comp, proposals=props, notes=["n1", "n2"]))
    out = render(ctx)
    assert len(out.splitlines()) <= 90
    assert all(f"flag{i}" in out for i in range(5))
    assert all(f"BUY — B{i}" in out for i in range(4))
    assert "- n1" in out and "- n2" in out
    assert "## 7. Scorecard" in out and "turnover QTD" in out
