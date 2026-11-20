# Oaklink Fund Engine — Design

Date: 2026-11-20 · Owner: Emmet Arikan · Status: v2 — builder-checked, awaiting Emmet's approval

## 1. Intent

Run the Oaklink Investments portfolio as an ongoing, AI-driven engine that follows the Oaklink IPS and tests its central claim: that returns follow investor *effort* (Benjamin Graham), and that agentic AI can supply that effort so Emmet doesn't have to. Emmet receives one short memo per week with yes/no actions, executes any trades himself at the broker, and the engine journals outcomes so the thesis is measurable over the 10-year horizon.

Why now: capital is small (~$3,000) and the IPS is written; the cheapest time to put the discipline in place is before the first trade.

## 2. Goals, non-goals, anti-goals

**Goals**
- G1. A weekly memo arrives without Emmet asking for it, costs him ≤15 minutes, and ends in explicit yes/no actions (or "no action").
- G2. Every action is IPS-checked before it reaches the memo (allocation targets, 5% per-name cap, 30% active cap, fee cap, allowable-instrument list, no margin/leverage).
- G3. The active sleeve is researched with the finance skills (valuation, estimates, earnings, correlation, liquidity) rather than from memory.
- G4. A decision ledger and performance record exist from day one so "active sleeve vs. indexed benchmark" is answerable at any time.
- G5. A small, tested toolkit (screener, portfolio math, memo renderer) exists as code so the weekly run is cheap and repeatable.
- G6. A quarterly deep dive uses the richer, machine-bound tools (social readers, TradingView) for color the weekly run can't get.

**Non-goals**
- Trade execution or broker API integration (every finance skill is read-only by design; execution stays manual).
- Options, futures, leverage, margin, shorting, crypto — all excluded by the IPS.
- Intraday or daily trading. The engine is weekly by design.
- Replacing the IPS. The engine enforces it; changing allocation or constraints is a human decision made by editing the IPS.

**Anti-goals (failure even if it "works")**
- Overtrading a $3k book: churn, spreads and tax lots eating returns. Target: single-digit trades per quarter.
- Memo bloat: a 6-page weekly report nobody reads.
- Silent IPS drift: the engine recommending something the IPS forbids because a check wasn't coded.
- An unmeasurable thesis: no benchmark, so in year 3 nobody can say whether the effort paid.

## 3. Constraints (from the IPS and the environment)

| Constraint | Source | Engine interpretation |
|---|---|---|
| 50% stocks / 30% bonds / 20% cash | IPS §III | Target weights; measured on total portfolio value including cash at broker |
| Individual stocks ≤30% of portfolio; no single name >5% | IPS §IV | Active sleeve ≤30%; indexed equity sleeve therefore ≥20%; per-name cap 5% → ≤6 names |
| ≥50% of the stock portion indexed; active ≤50% | IPS §V | Consistent with above; indexed equity must be ≥ active equity |
| Funds must cost <0.9% | IPS §IV | Hard filter on expense ratio |
| Allowable: ETFs, index funds, individual stocks, commodities, REITs. Not: margin, leveraged ETFs, futures, hedge funds, private loans, private real estate | IPS §IV | Hard allow/deny list; "venture capital / private equity" listed as allowable but impractical at $3k — treated as not in scope |
| Index funds: bottom-quartile fees, no lockups/gates, transparent | IPS §V | Screening rules for the indexed sleeve |
| Semi-quarterly review; rebalance if any position is 5% off target | IPS §VI | Weekly memo = monitoring. Formal review every 6 weeks (twice per quarter). Band = 5 percentage points of portfolio weight, absolute. Rebalance recommended only at a review date or when a band is breached. |
| Capital gains review quarterly | IPS §VI | Folded into the quarterly deep dive |
| ~$3,000 starting capital | IPS §III note | Fractional shares required at the broker; per-name positions ≈ $150 |
| Horizon 10 years; withdrawals begin in year 5 | IPS §II | Glide-path question deferred to year 4 review; noted in ledger |
| Taxable brokerage account | Emmet, 2026-11-20 | Tax lots matter: sells prefer long-term lots; quarterly capital-gains-distribution check is real work; turnover has a tax cost |
| No scheduled contributions; fixed ~$3k for now | Emmet, 2026-11-20 | Rebalancing can only be done with sells or cash; benchmark needs no flow handling; if deposits start later, Performance tab gains a flows column |
| Broker offers fractional shares for stocks but not for bond ETFs; Emmet keeps final control and executes every trade himself | Emmet, 2026-11-20 | Stock/ETF sizing can be exact to the dollar; bond sleeve is sized in whole shares and allowed to sit within ±1 share of target; engine advises only, never connects to the broker |
| Finance skills are read-only; yfinance works in the cloud; opencli/TradingView/Discord/Twitter readers need Emmet's PC | skills repo | Weekly run is cloud-scheduled; quarterly deep dive runs from the PC |

