# fundkit

`fundkit` encodes a personal investment policy statement (IPS) as tested Python: allocation bands, position
caps, valuation-based buy/sell rules and a weekly memo. It advises only; Emmet executes all trades himself
and records fills in a Google Sheet.

## Install

From a release zip (`python3 scripts/release.py` builds `dist/fundkit-<version>.zip`; it holds the wheel,
the sdist, the docs and `INSTALL.txt`):

```bash
unzip -o fundkit-<version>.zip -d toolkit
pip install toolkit/fundkit-*.whl --break-system-packages
```

From a checkout:

```bash
pip install -e .          # development: pip install -e ".[dev]"
```

## Quick start

```bash
fundkit init --data ./data --cash 3000     # create the five tables, seed $3,000 cash
fundkit run --data ./data --dry-run --review   # first run: writes nothing, prints the memo
```

Read the memo: 1 Last week, 2 IPS compliance, 3 Index & bond sleeve, 4 Active sleeve, 5 Watchlist,
6 Actions, 7 Scorecard. Drop `--dry-run` to record the run. Other commands: `fundkit reconcile [--today]`,
`fundkit screen [--limit N]`, `fundkit value TICKER [--peers A,B,C]`. `--data` defaults to `$FUNDKIT_DATA`,
then `./data`. Exit codes: 0 ok; 1 `error: ...` on stderr (market or value error); 2 data dir missing
(run `fundkit init`).

## Data tables

CSV files in the data dir; each maps to a tab of the "Oaklink Fund Ledger" Sheet. Headers must match exactly
(same columns, same order), dates are ISO `YYYY-MM-DD`, and numbers are plain unformatted numbers (no `$`,
no thousands separators, no `%`: 5% is `0.05`). A wrong header exits 1 with `error: schema mismatch in ...`;
a badly formatted value exits 1 with a pydantic `validation error`. Fix the Sheet rather than retrying.

- `holdings.csv`: `ticker`, `sleeve` (indexed_equity / active / bonds / cash), `shares`, `cost_basis` (per share), `lot_date`. Source of truth.
- `targets.csv`: `key,value` IPS parameters (below).
- `watchlist.csv`: `ticker`, `thesis`, `last_valued`, `fair_value`, `upside`, `status` (candidate / passed / rejected / held).
- `ledger.csv` (Decision Ledger): `id`, `date`, `action` (BUY / SELL / REBALANCE / NOTE), `ticker`, `amount`, `rationale`, `ips_check`, `response` (pending / done / applied / skipped / modified), `fill_shares`, `fill_price`, `est_tax`. Emmet sets `done` (or `modified`, when he traded a different amount) plus fill data, or `skipped`; `modified` is reconciled exactly like `done`.
- `performance.csv`: `date,total,indexed_equity,active,bonds,cash,bench_total,active_vs_index,turnover_qtd,unpriced`. Appended by the package only.

## IPS rules encoded (`targets.csv` defaults)

- Sleeves: stocks 50% (`stocks`), bonds 30% (`bonds`), cash 20% (`cash`); flag a sleeve more than 5 pts off (`band` 0.05).
- Active sleeve is a ceiling of 30% (`active_ceiling`), not a target; funded by selling the index ETF.
- Per-name cap 5% at purchase (`name_cap`); trim flag at 7.5% (`trim_threshold`), or above 6% at a review (`review_trim`).
- Fund expense ratio must be under 0.9% (`fee_cap`).
- Buys need at least 25% upside (`min_upside`), correlation at most 0.8 (`max_corr`), spread at most 0.5% (`max_spread`).
- Revalue a held name after earnings or a 15% price move (`reval_move`).
- Review run every 6th run (`review_every`); force with `--review`. Index ETF `VTI`, bond ETF `BND`.

## Scheduled run

Every Saturday an AI session follows [docs/weekly-run-prompt.md](docs/weekly-run-prompt.md): pull the Sheet,
run `fundkit`, add judgment notes, save the memo, push the data back. Quarterly, on the PC, see
[docs/quarterly-deep-dive.md](docs/quarterly-deep-dive.md).

## Offline mode

```bash
FUNDKIT_MARKET=fake fundkit init --data ./data --cash 3000
FUNDKIT_MARKET=fake fundkit run --data ./data --dry-run --review
```

`FUNDKIT_MARKET=fake` uses a packaged fixture; set `FUNDKIT_FAKE_MARKET_JSON` to supply your own.

## Tests

```bash
pytest
```

Design: [docs/superpowers/specs/2026-11-19-oaklink-fund-engine-design.md](docs/superpowers/specs/2026-11-19-oaklink-fund-engine-design.md)
