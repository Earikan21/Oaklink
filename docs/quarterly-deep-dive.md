# Quarterly deep dive (runbook, on the PC)

Run by Emmet from his own machine, where opencli and TradingView are available. The cloud scheduled
task cannot do this. Output: a 2-3 page report saved as `reports/YYYY-Qn.md` in the AI Fund project.

Prerequisite: `data/` holds current CSVs pulled from the "Oaklink Fund Ledger" Sheet, and `fundkit` is
installed (see README).

1. **Fresh scorecard.**
   `fundkit run --data ./data --review --dry-run --out memo.md`
   `--dry-run` writes nothing (reconcile still applies Emmet's filled ledger rows). Section 7 (Scorecard)
   is the thesis test: total vs the passive 50/30/20 benchmark, and active sleeve vs the index ETF it was
   funded from. The first meaningful read is at 12 months.

2. **Re-underwrite each holding.** For every active holding, `fundkit value TICKER`
   (optionally `--peers A,B,C` when the default peer map is thin). Record price, blended fair value,
   DCF, relative value, upside, confidence and flags.

3. **Technicals and color.** For each active holding, take the `tradingview-mcp` TA readout.
   Optionally add `finance-sentiment` and `twitter-reader` color; skip if not set up. These are
   finance-skills in Emmet's Claude setup and are PC-only. They inform judgment; they never override
   the valuation.

4. **Diversification check.** Run `stock-correlation` on a matrix of the active names plus the index
   ETF. Note any pair above the `max_corr` target (0.8).

5. **Capital-gains distributions.** Before December, check each held ETF for a pending capital-gains
   distribution (`info["lastCapGain"]` or the fund's site). In a taxable account, note any fund to
   avoid buying just before its distribution.

6. **IPS review questions.** Write these in the report as questions for Emmet, with the evidence:
   - Are 50/30/20 still the right sleeve targets?
   - Is the 7.5% trim threshold biting too often, or never?
   - Is the watchlist too thin to find a qualifying name?
   - Is the thesis-warning window right?
   - Is the 30% active ceiling still appropriate given the scorecard?

   **Nothing in `targets.csv` or the IPS is changed automatically.** Emmet decides, edits the IPS, and then
   edits targets.

7. **Seed ideas.** Capture ideas from the quarter with `gsd-capture --seed` for the backlog.

8. **Save the report** to the AI Fund project as `reports/YYYY-Qn.md` (for example `reports/2026-Q4.md`).
