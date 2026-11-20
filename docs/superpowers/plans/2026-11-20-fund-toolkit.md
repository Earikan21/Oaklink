# Oaklink Fund Toolkit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `fundkit`, a tested Python package that turns the Oaklink IPS into code — compliance checks, portfolio math, a Graham-style screen, the finance-skill research math, and a one-page weekly memo — and wire it to a Saturday scheduled run plus a quarterly deep-dive runbook.

**Architecture:** A pure-Python library with CSV-backed state tables (Holdings, Targets, Watchlist, Ledger, Performance) and a thin CLI. All money math lives in tested modules; the AI session that runs the weekly task only syncs the CSVs with the Google Sheet, interprets results, and writes prose. Market data comes from yfinance through one cached fetch layer so every research module is testable against fixtures.

**Tech Stack:** Python 3.11+, `yfinance`, `pandas`, `numpy`, `pytest`, `pydantic` (models), `typer` (CLI). No broker, no Google auth inside the package.

**Spec:** `claude/oaklink-fund-engine-design.md` in the AI Fund project (local copy: `docs/2026-11-19-oaklink-fund-engine-design.md`). Repo root: `C:\Users\emmet_i8nmp2p\Claude\Projects\AI Fund\fund-toolkit`.

## Global Constraints

- Targets: stocks 50% / bonds 30% / cash 20% of total portfolio value including cash (spec §3).
- Sleeve drift band: 5 percentage points of portfolio weight, absolute (spec §3).
- Active sleeve ceiling 30% of portfolio; indexed equity must be ≥ active equity (spec §3, §5.7).
- Per-name cap 5% at purchase; trim flag when a name exceeds 7.5% any week, or exceeds 6% on a review run (spec §5.3 step 3).
- Fund expense ratio must be < 0.9% (spec §3).
- Allowable instruments: ETF, index mutual fund, individual stock, commodity ETF, REIT. Denied: margin, leveraged/inverse ETF, futures, hedge fund, private loan, private real estate (spec §3).
- Buy gate: valuation upside ≥ 25%, not SEPA Stage 4, correlation ≤ 0.80 with every held active name, bid-ask spread < 0.5% (spec §5.3 step 7).
- Revalue a held name only on an earnings event or a price move > 15% since last valuation (spec §5.3 step 5).
- Every 6th weekly run is a review run (spec §5.3).
- Review-run screener universe: US equities, market cap > $2B, P/E < 20, debt/equity < 1, 5-yr EPS growth > 0; quality filters FCF > 0, debt/equity < 1 (spec §5.3, §5.5).
- Bond ETF sized in whole shares; ±1 share counts as on-target (spec §5.7).
- Taxable account: sells prefer lots held > 365 days; memo states estimated tax consequence of every sell (spec §5.5).
- No contributions; benchmark is passive 50/30/20 (VTI / BND / cash at money-market yield) rebalanced on the same schedule (spec §5.6).
- The package never places trades and never connects to a broker (spec §2 non-goals).
- Memo is one page; sections "IPS compliance" and "Actions" are always present (spec §5.3).
- Package reads/writes only CSV under a `data/` directory; Google Sheet sync is done by the AI session, not the package (plan decision, spec §6 left-to-builder).

## Review Focus

1. **Holdings tab with a ticker yfinance can't price** (delisted, typo). Expected: compliance still runs on the rest, the memo names the bad ticker, nothing crashes. Tests added to Task 5 (`test_unpriced_ticker_reported_not_fatal`) and Task 12 (`test_unpriced_tickers_named`).
2. **Market value drifts so that both a sleeve band and a per-name trim fire in the same week.** Expected: one coherent action list, trim first, then rebalance using post-trim weights. Test added to Task 11.
3. **Negative or missing financials in valuation** (negative EBITDA, no beta, WACC ≤ g). Expected: that method is skipped or capped and flagged; the blend uses the remaining methods; never a NaN implied price. Test added to Task 6.
4. **Ledger row marked `done` without shares or fill price.** Expected: reconcile refuses to update Holdings for that row and the memo asks for the missing fields. Test added to Task 3.
5. **First run with empty Holdings and $3,000 cash.** Expected: the review run proposes exactly two buys (index ETF ≈ $1,500, bond ETF whole shares ≈ $900; the remaining ~$600 stays as cash), no active buys, compliance green after the proposed trades. Test added to Task 11.

---

## File Structure

```
fund-toolkit/
  pyproject.toml
  README.md
  data/                      # CSV state tables (gitignored except data/examples/)
    holdings.csv  targets.csv  watchlist.csv  ledger.csv  performance.csv
    cache/                   # yfinance cache (gitignored)
  src/fundkit/
    __init__.py
    models.py                # pydantic models for every table row + market snapshot
    store.py                 # CSV load/save per table
    ips.py                   # IPS rules: allow-list, fee cap, caps, bands
    portfolio.py             # weights, drift, whole-share sizing, rebalance proposals
    market.py                # yfinance fetch + on-disk cache; MarketData protocol
    research/
      __init__.py
      valuation.py           # DCF, relative, blend, sensitivity (company-valuation recipe)
      estimates.py           # revision metrics (estimate-analysis recipe)
      correlation.py         # pairwise/log-return correlation (stock-correlation recipe)
      liquidity.py           # spread, ADTV, Amihud, sqrt impact (stock-liquidity recipe)
      etf.py                 # premium/discount vs NAV (etf-premium recipe)
      stage.py               # SEPA stage classifier (sepa-strategy recipe, stage only)
    screen.py                # Graham screen + buy gate
    reconcile.py             # ledger → holdings updates
    actions.py               # assemble proposed actions for the week
    performance.py           # snapshot + passive benchmark
    memo.py                  # render one-page markdown memo
    run.py                   # orchestrates a weekly run; review-run detection
    cli.py                   # typer app: init, reconcile, run, screen
  tests/
    conftest.py              # FakeMarket fixture (dict-backed MarketData)
    fixtures/                # small CSV/JSON fixtures
    test_models.py test_store.py test_ips.py test_portfolio.py test_market.py
    test_valuation.py test_estimates.py test_correlation.py test_liquidity.py
    test_etf.py test_stage.py test_screen.py test_reconcile.py test_actions.py
    test_performance.py test_memo.py test_run.py test_cli.py
  docs/
    weekly-run-prompt.md     # the scheduled task's instructions (Task 15)
    quarterly-deep-dive.md   # runbook for the PC session (Task 15)
```