## 4. Approaches considered

| | A. Cloud-scheduled only | B. GSD-run local operation | **C. Hybrid (chosen)** |
|---|---|---|---|
| Weekly memo | Scheduled task in cloud | Manual `/gsd-quick` each week | Scheduled task in cloud |
| Toolkit | None; skills run ad hoc each time | Built implicitly inside GSD phases | Built once with GSD as a tested Python package, then called by the weekly run |
| Social/TradingView readers | Not available | Available | Available in quarterly deep dive from PC |
| Token cost | Lowest | Highest (research/plan/execute/verify fan-out every week) | Front-loaded build, then ≈A + quarterly spikes |
| Why not | No upgrade path; recomputes everything from scratch weekly | GSD is built for shipping code; bending its loop to a 6-name sleeve adds friction, not value | — |

Emmet chose C for the fuller capability and the upgrade path, accepting the front-loaded build cost.

## 5. The design

### 5.1 Three components

```
┌──────────────────────────────────────────────────────────────┐
│ 1. fund-toolkit (Python, built with GSD, lives on Emmet's PC  │
│    and in this workspace)                                     │
│    data/      yfinance pulls + on-disk cache                  │
│    ips/       IPS rules as code: allocation, caps, allow-list │
│    portfolio/ holdings, drift, band checks, rebalance math    │
│    screen/    Graham-style value screen + SEPA stage filter   │
│    research/  the finance-skill math as tested Python:        │
│               DCF/multiples, revision metrics, correlation,   │
│               liquidity, ETF premium (recipes → code)         │
│    memo/      renders the weekly memo + ledger entries        │
└──────────────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────────────┐
│ 2. Weekly run (scheduled task, cloud, Saturday morning)       │
│    reads ledger → pulls data → IPS checks → research →        │
│    writes memo to AI Fund project + updates Sheet             │
└──────────────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────────────┐
│ 3. Quarterly deep dive (on PC, Emmet-triggered)               │
│    social sentiment, TradingView TA, re-underwrite every      │
│    holding, capital-gains check, thesis scorecard             │
└──────────────────────────────────────────────────────────────┘
```

### 5.2 State (single source of truth)

A Google Sheet, **Oaklink Fund Ledger**, in Emmet's Drive (Google Drive is connected; scheduled tasks can write to it; Emmet can edit from his phone). Tabs:

