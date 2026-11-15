# ibov-rebuild

Rebuilds Brazil's Ibovespa index from B3's own public end-of-day files, checks the result against B3's official close, and writes a report you can read in any browser.

Raw files are downloaded once and kept unchanged. Everything is then loaded into a local SQLite database, and every number in the report is computed from those files.

## What it does

- **Downloads** three kinds of B3 file:
  - the index's theoretical portfolio: the 76 share lines, their theoretical quantities, and the divisor (*redutor*);
  - the daily quotes file (COTAHIST);
  - the index's official daily closes.
- **Stores** the raw files unchanged in `data/raw/` and loads them into `data/ibov.db`.
- **Rebuilds** the index level and every weight, and reconciles them with B3's figures.
- **Detects payouts.** From B3's own weights and the change in the divisor, it finds the shares that went ex-dividend and the price adjustments B3 absorbed.
- **Splits the day's move** into each stock's contribution, in index points.
- **Checks its own work.** Every report opens with the acceptance checks, each marked Pass, Fail or Not run, and names the raw file behind every number.
- **Reports** in a single self-contained HTML file (charts included) and a CSV of every line. [`sample_report_2026-11-11.html`](sample_report_2026-11-11.html) is a real one; download it and open it in a browser.

## Result, 11 November 2026

| Check | Result |
|---|---|
| Rebuilt level vs official close | 172,060.43 vs 172,060.43, a difference of +0.0034 points (0.000002%) |
| 10 Nov prices on the 11 Nov portfolio | 195.68 points (0.114%) above the official close, explained by three payouts that went ex on 11 Nov (Sabesp, Gerdau, Metalúrgica Gerdau); residual −0.10 points |
| Contributions vs official change | +723.44 vs +723.44 points |
| B3 totals | quantities add up to the published 93,803,750,400; weights to 100.000% |

## Usage

Python 3.10 or later, standard library only: nothing to install.

```
git clone https://github.com/T444CEM/ibov-rebuild.git
cd ibov-rebuild
python -m ibov update          # download today's portfolio, the official closes and the latest quotes
python -m ibov report --open   # report for the latest session, opened in your browser
```

Other commands:

```
python -m ibov report 2026-11-11                 # report for one session
python -m ibov status                            # what is stored
python -m ibov backfill 2026-10-01 2026-11-10    # quote files for a date range
python -m ibov ingest --full                     # rebuild the database from the raw files
```

Reports are written to `data/reports/`.

**Run `update` every trading day, in the evening after B3 publishes.** B3 only serves the *current* portfolio. Without a saved copy for a given day, that day's divisor and quantities are lost: its level check becomes approximate and its payouts cannot be inferred. [`run_daily.cmd`](run_daily.cmd) runs `update` and `report`. On Windows, schedule it once with:

```
schtasks /create /tn "ibov update" /sc weekly /d MON,TUE,WED,THU,FRI /st 22:30 /tr "C:\path\to\ibov-rebuild\run_daily.cmd"
```

Everything under `data/` stays on your machine; git ignores it. Back up `data/raw/portfolio/`, since those files cannot be downloaded again. The rest is rebuilt by `update`, `backfill` and `ingest --full`.

## How the index is built

```
line value   = closing price × theoretical quantity
index level  = Σ line values ÷ redutor
weight       = line value ÷ Σ line values
contribution = quantity × (close today − adjusted close yesterday) ÷ redutor
```

The theoretical quantities stay fixed between B3's four-monthly rebalances.

The redutor keeps the series continuous. When a share goes ex-dividend, its price falls by the payout, but holders lose nothing. B3 therefore lowers the redutor so the index does not fall for that reason.

B3 computes each session's published weights from the *adjusted* previous closes, which gives a way to recover the adjustments:

- A line's implied adjusted close is its weight × yesterday's official close × today's redutor ÷ its quantity.
- A line whose traded close sits clearly above that implied value went ex that day.
- The payouts must add up exactly to yesterday's traded portfolio value minus yesterday's official close × today's redutor.
- This only works with the session's own portfolio, because B3's weights and redutor describe the move into that session. With another day's portfolio, the report says so and infers no payouts.

## Data

| Table | One row is | Source |
|---|---|---|
| `portfolio_header` | one session's divisor and total quantity | B3 index API |
| `portfolio_line` | one share line in one session's portfolio | B3 index API |
| `quote` | one instrument on one trading day (cash market) | B3 COTAHIST |
| `index_close` | one official close | B3 index statistics |
| `raw_file` | one raw file loaded into the database | — |

COTAHIST is fixed-width text, 245 characters a line, with prices in integer centavos. The field positions follow B3's published layout document and are listed in [`ibov/b3.py`](ibov/b3.py).

## Tests

```
python -m unittest discover tests
```

The tests build a three-stock index with a known payout. They check that:

- the parsers read B3's formats;
- the level reconciles exactly on same-day data;
- the payout is found from the weights and explains the whole cross-day gap;
- no payouts are inferred from another day's portfolio;
- the contributions add up to the official change;
- the end-to-end report passes every check and names its raw files.

## Limits

- End of day only: no intraday data.
- Payouts are implied from B3's weights and divisor, not read from company announcements.
- On a rebalance day the basket changes. The payout check then reports a large residual instead of attributing it.
- The portfolio and index-statistics endpoints are the ones B3's own website uses. They are public but undocumented, so they could change.

## Next

1. A history of portfolios and divisors, built from the daily runs.
2. A sector breakdown, from B3's sector classification file.
3. Earnings and book values from company filings (CVM), to compute the index P/E and P/B from its constituents.
4. More indices and markets, following the same pattern.

## Acknowledgements

The program uses only Python's standard library, so it has no third-party code to credit. All data comes from [B3](https://www.b3.com.br): its index portfolio and statistics pages, and its COTAHIST historical quotes files, read using the field layout B3 publishes for them.

## License

The code is released under the [MIT License](LICENSE). The market data it downloads belongs to B3, is subject to B3's terms of use, and is not included in this repository.