---

### Task 1: Project scaffold and models

**Files:**
- Create: `pyproject.toml`, `README.md`, `.gitignore`, `src/fundkit/__init__.py`, `src/fundkit/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Produces (all pydantic `BaseModel`, in `models.py`):
  - `Sleeve = Literal["indexed_equity","active","bonds","cash"]`
  - `Holding(ticker:str, sleeve:Sleeve, shares:float, cost_basis:float, lot_date:date)` — `ticker="CASH"` with `shares` = dollars for the cash sleeve.
  - `Targets(stocks:float=0.50, bonds:float=0.30, cash:float=0.20, band:float=0.05, active_ceiling:float=0.30, name_cap:float=0.05, trim_threshold:float=0.075, review_trim:float=0.06, fee_cap:float=0.009, min_upside:float=0.25, max_corr:float=0.80, max_spread:float=0.005, reval_move:float=0.15, review_every:int=6, index_etf:str="VTI", bond_etf:str="BND")` with a validator that `stocks+bonds+cash == 1.0` (±1e-9).
  - `WatchItem(ticker:str, thesis:str, last_valued:date|None, fair_value:float|None, upside:float|None, status:Literal["candidate","passed","rejected","held"])`
  - `LedgerRow(id:str, date:date, action:Literal["BUY","SELL","REBALANCE","NOTE"], ticker:str, amount:float, rationale:str, ips_check:Literal["PASS","FAIL"], response:Literal["pending","done","skipped","modified"]="pending", fill_shares:float|None=None, fill_price:float|None=None, est_tax:float|None=None)`
  - `Snapshot(date:date, total:float, indexed_equity:float, active:float, bonds:float, cash:float, bench_total:float, active_vs_index:float|None, turnover_qtd:float)`
  - `Quote(ticker:str, price:float, bid:float|None, ask:float|None, expense_ratio:float|None, nav:float|None, quote_type:str, avg_volume:float|None)`

- [ ] **Step 1: Write the failing tests**

```python
def test_targets_default_sum_to_one():
    assert Targets().stocks + Targets().bonds + Targets().cash == pytest.approx(1.0)

def test_targets_reject_bad_sum():
    with pytest.raises(ValidationError):
        Targets(stocks=0.6, bonds=0.3, cash=0.2)

