# Weekly run prompt (the scheduled task, verbatim)

> This file is the literal text of the Saturday scheduled task. Placeholders in angle brackets
> were filled in on 2026-11-24 when the Sheet and the scheduled task were created.

---

You are running the weekly Oaklink fund cycle. All arithmetic is done by the `fundkit` package; you only
read its output and add judgment. Follow these steps in order.

Date: substitute the current date in America/Los_Angeles as YYYY-MM-DD everywhere `<today>` appears, and use
the identical date in steps 3, 5 and 6.

Sheet: Google Sheet "Oaklink Fund Ledger", file id `16Twd-A7b9ELl1I-uMDoykIqOri95jafQb0jpLdVI9z8`
(Drive folder "Oaklink Fund", id `1ehq1sI-KGKZAbziuYIPCfZkcKCqifVDT`).
Toolkit: the AI Fund project doc `claude/fundkit-wheel-b64.txt` holds the release wheel, base64-encoded
(a copy of the release zip also sits in the Drive folder for humans).

0. **Check what you have.** You need (a) the Google Drive connector, (b) the Google Sheets connector
   (tools that read and write cell values, e.g. `get_values` / `update_values`), and (c) `pip` access.
   If (b) is missing, you may still do steps 1 to 4 and 6 to 8 in *preview mode*: skip step 5 (live
   run) and step 7, and say in the chat summary that the Google Sheets connector must be turned on for
   the run to record anything. Nothing is lost: a preview run writes nothing durable.

1. **Pull the data.** Export the Sheet as a workbook with the Drive connector
   (`download_file_content`, `exportMimeType` =
   `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`; the tool saves the bytes to a
   local path), then convert it: `fundkit sheet-import <that path> --data ./data`. This writes the five
   CSVs below with normalised values and fails with `error: schema mismatch …` if a tab or header column
   moved. Do not hand-write the CSVs. The tabs and headers, for reference:

   | Tab | File | Header |
   |---|---|---|
   | Holdings | `data/holdings.csv` | `ticker,sleeve,shares,cost_basis,lot_date` |
   | Targets | `data/targets.csv` | `key,value` |
   | Watchlist | `data/watchlist.csv` | `ticker,thesis,last_valued,fair_value,upside,status` |
   | Decision Ledger | `data/ledger.csv` | `id,date,action,ticker,amount,rationale,ips_check,response,fill_shares,fill_price,est_tax` |
   | Performance | `data/performance.csv` | `date,total,indexed_equity,active,bonds,cash,bench_total,active_vs_index,turnover_qtd,unpriced` |

   Performance is pulled too, but only the package ever appends to it. Do not edit any CSV by hand.

   Emmet types values into the Sheet as ISO dates (`YYYY-MM-DD`) and plain numbers (`1500.5`, `0.05`);
   the import converts real date/number cells, but text like `$1,500.50` or `5%` will fail validation.

2. **Install the toolkit.** Read the project doc `claude/fundkit-wheel-b64.txt` (the tool saves large
   docs to a local file), then:
   `base64 -d <that file> > fundkit-0.1.0-py3-none-any.whl && pip install fundkit-0.1.0-py3-none-any.whl --break-system-packages`
   Check: `fundkit --help` lists `run`, `sheet-import`, `sheet-export`.

3. **Preview the cycle (dry run).**
   `fundkit run --data ./data --today <today> --dry-run --json run.json --out memo.md`

   **If this fails with `error: cannot price …` or a market-data error**, Yahoo Finance is not reachable
   from this environment (its network allowlist). Then, and only then, use *web-quoted mode*:
   - It is allowed only while Holdings contain nothing but CASH, `index_etf` and `bond_etf` (check
     `data/holdings.csv`). If any other ticker is held or the Watchlist is non-empty, stop and report
     "market data unavailable; active names cannot be valued" — do not guess.
   - Web-search the current price, bid, ask, NAV, expense ratio and average volume for the index and bond
     ETFs (prefer the issuer's page or Yahoo Finance; note the quote time). Write `market.json` in the
     package's offline format (one object per ticker with `quote` {ticker, price, bid, ask,
     expense_ratio as a fraction e.g. 0.0003, nav, quote_type "ETF", avg_volume}, `info` {longName,
     quoteType, netExpenseRatio as percent e.g. 0.03}, `history` {start: <yesterday>, closes: [price, price]},
     plus `"__screen__": []`).
   - Run every `fundkit run` in this cycle with `FUNDKIT_MARKET=fake FUNDKIT_FAKE_MARKET_JSON=market.json`
     in the environment, pass `--no-review` (screening is impossible without data), and start your notes
     for section 3 with "Prices web-quoted at <time> from <source>; Yahoo Finance unreachable."
   - The scorecard's benchmark is then flat for the week; say so in section 7's note.
   The run first reconciles the Decision Ledger: rows Emmet marked `done` (with `fill_shares` and
   `fill_price`) become `applied` and update Holdings; `skipped` rows are closed. Chat replies never reach
   a scheduled run, so the Sheet is the only channel. Reconcile is the only thing a dry run writes, and it
   is idempotent; nothing else is recorded yet.

