# fries-backtest

Backtest of [FriesTrader](https://github.com/YizhiSong/FriesTrader)'s mechanical
rules on a custom stock pool, run on GitHub Actions (which can download prices).

## Use

1. Put symbols in `pool.txt`, one per line, Yahoo format
   (`AAPL`, `BRK-B`, `0700.HK`, `600519.SS`, `000001.SZ`).
2. Push. The `backtest` workflow downloads prices, runs, and commits
   `results/summary.json`, `results/trades.csv`, `results/equity.csv`.
   Or run it by hand from the Actions tab with a different start date,
   conviction tier, or benchmark.
3. To test a rule variant, edit `rules.json` and push.

## What is and isn't tested

Simulated: Phase A universe filters (price, volume, 200-day trend) and signal
gate at each close; Phase B at the next open: vol-scaled stop-loss (trailing
after a take-profit fires), tiered take-profit, conviction trim, loss-limit
halt, entry gate (price gap, 20-day extension, wash-sale, re-entry lock),
ranking by % below 52-week high, sizing with max positions and cash buffer.

Not simulated: the LLM news thesis (every signal is a `long` at one fixed
conviction), market-cap band, earnings blackout. A pool chosen today has
survivorship bias. Treat results as a test of the rules, not of FriesTrader's
stock picking.

Benchmarks: the benchmark symbol (default SPY) and an equal-weight buy-and-hold
of the same pool.