def test_cash_holding_uses_shares_as_dollars():
    h = Holding(ticker="CASH", sleeve="cash", shares=600.0, cost_basis=1.0, lot_date=date(2026,11,22))
    assert h.shares == 600.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_models.py -v` — Expected: FAIL, `ModuleNotFoundError: fundkit`

- [ ] **Step 3: Create `pyproject.toml`** (name `fundkit`, `src` layout, deps `yfinance>=0.2.50, pandas, numpy, pydantic>=2, typer`, dev `pytest`), `.gitignore` (`data/*.csv`, `data/cache/`, keep `data/examples/`), and `models.py` with the models above.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pip install -e ".[dev]" && pytest tests/test_models.py -v` — Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git init && git add -A && git commit -m "feat: scaffold fundkit with IPS models"
```

---

### Task 2: CSV store

**Files:**
- Create: `src/fundkit/store.py`, `data/examples/*.csv` (one header-only file per table plus `targets.csv` with defaults)
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: models from Task 1.
- Produces: `class Store(root: Path)` with `load_holdings()->list[Holding]`, `save_holdings(list[Holding])`, `load_targets()->Targets`, `save_targets(Targets)`, `load_watchlist()->list[WatchItem]`, `save_watchlist(...)`, `load_ledger()->list[LedgerRow]`, `append_ledger(rows:list[LedgerRow])`, `load_performance()->list[Snapshot]`, `append_performance(Snapshot)`, `init(from_examples:bool=True)` which copies `data/examples/` into `root` without overwriting existing files.

- [ ] **Step 1: Write the failing tests**

```python
def test_roundtrip_holdings(tmp_path):
    s = Store(tmp_path); s.init()
    rows = [Holding(ticker="VTI", sleeve="indexed_equity", shares=5.25, cost_basis=285.0, lot_date=date(2026,11,22))]
    s.save_holdings(rows)
    assert s.load_holdings() == rows

def test_init_does_not_overwrite(tmp_path):
    s = Store(tmp_path); s.init()
    s.save_targets(Targets(index_etf="ITOT"))
    s.init()
    assert s.load_targets().index_etf == "ITOT"

def test_append_ledger_preserves_order(tmp_path):
    s = Store(tmp_path); s.init()
    s.append_ledger([LedgerRow(id="a", date=date(2026,11,22), action="NOTE", ticker="", amount=0, rationale="x", ips_check="PASS")])
    s.append_ledger([LedgerRow(id="b", date=date(2026,11,29), action="NOTE", ticker="", amount=0, rationale="y", ips_check="PASS")])
    assert [r.id for r in s.load_ledger()] == ["a","b"]
```

- [ ] **Step 2: Run tests to verify they fail** — `pytest tests/test_store.py -v` → FAIL, `ImportError`
- [ ] **Step 3: Implement `Store`** using `csv.DictReader/DictWriter`; serialize dates as ISO strings; `targets.csv` is a two-column key/value file.
- [ ] **Step 4: Run tests to verify they pass** — Expected: 3 passed
- [ ] **Step 5: Commit** — `git commit -am "feat: CSV-backed state store"`

---

### Task 3: Reconcile ledger → holdings

**Files:**
- Create: `src/fundkit/reconcile.py`
- Test: `tests/test_reconcile.py`

**Interfaces:**
- Consumes: `Store`, `LedgerRow`, `Holding`.
- Produces: `reconcile(store:Store, today:date) -> ReconcileResult` where `ReconcileResult(applied:list[str], carried:list[str], dropped:list[str], incomplete:list[str])` (ledger ids). Rules: `done` rows with `fill_shares` and `fill_price` update Holdings (BUY adds a lot; SELL reduces the oldest lot first — FIFO — and removes empty lots; REBALANCE is a SELL then BUY pair expressed as two rows). `done` rows missing either fill field go to `incomplete` and are left `pending`. `pending` rows dated ≤ 7 days ago are `carried`; older than 7 days are set `skipped` and listed in `dropped`. Cash holding adjusts by ±`fill_shares*fill_price`.

- [ ] **Step 1: Write the failing tests**

```python
def test_done_buy_adds_lot_and_reduces_cash(store_with_cash_3000):
    s = store_with_cash_3000
    s.append_ledger([LedgerRow(id="b1", date=d(-3), action="BUY", ticker="VTI", amount=1500, rationale="", ips_check="PASS",
                               response="done", fill_shares=5.0, fill_price=300.0)])
    r = reconcile(s, today=d(0))
    assert r.applied == ["b1"]
    h = {x.ticker: x for x in s.load_holdings()}
    assert h["VTI"].shares == 5.0 and h["CASH"].shares == 1500.0

def test_done_without_fill_is_incomplete(store_with_cash_3000):
    s = store_with_cash_3000
    s.append_ledger([LedgerRow(id="b2", date=d(-3), action="BUY", ticker="VTI", amount=1500, rationale="", ips_check="PASS", response="done")])
    r = reconcile(s, today=d(0))
    assert r.incomplete == ["b2"] and s.load_holdings()[0].ticker == "CASH"

def test_pending_older_than_7_days_is_dropped(store_with_cash_3000):
    s = store_with_cash_3000
    s.append_ledger([LedgerRow(id="p1", date=d(-8), action="BUY", ticker="BND", amount=900, rationale="", ips_check="PASS")])
    r = reconcile(s, today=d(0))
    assert r.dropped == ["p1"] and s.load_ledger()[0].response == "skipped"

def test_sell_is_fifo():
    # two VTI lots (2026-01-01: 2 sh, 2026-06-01: 3 sh); SELL done 2.5 sh → first lot gone, second has 2.5
```

- [ ] **Step 2: Run tests to verify they fail** — Expected: FAIL, `ImportError`
- [ ] **Step 3: Implement `reconcile`** in `reconcile.py`; add `conftest.py` fixture `store_with_cash_3000` (tmp Store with one `CASH` holding of 3000) and helper `d(offset_days)`.
- [ ] **Step 4: Run tests to verify they pass** — Expected: 4 passed
- [ ] **Step 5: Commit** — `git commit -am "feat: reconcile ledger responses into holdings"`

---

### Task 4: Market data layer with cache

**Files:**
- Create: `src/fundkit/market.py`
- Test: `tests/test_market.py`, `tests/conftest.py` (add `FakeMarket`)

**Interfaces:**
- Produces: `class MarketData(Protocol)` with `quote(ticker)->Quote`, `history(ticker, period="1y")->pd.DataFrame` (columns Open/High/Low/Close/Volume, tz-naive index), `info(ticker)->dict`, `income_stmt(ticker)->pd.DataFrame`, `cashflow(ticker)->pd.DataFrame`, `balance_sheet(ticker)->pd.DataFrame`, `earnings_estimate(ticker)->pd.DataFrame`, `eps_trend(ticker)->pd.DataFrame`, `eps_revisions(ticker)->pd.DataFrame`, `earnings_dates(ticker)->pd.DataFrame`, `screen(query:dict)->list[str]`.
- `class YFMarket(cache_dir:Path, ttl_hours:int=20)` implementing it with yfinance, caching each call to `cache_dir/<ticker>/<method>.pkl` keyed by date; raises `MarketUnavailable(ticker)` when yfinance returns empty/None for `quote`.
- `class FakeMarket(data:dict)` in `conftest.py` implementing the protocol from dicts/DataFrames for tests.

- [ ] **Step 1: Write the failing tests**

```python
def test_quote_cached_within_ttl(tmp_path, monkeypatch):
    calls = {"n": 0}
    def fake_ticker(t): calls["n"] += 1; return _StubTicker(price=100.0)
    monkeypatch.setattr("fundkit.market.yf.Ticker", fake_ticker)
    m = YFMarket(tmp_path)
    assert m.quote("VTI").price == 100.0
    assert m.quote("VTI").price == 100.0
    assert calls["n"] == 1

def test_unpriceable_ticker_raises(tmp_path, monkeypatch):
    monkeypatch.setattr("fundkit.market.yf.Ticker", lambda t: _StubTicker(price=None))
    with pytest.raises(MarketUnavailable):
        YFMarket(tmp_path).quote("ZZZZ")
```

- [ ] **Step 2: Run tests to verify they fail** — Expected: FAIL, `ImportError`
- [ ] **Step 3: Implement `MarketData`, `YFMarket`, `MarketUnavailable`**; `quote` reads `fast_info.last_price` then `info` for bid/ask/`netExpenseRatio`/`navPrice`/`quoteType`/`averageVolume`; `history` strips tz with `.tz_localize(None)`.
- [ ] **Step 4: Run tests to verify they pass** — Expected: 2 passed
- [ ] **Step 5: Commit** — `git commit -am "feat: cached yfinance market layer"`

---

### Task 5: IPS rules and portfolio math

**Files:**
- Create: `src/fundkit/ips.py`, `src/fundkit/portfolio.py`
- Test: `tests/test_ips.py`, `tests/test_portfolio.py`

**Interfaces:**
- Consumes: `Holding`, `Targets`, `Quote`, `MarketData`.
- Produces in `portfolio.py`:
  - `value_holdings(holdings, market) -> tuple[dict[str,float], list[str]]` — `{ticker: market_value}` (CASH = shares) and a list of tickers that raised `MarketUnavailable`.
  - `sleeve_weights(holdings, values) -> dict[Sleeve,float]` (fractions of total).
  - `name_weights(holdings, values) -> dict[str,float]` for active-sleeve tickers only.
  - `whole_share_target(dollars:float, price:float) -> int` (round to nearest, min 0).
  - `rebalance_proposals(holdings, values, targets, prices, review:bool) -> list[Proposal]` where `Proposal(action, ticker, dollars, shares, reason)`; proposes only when a sleeve is outside `targets.band`; bond ETF in whole shares with ±1-share tolerance; index ETF absorbs the stock-sleeve correction.
- Produces in `ips.py`:
  - `ALLOWED_TYPES = {"ETF","MUTUALFUND","EQUITY"}`; `DENIED_KEYWORDS = ("leveraged","2x","3x","ultra","inverse","short","futures")`.
  - `check_instrument(q:Quote, info:dict) -> list[str]` — violations: fee ≥ `fee_cap`, quote type not allowed, denied keyword in `longName`.
  - `compliance(holdings, values, targets, review:bool) -> ComplianceReport(sleeve_flags:list[str], active_ceiling_flag:str|None, trim_flags:list[str], indexed_lt_active:bool, unpriced:list[str])` — trim flags use `trim_threshold` weekly and `review_trim` on review runs.
  - `buy_allowed(ticker, dollars, holdings, values, targets) -> tuple[bool,str]` — false if post-trade name weight > `name_cap`, active sleeve > `active_ceiling`, or indexed < active.

- [ ] **Step 1: Write the failing tests**

```python
def test_weights_include_cash(three_k_portfolio):          # VTI 1500, BND 900, CASH 600
    w = sleeve_weights(*three_k_portfolio)
    assert w == pytest.approx({"indexed_equity":0.5,"active":0.0,"bonds":0.3,"cash":0.2})

def test_no_flags_when_on_target(three_k_portfolio):
    rep = compliance(*three_k_portfolio, Targets(), review=False)
    assert rep.sleeve_flags == [] and rep.trim_flags == []

def test_sleeve_flag_outside_5pt_band():                      # stocks at 56%
    ...assert rep.sleeve_flags == ["stocks 56.0% vs 50% (+6.0 pts)"]

def test_trim_threshold_7_5_weekly_6_review():
    # active name at 7.0%: no flag weekly, flag on review; at 7.6%: flag both

def test_buy_refused_above_name_cap(three_k_portfolio):
    ok, why = buy_allowed("AAPL", 160.0, *three_k_portfolio[:2], Targets())   # 160/3000 = 5.33%
    assert not ok and "5%" in why

def test_fee_cap_violation():
    assert check_instrument(Quote(ticker="X", price=10, bid=None, ask=None, expense_ratio=0.009, nav=None, quote_type="ETF", avg_volume=None), {"longName":"X Fund"}) == ["expense ratio 0.90% >= 0.90% cap"]

def test_leveraged_denied():
    assert "leveraged" in check_instrument(..., {"longName":"ProShares UltraPro QQQ"})[0].lower()

def test_whole_share_bond_tolerance():
    assert whole_share_target(900, 75.3) == 12
    # holdings with 11 BND shares vs target 12 → no proposal

def test_unpriced_ticker_reported_not_fatal(three_k_portfolio_plus_zzzz, fake_market_missing_zzzz):
    values, unpriced = value_holdings(...)
    assert unpriced == ["ZZZZ"] and "VTI" in values
```

- [ ] **Step 2: Run tests to verify they fail** — Expected: FAIL, `ImportError`
- [ ] **Step 3: Implement `portfolio.py` then `ips.py`**; sleeve-flag string format exactly `"{sleeve} {pct:.1f}% vs {target:.0f}% ({signed:+.1f} pts)"` where `stocks` = indexed_equity + active.
- [ ] **Step 4: Run tests to verify they pass** — Expected: 9 passed
- [ ] **Step 5: Commit** — `git commit -am "feat: IPS rules and portfolio math"`

---

### Task 6: Valuation (company-valuation recipe)

**Files:**
- Create: `src/fundkit/research/__init__.py`, `src/fundkit/research/valuation.py`
- Test: `tests/test_valuation.py`, `tests/fixtures/valuation_acme.json`

**Interfaces:**
- Consumes: `MarketData`.
- Produces: `value_company(ticker, market, peers:list[str], rf:float|None=None) -> Valuation` where `Valuation(ticker, price, dcf:float|None, relative:float|None, blended:float, upside:float, wacc:float, g:float, tv_share:float, sensitivity:dict[tuple[float,float],float], flags:list[str], confidence:Literal["high","medium","low"])`. Defaults per the recipe: horizon 5, g=0.025, rf from `^TNX`/100 else 0.045 (flag "stale_rf"), ERP 0.055, kd = interest/debt else 0.055, tax = 3-yr median effective clamped [0.15,0.30] else 0.21, margins = 3-yr medians, TV = mean(Gordon, 15× EBITDA exit), peer median multiples (fwd P/E, EV/Rev, EV/EBITDA), relative = nanmedian of implied prices. Blend 50/50 when both exist; if WACC ≤ g set g = wacc − 0.005 and flag `g_capped`; skip EV/EBITDA when EBITDA < 0, skip P/E when EPS < 0; `tv_share` outside [0.45, 0.85] adds flag `tv_share`; `confidence="low"` when < 3 years of statements or a method was dropped.

- [ ] **Step 1: Write the failing tests**

```python
def test_acme_blended_and_upside(fake_market_acme):      # fixture: rev 100→110→121, EBIT margin 20%, price 50, 10 sh, no debt, 20 cash
    v = value_company("ACME", fake_market_acme, peers=["P1","P2","P3"], rf=0.045)
    assert v.dcf is not None and v.relative is not None
    assert v.blended == pytest.approx(0.5*v.dcf + 0.5*v.relative)
    assert v.upside == pytest.approx(v.blended/50 - 1)
    assert len(v.sensitivity) == 25

def test_negative_ebitda_skips_multiple(fake_market_acme_neg_ebitda):
    v = value_company("ACME", ..., peers=[...])
    assert "ev_ebitda_skipped" in v.flags and not math.isnan(v.blended)

def test_wacc_below_g_is_capped(fake_market_acme_low_beta):   # beta → wacc 0.02
    v = value_company(...)
    assert "g_capped" in v.flags and v.g == pytest.approx(v.wacc - 0.005)

def test_missing_beta_uses_default_one(fake_market_acme_no_beta):
    assert "beta_default" in value_company(...).flags
```

- [ ] **Step 2: Run tests to verify they fail** — Expected: FAIL, `ImportError`
- [ ] **Step 3: Implement `value_company`** following the recipe's Step 4–7 code (yfinance row labels: `Total Revenue`, `Operating Income`, `Depreciation And Amortization`, `Capital Expenditure`, `Change In Working Capital`, `Tax Provision`, `Pretax Income`, `Diluted EPS`, `EBIT`). Build `FakeMarket` fixtures as small DataFrames in `tests/fixtures/`.
- [ ] **Step 4: Run tests to verify they pass** — Expected: 4 passed
- [ ] **Step 5: Commit** — `git commit -am "feat: DCF + relative valuation with sensitivity"`

---

### Task 7: Estimates, correlation, liquidity, ETF premium, stage

**Files:**
- Create: `research/estimates.py`, `research/correlation.py`, `research/liquidity.py`, `research/etf.py`, `research/stage.py`
- Test: `tests/test_estimates.py`, `test_correlation.py`, `test_liquidity.py`, `test_etf.py`, `test_stage.py`

**Interfaces:**
- `estimates.revision_summary(ticker, market) -> Revisions(direction:Literal["up","down","flat"], pct_30d:float, up_30d:int, down_30d:int, breadth:float|None, beat_rate_4q:float|None)` — direction from `eps_trend` current vs `30daysAgo` for period `0y`: up if > +1%, down if < −1%; `breadth = up/(up+down)` from `eps_revisions`.
- `correlation.pairwise(tickers:list[str], market, period="1y") -> pd.DataFrame` of Pearson correlations of daily log returns; `max_corr_with(ticker, held:list[str], market) -> float` (0.0 when `held` is empty).
- `liquidity.snapshot(ticker, market) -> Liquidity(spread_pct:float|None, adtv_dollars:float, amihud_e9:float, impact_bps_1pct_adv:float, grade:Literal["Very High","High","Moderate","Low","Very Low"])` — grade from the recipe's dollar-volume table; `spread_pct = (ask-bid)/mid`.
- `etf.premium(ticker, market) -> EtfPremium(price, nav, premium_pct, spread_pct:float|None, meaningful:bool)` — `meaningful = abs(premium_pct) > spread_pct` when spread known; raises `ValueError` when `quote_type != "ETF"` or nav missing.
- `stage.classify(ticker, market) -> Literal[1,2,3,4]` — Stage 2 if price > 150MA > 200MA and 200MA higher than 21 trading days ago and price > 50MA; Stage 4 if price < 200MA and 200MA lower than 21 days ago; Stage 1 if price within ±5% of a flat 200MA; else Stage 3. Needs ≥ 250 rows of history, else raises `ValueError("insufficient history")`.

- [ ] **Step 1: Write the failing tests**

```python
def test_revision_up_when_30d_gain_over_1pct(fake_market_eps_up):   # 0y current 5.10, 30daysAgo 5.00
    r = revision_summary("X", fake_market_eps_up); assert r.direction == "up" and r.pct_30d == pytest.approx(0.02)
def test_breadth_ratio():                                            # up 7, down 3 → 0.7
def test_pairwise_identity_diagonal(fake_market_two_series):          # corr(A,A)=1; corr(A,B) matches numpy
def test_max_corr_with_empty_held_is_zero()
def test_liquidity_grade_high():                                      # $100M/day, 0.05% spread → "High"
def test_amihud_scaled_e9()
def test_etf_premium_meaningful_only_beyond_spread():                 # +0.40% premium, 0.10% spread → True; +0.05%/0.10% → False
def test_etf_premium_rejects_equity()
def test_stage2_on_rising_series(fake_market_uptrend)                 # synthetic linear uptrend, 300 days
def test_stage4_on_falling_series(fake_market_downtrend)
def test_stage_requires_250_days()
```

- [ ] **Step 2: Run tests to verify they fail** — Expected: FAIL, `ImportError`
- [ ] **Step 3: Implement the five modules.** Correlation uses `np.log(close/close.shift(1)).dropna()`; liquidity impact `σ_daily·√(0.01)·1e4` bps.
- [ ] **Step 4: Run tests to verify they pass** — Expected: 11 passed
- [ ] **Step 5: Commit** — `git commit -am "feat: estimates, correlation, liquidity, ETF premium, stage"`

---

### Task 8: Graham screen and buy gate

**Files:**
- Create: `src/fundkit/screen.py`
- Test: `tests/test_screen.py`

**Interfaces:**
- Consumes: `MarketData.screen`, `value_company`, `revision_summary`, `max_corr_with`, `liquidity.snapshot`, `stage.classify`, `ips.buy_allowed`, `Targets`.
- Produces:
  - `UNIVERSE_QUERY = {"region":"us","marketcap_gt":2e9,"pe_lt":20,"de_lt":1.0,"eps_growth_5y_gt":0}` and `universe(market) -> list[str]` calling `market.screen(UNIVERSE_QUERY)` (YFMarket maps it to `yf.EquityQuery`; FakeMarket returns the dict's list).
  - `quality_ok(ticker, market) -> tuple[bool,list[str]]` — FCF > 0 (latest `Free Cash Flow` row), debt/equity < 1 (`info["debtToEquity"]/100`).
  - `gate(ticker, dollars, holdings, values, targets, market, peers, held_active:list[str]) -> GateResult(passed:bool, upside, stage, max_corr, spread_pct, reasons:list[str], valuation:Valuation)` — passes only when `upside ≥ targets.min_upside`, `stage != 4`, `max_corr ≤ targets.max_corr`, `spread_pct < targets.max_spread`, `quality_ok`, and `buy_allowed`. Every failed check appends a reason string; all checks run (no short-circuit) so the memo can show them.
  - `refresh_watchlist(watch:list[WatchItem], market, targets, today) -> list[WatchItem]` — revalues items whose `last_valued` is None, older than 42 days, or whose price moved > `reval_move` since valuation; sets `fair_value/upside/last_valued`; status `passed` if upside ≥ min_upside else `candidate`.

- [ ] **Step 1: Write the failing tests**

```python
def test_gate_passes_clean_candidate(gate_env_pass):      # upside 0.30, stage 2, corr 0.4, spread 0.1%, quality ok, $150 into $3k
    g = gate(**gate_env_pass); assert g.passed and g.reasons == []
def test_gate_fails_on_stage4_and_lists_all_reasons(gate_env_stage4_lowupside):   # stage 4 and upside 0.10
    g = gate(...); assert not g.passed and len(g.reasons) == 2
def test_gate_fails_correlation_0_81(gate_env_corr_081)
def test_gate_fails_spread_0_5(gate_env_spread_exact_half_pct)    # 0.5% is not < 0.5%
def test_refresh_only_stale_or_moved(watch_two_items)              # one valued 10 days ago, price +3% → untouched; other +16% → revalued
```

- [ ] **Step 2: Run tests to verify they fail** — Expected: FAIL, `ImportError`
- [ ] **Step 3: Implement `screen.py`.** Peer list for `value_company`: `info["industry"]` screened via `market.screen({"industry": ..., "limit": 6})`, excluding the ticker; fall back to `[]` with flag `no_peers` (relative method skipped).
- [ ] **Step 4: Run tests to verify they pass** — Expected: 5 passed
- [ ] **Step 5: Commit** — `git commit -am "feat: Graham screen and buy gate"`

---

### Task 9: Performance snapshot and passive benchmark

**Files:**
- Create: `src/fundkit/performance.py`
- Test: `tests/test_performance.py`

**Interfaces:**
- Consumes: `Store`, `MarketData`, `Targets`, `Snapshot`.
- Produces:
  - `benchmark_value(start_value:float, start_date:date, today:date, market, targets, cash_yield:float=0.04) -> float` — 50% `targets.index_etf` / 30% `targets.bond_etf` / 20% cash accruing `cash_yield` daily, rebalanced to target every 42 days from `start_date`.
  - `active_vs_index(ledger:list[LedgerRow], holdings, values, market, targets) -> float|None` — for each `done` active BUY, compare current value of the shares bought with the value the same dollars would have in `index_etf` from the fill date; return the dollar difference summed; `None` when no active buys.
  - `turnover_qtd(ledger, today) -> float` — sum of `abs(fill_shares*fill_price)` for `done` rows since the quarter start divided by current total.
  - `snapshot(store, market, targets, today, inception:date, inception_value:float) -> Snapshot` and `store.append_performance(...)` is called by `run.py`, not here.

- [ ] **Step 1: Write the failing tests**

```python
def test_benchmark_flat_market_grows_only_by_cash_yield(fake_market_flat):   # prices constant → value = 3000*(1 + 0.2*0.04*days/365)
def test_benchmark_rebalances_every_42_days(fake_market_stock_doubles_day_43)
def test_active_vs_index_positive_when_pick_beats_etf(ledger_one_active_buy, fake_market_pick_up_20_etf_up_10)
def test_active_vs_index_none_without_active_buys()
def test_turnover_qtd_resets_at_quarter()
```

- [ ] **Step 2: Run tests to verify they fail** — Expected: FAIL, `ImportError`
- [ ] **Step 3: Implement `performance.py`.**
- [ ] **Step 4: Run tests to verify they pass** — Expected: 5 passed
- [ ] **Step 5: Commit** — `git commit -am "feat: performance snapshot and passive benchmark"`

---

### Task 10: Earnings calendar helpers

**Files:**
- Create: `src/fundkit/research/earnings.py`
- Test: `tests/test_earnings.py`

**Interfaces:**
- Produces: `upcoming_within(ticker, market, today, days=7) -> datetime|None` (next report timestamp with no Reported EPS), `reported_within(ticker, market, today, days=7) -> EarningsResult|None` where `EarningsResult(report_ts, eps_est, eps_actual, surprise_pct, reaction_pct:float|None)`; `reaction_pct` uses the recipe's rule: report at/after 16:00 ET → report-day close to next close; earlier → prior close to report-day close; `None` if the post session hasn't closed.

- [ ] **Step 1: Write the failing tests**

```python
def test_upcoming_found_within_7_days(fake_market_earnings_in_5_days)
def test_reported_after_close_reaction_uses_next_session(fake_market_reported_1630)   # closes 100 (report day), 105 next → +5%
def test_reported_before_open_reaction_uses_same_day(fake_market_reported_0800)       # prior 100, report-day 97 → −3%
def test_reaction_none_when_no_post_close()
```

- [ ] **Step 2: Run tests to verify they fail** — Expected: FAIL
- [ ] **Step 3: Implement `earnings.py`** reading `market.earnings_dates(ticker)` (columns `EPS Estimate`, `Reported EPS`, `Surprise(%)`, tz-aware index in US/Eastern).
- [ ] **Step 4: Run tests to verify they pass** — Expected: 4 passed
- [ ] **Step 5: Commit** — `git commit -am "feat: earnings calendar helpers"`

---

### Task 11: Assemble weekly actions

**Files:**
- Create: `src/fundkit/actions.py`
- Test: `tests/test_actions.py`

**Interfaces:**
- Consumes: everything in Tasks 5–10.
- Produces: `build_actions(store, market, targets, today, review:bool) -> ActionSet(compliance:ComplianceReport, proposals:list[Proposal], gated:list[GateResult], sells:list[Proposal], notes:list[str])`. Order: (1) trims from `compliance.trim_flags` → SELL proposals down to `name_cap`, preferring lots > 365 days old, with `est_tax` = gain × 0.15 for long-term lots else × 0.24 (flat assumptions, stated in the memo); (2) sell triggers from §5.5: price ≥ `fair_value`, or `revision_summary.direction == "down"` on two consecutive review runs (tracked via a `NOTE` ledger row `thesis_warning:<ticker>`) and `upside < 0`; (3) `rebalance_proposals` computed on post-trim values; (4) on review runs, up to 3 watchlist items with status `passed` go through `gate` with `dollars = min(targets.name_cap*total, 0.30*total − active_value)`; each pass becomes a SELL `index_etf` + BUY pair; (5) cap total proposals at 3 BUY/SELL pairs; everything else is a note. First-run rule: when Holdings contains only CASH, proposals are BUY `index_etf` for `0.5*total`, BUY `bond_etf` for `whole_share_target(0.3*total, price)` shares, and nothing else.

- [ ] **Step 1: Write the failing tests**

```python
def test_first_run_proposes_index_and_bond_only(store_with_cash_3000, fake_market_vti300_bnd75):
    a = build_actions(store_with_cash_3000, fake_market_vti300_bnd75, Targets(), today, review=True)
    assert [(p.action,p.ticker) for p in a.proposals] == [("BUY","VTI"),("BUY","BND")]
    assert a.proposals[0].dollars == 1500 and a.proposals[1].shares == 12      # 900/75 = 12
    assert a.gated == []

def test_trim_then_rebalance_uses_post_trim_weights(store_active_name_at_8pct_and_stocks_57pct):
    a = build_actions(...)
    assert a.sells[0].ticker == "ACME" and a.sells[0].reason.startswith("trim")
    assert all(p.ticker != "ACME" for p in a.proposals)         # rebalance doesn't double-sell ACME
    # stocks after trim+rebalance within band

def test_sell_prefers_long_term_lot_and_states_tax(store_two_lots_one_old)
def test_review_run_funds_pick_by_selling_index(store_on_target_with_passed_watch_item)   # proposals contain SELL VTI 150 and BUY ACME 150
def test_weekly_run_never_adds_new_names(store_on_target_with_passed_watch_item)          # review=False → no gated
def test_cap_three_pairs(store_with_five_passed_items)
```

- [ ] **Step 2: Run tests to verify they fail** — Expected: FAIL
- [ ] **Step 3: Implement `actions.py`.**
- [ ] **Step 4: Run tests to verify they pass** — Expected: 6 passed
- [ ] **Step 5: Commit** — `git commit -am "feat: assemble weekly action set"`

---

### Task 12: Memo renderer

**Files:**
- Create: `src/fundkit/memo.py`, `src/fundkit/templates/memo.md.j2` (add `jinja2` dep)
- Test: `tests/test_memo.py`

**Interfaces:**
- Consumes: `ActionSet`, `ReconcileResult`, `Snapshot`, `list[EtfPremium]`, active-sleeve monitor rows, earnings previews/recaps.
- Produces: `render(ctx: MemoContext) -> str` where `MemoContext(date, review:bool, reconcile, compliance, etf_rows, active_rows:list[ActiveRow], earnings_previews:list[str], earnings_recaps:list[EarningsResult], watch_summary:list[WatchItem], actions:ActionSet, snapshot:Snapshot, unpriced:list[str], ai_notes:dict[str,str]={})`. Sections in order: `# Oaklink weekly memo — {date}` ({`REVIEW RUN` badge}), `## 1. Last week` (reconcile), `## 2. IPS compliance` (always), `## 3. Index & bond sleeve` (one line if quiet), `## 4. Active sleeve` (one line if empty), `## 5. Watchlist`, `## 6. Actions` (always; `No action — <why>` when empty), `## 7. Scorecard` (total, bench, active-vs-index, turnover). Action lines exactly: `- [ ] {ACTION} — {ticker} — ${dollars:,.0f}{ (N sh)} — {reason} — IPS check: {PASS|FAIL}{ — est. tax ${x}}`. `ai_notes` values are inserted verbatim under their section (keys `"3".."7"`), so the AI can add one paragraph of interpretation per section. Hard cap: rendered memo ≤ 90 lines; renderer truncates watchlist and active rows first and appends `(… N more in Sheet)`.

- [ ] **Step 1: Write the failing tests**

```python
def test_memo_always_has_compliance_and_actions(minimal_ctx):
    out = render(minimal_ctx); assert "## 2. IPS compliance" in out and "## 6. Actions" in out
def test_no_action_line_when_empty(minimal_ctx):
    assert "No action —" in render(minimal_ctx)
def test_action_line_format(ctx_one_buy):   # BUY VTI 1500 → "- [ ] BUY — VTI — $1,500 — initial allocation — IPS check: PASS"
def test_memo_under_90_lines(ctx_with_30_watch_items):
    assert len(render(ctx_with_30_watch_items).splitlines()) <= 90 and "more in Sheet" in render(...)
def test_unpriced_tickers_named(ctx_with_unpriced):  assert "ZZZZ" in render(ctx_with_unpriced)
```

- [ ] **Step 2: Run tests to verify they fail** — Expected: FAIL
- [ ] **Step 3: Implement `memo.py` + template.**
- [ ] **Step 4: Run tests to verify they pass** — Expected: 5 passed
- [ ] **Step 5: Commit** — `git commit -am "feat: one-page memo renderer"`

---

### Task 13: Weekly run orchestrator

**Files:**
- Create: `src/fundkit/run.py`
- Test: `tests/test_run.py`

**Interfaces:**
- Consumes: Tasks 3–12.
- Produces: `weekly_run(store, market, today, dry_run:bool=False, force_review:bool|None=None, ai_notes:dict[str,str]|None=None) -> RunResult(memo_md:str, actions:ActionSet, snapshot:Snapshot, review:bool, written:bool)`. Steps in order: reconcile → value holdings (collect unpriced) → `review = force_review if not None else (run_number % targets.review_every == 0)` where `run_number = len(store.load_performance()) + 1` → compliance → ETF premium for held ETFs → active rows (price Δ, revisions, earnings preview/recap flags, revalue when `reported_within` or |Δ| > `reval_move` since `WatchItem.last_valued`) → `refresh_watchlist` only on review runs → `build_actions` → snapshot → render. When `dry_run` is False: append ledger rows (`pending`) for each proposal, append snapshot, save watchlist; `written=True`. Inception date/value for the benchmark = first performance row (or today/total when none).

- [ ] **Step 1: Write the failing tests**

```python
def test_sixth_run_is_review(store_with_5_snapshots, fake_market_basic):
    assert weekly_run(store_with_5_snapshots, fake_market_basic, today, dry_run=True).review is True
def test_fifth_run_is_not_review(store_with_4_snapshots, ...)
def test_dry_run_writes_nothing(store_with_cash_3000, ...):
    r = weekly_run(..., dry_run=True); assert not r.written and store.load_ledger() == [] and store.load_performance() == []
def test_live_run_appends_pending_ledger_rows_and_snapshot(store_with_cash_3000, ...):
    r = weekly_run(..., dry_run=False); assert all(x.response == "pending" for x in store.load_ledger()) and len(store.load_performance()) == 1
def test_revalue_only_on_earnings_or_15pct_move(store_active_acme_valued_last_week, fake_market_acme_up_10)   # no revalue; with up_16 → revalue
```

- [ ] **Step 2: Run tests to verify they fail** — Expected: FAIL
- [ ] **Step 3: Implement `run.py`.**
- [ ] **Step 4: Run tests to verify they pass** — Expected: 5 passed
- [ ] **Step 5: Commit** — `git commit -am "feat: weekly run orchestrator"`

---

### Task 14: CLI

**Files:**
- Create: `src/fundkit/cli.py`; add `[project.scripts] fundkit = "fundkit.cli:app"` to `pyproject.toml`
- Test: `tests/test_cli.py` (typer `CliRunner`)

**Interfaces:**
- Commands: `fundkit init [--data DIR]` (Store.init), `fundkit reconcile [--today YYYY-MM-DD]`, `fundkit run [--today ...] [--dry-run] [--review/--no-review] [--notes notes.json] [--out memo.md]` (prints memo to stdout and writes `--out`), `fundkit screen [--limit N]` (prints universe + quality results as a table), `fundkit value TICKER [--peers A,B,C]` (prints a `Valuation` summary). `--data` defaults to `./data`; `FUNDKIT_DATA` env var overrides. All commands exit 0 on success, 1 on `MarketUnavailable` for a required ticker (index/bond ETF), and never raise tracebacks to the user.

- [ ] **Step 1: Write the failing tests**

```python
def test_init_creates_tables(tmp_path):  result = runner.invoke(app, ["init","--data",str(tmp_path)]); assert result.exit_code == 0 and (tmp_path/"targets.csv").exists()
def test_run_dry_run_prints_memo(tmp_store, monkeypatch_fake_market):  assert "## 6. Actions" in runner.invoke(app, ["run","--dry-run","--data",...]).stdout
def test_run_exit_1_when_index_etf_unpriceable(...)
def test_value_prints_blended(...)
```

- [ ] **Step 2: Run tests to verify they fail** — Expected: FAIL
- [ ] **Step 3: Implement `cli.py`** with a `FUNDKIT_MARKET=fake` hook that loads `tests/conftest.FakeMarket` from a JSON path, so the CLI is testable and the scheduled run can be rehearsed offline.
- [ ] **Step 4: Run tests to verify they pass** — Expected: 4 passed
- [ ] **Step 5: Commit** — `git commit -am "feat: fundkit CLI"`

---

### Task 15: Operating docs — weekly run prompt and quarterly runbook

**Files:**
- Create: `docs/weekly-run-prompt.md`, `docs/quarterly-deep-dive.md`, update `README.md`
- Test: none (docs); verification is a manual dry run.

**Interfaces:**
- `docs/weekly-run-prompt.md` is the exact text of the scheduled task. It must instruct the AI session to, in order: (1) pull the four tabs of the **Oaklink Fund Ledger** Google Sheet via the Drive connector into `data/*.csv` (Holdings, Targets, Watchlist, Decision Ledger; Performance is append-only from the package); (2) `pip install` the toolkit from the pinned release zip in the AI Fund project; (3) `fundkit run --today <date> --out memo.md`; (4) read `memo.md` and the `ActionSet` JSON, then write one short paragraph per section 3–7 into `notes.json` — judgment only, never new numbers; (5) re-render with `fundkit run --notes notes.json --dry-run --out memo.md` (same date, so no double write); (6) write `memo.md` to the AI Fund project as `memos/YYYY-MM-DD.md`; (7) push `ledger.csv`, `watchlist.csv`, `holdings.csv`, `performance.csv` back to the Sheet; (8) if any `unpriced` ticker or `incomplete` ledger row exists, say so in the first line of the memo. The prompt states the two things the AI may never do: invent a number not in the package output, and recommend an instrument that failed `check_instrument`.
- `docs/quarterly-deep-dive.md` is the PC runbook: `fundkit run --review --dry-run` for the scorecard; per holding `fundkit value`, then the `tradingview-mcp` TA readout and optional `finance-sentiment`/`twitter-reader` color; `stock-correlation` matrix of active + index ETF; capital-gains distribution check (`info["lastCapGain"]` / fund site) for held ETFs before December; the IPS review questions (sleeve targets still right? trim threshold biting? watchlist too thin?) written as questions, never changes; `gsd-capture --seed` for ideas.

- [ ] **Step 1: Write both docs and the README quick-start** (install, `fundkit init`, fill `targets.csv`, first `fundkit run --review --dry-run`).
- [ ] **Step 2: Rehearse the weekly prompt offline** — `FUNDKIT_MARKET=fake fundkit run --dry-run --review --data data/examples` prints a memo whose Actions section has exactly two BUY lines (index ETF, bond ETF).
- [ ] **Step 3: Commit** — `git commit -am "docs: weekly run prompt and quarterly runbook"`

---

### Task 16: Release and scheduled task

**Files:**
- Create: `scripts/release.py` (builds `dist/fundkit-<ver>.zip` with `python -m build`), tag `v0.1.0`
- Modify: AI Fund project — upload the zip as a project file; create the Google Sheet **Oaklink Fund Ledger** with the five tabs from `data/examples`.

**Steps:**
- [ ] **Step 1: Build and tag** — `python scripts/release.py && git tag v0.1.0`; Expected: `dist/fundkit-0.1.0.zip` exists.
- [ ] **Step 2: Create the Sheet** from `data/examples/*.csv` (via the google-workspace skill) and record its file id in `docs/weekly-run-prompt.md`.
- [ ] **Step 3: Upload the zip to the AI Fund project** and record the path in the prompt.
- [ ] **Step 4: Live dry run from the cloud session** — follow `docs/weekly-run-prompt.md` by hand with `--dry-run`; Expected: a memo appears in chat, nothing written to the Sheet.
- [ ] **Step 5: Create the scheduled task** — `CRON_TZ=America/Los_Angeles 37 7 * * 6`, prompt = `docs/weekly-run-prompt.md`, push notifications on. Confirm the first real run the following Saturday produces `memos/<date>.md` with the two initial BUY lines.
- [ ] **Step 6: Commit** — `git commit -am "chore: v0.1.0 release and scheduled run config"`

---

## Self-review notes

- Spec coverage: §5.2 tables → Tasks 1–2; §5.3 steps 1–8 → Tasks 3, 4, 5, 7(etf), 7+10(active), 8, 11, 9/12/13; review-run cadence → Task 13; §5.4 → Task 15 runbook; §5.5 philosophy → Tasks 7(stage), 8, 11(sell rules); §5.6 → Task 9; §5.7 first portfolio → Task 11 first-run rule; §5.8 → the plan itself; Sheet auth left to the AI session per §6 → Task 15/16.
- Type consistency checked: `Proposal`, `ComplianceReport`, `GateResult`, `ActionSet`, `Snapshot`, `Valuation` names match across Tasks 5–13.
- Review Focus items 1–5 are pinned to Tasks 5+12, 11, 6, 3, 11 respectively.
- Not in this plan (deliberately): any broker connectivity, options/leverage logic, contributions handling (spec says none; add a `flows` column to `Snapshot` if that changes), international ETF.