4. **Write the notes.** Read `run.json` (keys: `memo_md`, `review`, `written`, `actions`, `snapshot`) and
   `memo.md`. Write `notes.json`: a JSON object whose keys are the section numbers as strings and whose
   values are one short paragraph each, for sections 3 to 7:
   `"3"` Index & bond sleeve, `"4"` Active sleeve, `"5"` Watchlist, `"6"` Actions, `"7"` Scorecard.
   (`"1"` and `"2"` are accepted but not needed.) Judgment only: what to watch, why a flag matters,
   what Emmet should look at. Use no number that does not appear in `run.json` or `memo.md`, and never
   mention as a candidate any instrument that failed `check_instrument` (see `actions.gated`, and the
   fund flags in section 2).

5. **Run the cycle (live).**
   `fundkit run --data ./data --today <today> --notes notes.json --out memo.md --json run.json`
   This live run is the one that writes to `data/` (ledger rows, snapshot, watchlist), and its `memo.md`
   is the memo that is published. Same date as step 3, so it computes the same actions; a live run is
   idempotent per day (re-running it adds no duplicate rows). Never pass a different date here.

6. **Save the memo.** Save `memo.md` to the AI Fund project as `memos/<today>.md` (YYYY-MM-DD). In
   preview mode (step 0) or web-quoted mode, put that fact in the memo's first line.

7. **Push the data back.** With the Google Sheets connector, replace the contents of the tabs
   Decision Ledger, Watchlist, Holdings and Performance with the current local CSVs
   (`data/ledger.csv`, `data/watchlist.csv`, `data/holdings.csv`, `data/performance.csv`): clear each
   tab's old rows, write the header plus rows from the CSV as plain values (dates as text `YYYY-MM-DD`).
   `fundkit sheet-export out.xlsx --data ./data` gives the same four tabs as a workbook if a bulk
   route is easier. Never push `targets.csv` (the Sheet is its source).
   Before replacing each tab, re-read it and compare its row count with what you pulled in step 1. If the
   Sheet was edited since step 1 (counts differ), do not overwrite it: report the difference instead.

8. **Summarize in chat.** If `memo.md` contains an `**Attention:**` line, repeat it as the first line of
   your chat summary, then give a short summary of the Actions section.

## Never

- Invent a number. Every figure you write must appear in `run.json` or `memo.md`.
- Recommend an instrument the package flagged (failed `check_instrument` / gated).
- Place, simulate, or "pretend-fill" a trade. Emmet executes everything himself and records fills in the Sheet.
- Edit `targets.csv`. IPS changes are Emmet's: he edits the IPS, then the targets.
- Skip step 7 after a successful step 5. An un-pushed run means next week reconciles against stale state.
  (If step 3 or step 5 failed you stop and do not push; see below.)

## If something fails

- If step 3 or step 5 fails (exit 1 or 2), stop and do NOT push to the Sheet. The Sheet is unchanged and
  reconcile is idempotent next week. Step 7 is mandatory only after a successful step 5.
- Exit code 2 (`error: data dir ... not found; run fundkit init first`): `fundkit init` was never run on
  this data directory. Stop and report; do not improvise.
- Exit code 1 whose message starts with `error: schema mismatch` or contains `validation`
  (e.g. `error: 1 validation error for LedgerRow`): a Sheet formatting problem (a moved/added column, a
  formatted date or number, a percent typed as `5`). Do not retry blindly: report the exact message so Emmet
  can fix the Sheet (or fix the CSV export from step 1 if the formatting came from your pull), then rerun.
- Any other exit code 1 (`error: ...` on stderr; market data or value error): market data is unavailable.
  Report it. Retry at most once.
- A malformed `notes.json` gives exit 1 in step 5 before anything is written: fix the JSON and run step 5
  again with the same date.
- The memo rendered but contains notes such as "benchmark unavailable": deliver it anyway (steps 4 to 8)
  and say so in the chat summary.