- **Holdings** — ticker, sleeve (indexed-equity / active / bonds / cash), shares, cost basis, lot date. **Source of truth for the engine.** It is updated only from Decision Ledger rows Emmet marks `done` with shares and fill price; Emmet can also correct it directly. No broker connection (Emmet's call: advise only, he executes).
- **Targets** — the IPS allocation and caps, copied from the IPS so the engine never hardcodes them.
- **Watchlist** — ≤10 candidate names for the active sleeve with thesis one-liner, last valuation date, implied upside, status.
- **Decision Ledger** — one row per recommended action: date, action, ticker, size, rationale (one line), IPS check result, Emmet's response (done / skipped / modified), fill price, outcome fields filled in later.
- **Performance** — weekly snapshot: total value, each sleeve's value and return, benchmark returns (see 5.6).

Weekly memos are written as dated docs in the AI Fund project (`memos/YYYY-MM-DD.md`) so they're readable from any device and searchable.

### 5.3 The weekly run

Runs Saturday ~07:00 Pacific as a scheduled task in the cloud workspace. Steps, in order; each produces one memo section, and the whole memo is one page:

1. **Reconcile.** Read the Decision Ledger for last week's actions. Emmet marks each `done` (with shares + fill price) or `skipped` in the Sheet — chat replies don't reach a scheduled run, so the Sheet is the only channel. `done` rows update Holdings. Unanswered items are carried forward once, then dropped with a note.
2. **Pull data.** Prices and basic info for all holdings, watchlist, and benchmark ETFs via `yfinance-data`. Cache to disk so later steps don't re-pull.
3. **IPS compliance.** Compute current weights; flag any sleeve more than 5 pts off target, active sleeve >30%, any fund with expense ratio ≥0.9%. The 5% per-name cap is a **purchase-time** limit (no buy may take a name above 5%); after purchase a name is only flagged for trimming when it drifts past **7.5%** (1.5× cap) or sits above 6% at a 6-week review — so winners aren't sold on every uptick. This section is always present, even when all green.
4. **Indexed + bond sleeve monitor.** `etf-premium` snapshot for the index/bond ETFs held (flag premium beyond the bid-ask spread), fee check, and a one-line "nothing to do" when nothing is wrong.
5. **Active sleeve monitor.** For each held name: price change, `estimate-analysis` revision direction (up/down/flat over 30 days), any earnings in the next 7 days → run `earnings-preview`; any earnings in the last 7 days → run `earnings-recap`. Re-run `company-valuation` only when an earnings event happened or the price moved >15% since the last valuation (keeps cost down).
6. **Watchlist screen.** Run the Graham-style screen (see 5.5) over the watchlist; for names passing, show implied upside from the cached valuation. Add new candidates only at the 6-week review, not weekly.
7. **Actions.** Zero to three proposed actions, each as one line: `BUY/SELL/REBALANCE — ticker — $amount — reason — IPS check: PASS`. A proposed buy must pass: IPS caps, valuation upside ≥25% (Graham margin of safety), not in SEPA Stage 4, `stock-correlation` ≤0.8 with existing active names, `stock-liquidity` spread <0.5%. If nothing qualifies, the memo says "No action" and why.
8. **Ledger + performance update.** Append to Decision Ledger and Performance; write memo.

Memo length: one page. Sections 3 and 7 are mandatory; others collapse to a line when quiet.

Every 6th weekly run is a **review run**: it additionally re-underwrites all active names with `company-valuation`, re-runs the screen over a broader universe (via `yfinance` screener: US equities, market cap > $2B, P/E < 20, debt/equity < 1, positive 5-yr earnings growth — Graham-inspired defaults, tunable in Targets) to refresh the watchlist, and proposes rebalancing if any band is breached.

### 5.4 Quarterly deep dive (on the PC)

Triggered by Emmet from his machine, where opencli and TradingView run. Produces a 2–3 page quarterly report in the project:

- Thesis scorecard (5.6).
- Per-holding re-underwrite with `company-valuation`, `estimate-analysis`, `tradingview-mcp` TA readout, and `finance-sentiment`/`twitter-reader` color (optional; skipped if not set up).
- `stock-correlation` matrix of the active sleeve + indexed ETF to confirm diversification.
- Capital-gains distribution check for held funds (IPS §VI).
- IPS review prompt: anything in the IPS that experience says should change (surfaced as a question for Emmet, never auto-changed).
- Backlog of ideas captured during the quarter (seeds), using GSD's capture/backlog pattern.

### 5.5 Investment philosophy encoded in the screen

The IPS cites Graham. The skills repo's `sepa-strategy` is Minervini momentum — a different school. Resolution (my call, to be confirmed):

- **What to buy** is decided by value: `company-valuation` blended fair value with ≥25% upside, plus quality filters (positive FCF, debt/equity <1, 5-yr earnings growth >0).
- **When not to buy** uses SEPA's stage analysis as a negative filter only: no new buys in Stage 4 (price below declining 200-day MA). SEPA's trend template, VCP patterns, pivot entries, and stop-loss rules are **not** used — they are a trading system, not an investing one, and conflict with the IPS's buy-and-hold horizon.
- **When to sell**: price ≥ fair value (thesis complete), thesis broken (estimate revisions negative two reviews running and valuation no longer shows upside), or the per-name trim threshold in 5.3 step 3. No stop-losses. In this taxable account, trims and rebalances prefer lots held >12 months and the memo states the estimated tax consequence of any sell.
- `estimate-analysis` revision breadth is used as a tiebreaker between candidates, not a trigger.

### 5.6 Measuring the thesis

The Graham claim is only testable against a passive counterfactual. The Performance tab tracks, weekly:

- Total portfolio return vs. a **passive 50/30/20 benchmark** (VTI / BND / cash at a money-market yield) rebalanced on the same schedule.
- **Active sleeve return vs. the index ETF it was funded from**, over the same dollars from the day each name was bought — the direct test of "effort." Because every active buy is funded by selling the index ETF, the counterfactual is exact.
- Turnover and estimated trading cost per quarter (anti-overtrading check).
- An "effort log": tokens/runs spent per quarter, so cost of effort is visible alongside its return.

The quarterly scorecard reports these; the first meaningful read is at 12 months.

### 5.7 Initial portfolio (the first review run)

The first run is a review run with an empty Holdings tab. It proposes:
- Indexed equity sleeve: one broad US ETF (my call: US-only; Emmet didn't specify international and a $3k book doesn't need a second line). It holds **all 50% of the stock allocation on day one**, including the part the active sleeve hasn't claimed yet.
- Active sleeve (ceiling 30%): starts **empty**. The IPS's 30% is treated as a ceiling, not a target. When a name qualifies (5.3 step 7), the memo proposes selling that dollar amount of the index ETF to fund it — one sell, one buy, stocks stay at 50% throughout. This is the resolution to the "undeployed active allocation" problem: it is never cash and never a red compliance flag.
- Bond sleeve (30%): one aggregate bond ETF bought in whole shares (broker doesn't offer fractional bonds). At ~$900 that is roughly 9–12 shares depending on the ETF; the engine treats ±1 share as on-target. This resolves the IPS note ("not sure which bonds I can buy") — ETFs, not individual bonds, at this size.
- Cash (20%): left at the broker or in its money-market sweep; the engine treats cash as a sleeve with a target, not leftover.

### 5.8 Building the toolkit with GSD

GSD is used for what it's built for — shipping tested code — not as the fund's runtime:

- `/gsd-new-project` in `C:\Users\emmet_i8nmp2p\Claude\Projects\AI Fund\fund-toolkit` with this document as the idea input.
- Phases (planner will refine): (1) IPS rules + portfolio math with tests; (2) data layer with cache; (3a) screen + valuation math (DCF, peer multiples, blended fair value, sensitivity) as tested Python, ported from the company-valuation recipe; (3b) estimate-revision, correlation, liquidity and ETF-premium metrics, ported from their recipes; (4) memo renderer + Sheet I/O; (5) scheduled-run entrypoint + dry-run mode (dry-run = full run against the Sheet with writes disabled, output to stdout).
- Division of labour in the weekly run (my call, from the builder check): **all arithmetic is Python** — weights, caps, DCF, multiples, revision ratios, correlations, impact estimates. The AI session that runs the task only does judgment and prose: choosing peers when the default peer map is thin, deciding what to watch into an earnings print, and writing the memo. That keeps the tested surface where errors hurt (money math) and the recurring token cost near Approach A.
- `/gsd-execute-phase` with `--tdd`; `/gsd-verify-work` before each phase ships. Correctness of IPS math and caps is the thing tests must nail — a wrong cap check is the silent-drift anti-goal.
- Once phase 5 ships, the scheduled task is created and the toolkit is installed in the cloud workspace too.

Weekly runs after that are plain scheduled tasks — no GSD subagent fan-out — which is what keeps the recurring token cost near approach A.

## 6. Left to the builder

- Exact Python package layout, testing framework, and cache format.
- How the scheduled task authenticates to the Google Sheet (Drive connector is available; fall back to a Sheets artifact if a scheduled session can't reach it).
- Memo template wording and the exact one-page layout.
- Screener thresholds beyond the Graham-inspired defaults above (recorded in the Targets tab so they're tunable without code).
- Broker-specific details — the design assumes fractional shares are available.
- How to install the toolkit in the cloud workspace for the scheduled run (pip from a zip or a private repo).

## 7. Decisions log

Emmet's decisions: ongoing engine (not one-off); weekly memo; approach C; taxable brokerage; fixed $3k, no contributions; broker has fractional stocks but not bond ETFs; Emmet keeps final control and executes all trades himself.

My calls (Emmet can override): all math in tested Python, AI does interpretation only (5.8); Holdings tab is source of truth, updated from `done` ledger rows, no broker link (5.2); 5% cap is purchase-time with a 7.5% trim threshold (5.3); undeployed active allocation sits in the index ETF and active 30% is a ceiling (5.7); US-only index ETF; memo delivered as a project doc only, no email; bond sleeve in whole shares with ±1-share tolerance.
