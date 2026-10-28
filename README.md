# fries-backtest

## Strategy implementations / 策略目录

- Root directory: Claude's FriesTrader rules engine, stock pool, results and existing GitHub Actions workflow (preserved).
- [`strategies/quant`](strategies/quant/README.md): B1/KDJ + MA60, 2/3-day time exits, industry-capped next-open execution, 45-week oversold/322 proxies, Qullamaggie breakout/EP and historical leveraged-product experiments, with tests and data-fetch scripts.

The engines coexist; their execution assumptions and results are **not interchangeable**. The root workflow continues to run FriesTrader only. Quant experiments require their own downloaded inputs and are run from `strategies/quant`; private source watchlists and local market-data caches are not included.

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
