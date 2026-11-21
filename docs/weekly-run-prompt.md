# Weekly run prompt (the scheduled task, verbatim)

> This file is the literal text of the Saturday scheduled task. Placeholders in angle brackets
> (`<SHEET_FILE_ID>`, `<TOOLKIT_ZIP_PATH>`) are filled in by Task 16 when the task is created.

---

You are running the weekly Oaklink fund cycle. All arithmetic is done by the `fundkit` package; you only
read its output and add judgment. Follow these steps in order.

Date: substitute the current date in America/Los_Angeles as YYYY-MM-DD everywhere `<today>` appears, and use
the identical date in steps 3, 5 and 6.

Sheet: Google Sheet "Oaklink Fund Ledger", file id `<SHEET_FILE_ID>`.
Toolkit: release zip `<TOOLKIT_ZIP_PATH>` in the AI Fund project.

1. **Pull the data.** Using the Drive connector, read these tabs of the Sheet and write each to a CSV in
   `data/` (create the directory). The header row must match the package's `examples/*.csv` exactly:

   | Tab | File | Header |
   |---|---|---|
   | Holdings | `data/holdings.csv` | `ticker,sleeve,shares,cost_basis,lot_date` |
   | Targets | `data/targets.csv` | `key,value` |
   | Watchlist | `data/watchlist.csv` | `ticker,thesis,last_valued,fair_value,upside,status` |
   | Decision Ledger | `data/ledger.csv` | `id,date,action,ticker,amount,rationale,ips_check,response,fill_shares,fill_price,est_tax` |
   | Performance | `data/performance.csv` | `date,total,indexed_equity,active,bonds,cash,bench_total,active_vs_index,turnover_qtd,unpriced` |

   Performance is pulled too, but only the package ever appends to it. Do not edit any CSV by hand.

2. **Install the toolkit.** `pip install <TOOLKIT_ZIP_PATH> --break-system-packages`

3. **Run the cycle (live).**
   `fundkit run --data ./data --today <today> --out memo.md --json run.json`
   The run first reconciles the Decision Ledger: rows Emmet marked `done` (with `fill_shares` and
   `fill_price`) become `applied` and update Holdings; `skipped` rows are closed. Chat replies never reach
   a scheduled run, so the Sheet is the only channel. This live run is the one that writes to `data/`.

4. **Write the notes.** Read `run.json` (keys: `memo_md`, `review`, `written`, `actions`, `snapshot`) and
   `memo.md`. Write `notes.json`: a JSON object whose keys are the section numbers as strings and whose
   values are one short paragraph each, for sections 3 to 7:
   `"3"` Index & bond sleeve, `"4"` Active sleeve, `"5"` Watchlist, `"6"` Actions, `"7"` Scorecard.
   (`"1"` and `"2"` are accepted but not needed.) Judgment only: what to watch, why a flag matters,
   what Emmet should look at. Use no number that does not appear in `run.json` or `memo.md`, and never
   mention as a candidate any instrument that failed `check_instrument` (see `actions.gated`).

5. **Re-render with notes.**
   `fundkit run --data ./data --today <today> --dry-run --notes notes.json --out memo.md`
   Same date, so this is idempotent: the live write already happened in step 3 and `--dry-run` writes
   nothing further. Never pass a different date here.

6. **Save the memo.** Save `memo.md` to the AI Fund project as `memos/<today>.md` (YYYY-MM-DD).

7. **Push the data back.** Replace the contents of these Sheet tabs with the current local CSVs:
   `data/ledger.csv`, `data/watchlist.csv`, `data/holdings.csv`, `data/performance.csv`.
   Do not push `targets.csv` (the Sheet is its source).
   Before replacing each tab, re-read it and compare its row count with what you pulled in step 1. If the
   Sheet was edited since step 1 (counts differ), do not overwrite it: report the difference instead.

8. **Summarize in chat.** If `memo.md` contains an `**Attention:**` line, repeat it as the first line of
   your chat summary, then give a short summary of the Actions section.

## Never

- Invent a number. Every figure you write must appear in `run.json` or `memo.md`.
- Recommend an instrument the package flagged (failed `check_instrument` / gated).
- Place, simulate, or "pretend-fill" a trade. Emmet executes everything himself and records fills in the Sheet.
- Edit `targets.csv`. IPS changes are Emmet's: he edits the IPS, then the targets.
- Skip step 7 after a successful step 3. An un-pushed run means next week reconciles against stale state.
  (If step 3 failed you stop and do not push; see below.)

## If something fails

- If step 3 fails (exit 1 or 2), stop and do NOT push to the Sheet. The Sheet is unchanged and reconcile is
  idempotent next week. Step 7 is mandatory only after a successful step 3.
- Exit code 2 (`error: data dir ... not found; run fundkit init first`): `fundkit init` was never run on
  this data directory. Stop and report; do not improvise.
- Exit code 1 (`error: ...` on stderr; market data or value error): market data is unavailable. Report it.
  Retry at most once.
- A malformed `notes.json` gives exit 1 in step 5: fix the JSON; do not retry the run.
- The memo rendered but contains notes such as "benchmark unavailable": deliver it anyway (steps 5 to 8)
  and say so in the chat summary.
