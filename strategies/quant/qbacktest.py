#!/usr/bin/env python3
"""Portfolio backtest for daily Qullamaggie-style breakout and EP proxies."""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class Bar:
    date: date
    symbol: str
    market: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    catalyst: bool = False


@dataclass
class Config:
    initial_cash: float = 100_000.0
    risk_fraction: float = 0.005
    max_position_fraction: float = 0.20
    cost_bps: float = 10.0
    base_days: int = 20
    prior_window: int = 63
    min_prior_move: float = 0.30
    max_base_width: float = 0.25
    breakout_volume_multiple: float = 1.5
    ep_gap: float = 0.10
    ep_volume_multiple: float = 3.0
    ep_max_prior_range: float = 0.50
    ep_require_catalyst: bool = False
    partial_after_days: int = 3
    partial_fraction: float = 0.50
    exit_ma: int = 10


@dataclass(frozen=True)
class Signal:
    strategy: str
    symbol: str
    signal_date: date
    entry_date: date
    signal_low: float
    adr: float


@dataclass
class Position:
    strategy: str
    symbol: str
    market: str
    entry_date: date
    entry_price: float
    initial_stop: float
    stop: float
    initial_shares: int
    shares: int
    entry_fee: float
    initial_risk: float
    bars_held: int = 0
    partial_taken: bool = False
    exit_next_open: bool = False
    proceeds_net: float = 0.0
    proceeds_gross: float = 0.0


@dataclass(frozen=True)
class Trade:
    strategy: str
    symbol: str
    market: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    shares: int
    pnl: float
    return_pct: float
    r_multiple: float
    reason: str


def mean(values: Iterable[float]) -> float:
    items = list(values)
    return sum(items) / len(items) if items else math.nan


def load_bars(path: str) -> dict[str, list[Bar]]:
    required = {"date", "symbol", "open", "high", "low", "close", "volume"}
    grouped: dict[str, list[Bar]] = defaultdict(list)
    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"CSV 缺少字段: {', '.join(sorted(missing))}")
        for row in reader:
            bar = Bar(
                date=date.fromisoformat(row["date"][:10]),
                symbol=row["symbol"].strip(),
                market=(row.get("market") or "US").strip().upper(),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
                catalyst=(row.get("catalyst") or "0").strip().lower() in {"1", "true", "yes"},
            )
            if min(bar.open, bar.high, bar.low, bar.close) <= 0 or bar.volume < 0:
                raise ValueError(f"{bar.symbol} {bar.date}: 非法 OHLCV")
            high_invalid = bar.high < max(bar.open, bar.close) and not math.isclose(
                bar.high, max(bar.open, bar.close), rel_tol=1e-12
            )
            low_invalid = bar.low > min(bar.open, bar.close) and not math.isclose(
                bar.low, min(bar.open, bar.close), rel_tol=1e-12
            )
            if high_invalid or low_invalid:
                raise ValueError(f"{bar.symbol} {bar.date}: OHLC 关系非法")
            grouped[bar.symbol].append(bar)
    for symbol, bars in grouped.items():
        bars.sort(key=lambda item: item.date)
        if len({bar.date for bar in bars}) != len(bars):
            raise ValueError(f"{symbol}: 存在重复日期")
    return dict(grouped)


def sma(bars: list[Bar], index: int, window: int) -> float:
    if index + 1 < window:
        return math.nan
    return mean(bar.close for bar in bars[index - window + 1 : index + 1])


def adr(bars: list[Bar], index: int, window: int = 20) -> float:
    start = max(1, index - window + 1)
    values = [(bars[i].high - bars[i].low) / bars[i - 1].close for i in range(start, index + 1)]
    return mean(values)


def average_volume(bars: list[Bar], end_exclusive: int, window: int = 20) -> float:
    start = max(0, end_exclusive - window)
    return mean(bar.volume for bar in bars[start:end_exclusive])


def breakout_at(bars: list[Bar], i: int, cfg: Config) -> bool:
    history_needed = cfg.base_days + cfg.prior_window
    if i < history_needed or i < 55:
        return False
    base = bars[i - cfg.base_days : i]
    earlier = bars[i - history_needed : i - cfg.base_days]
    base_high = max(bar.high for bar in base)
    base_low = min(bar.low for bar in base)
    prior_low = min(bar.close for bar in earlier)
    prior_move = base_high / prior_low - 1.0
    half = len(base) // 2
    higher_lows = min(bar.low for bar in base[half:]) >= min(bar.low for bar in base[:half]) * 0.98
    volume_ok = bars[i].volume >= average_volume(bars, i) * cfg.breakout_volume_multiple
    ma10 = sma(bars, i, 10)
    ma20 = sma(bars, i, 20)
    ma50 = sma(bars, i, 50)
    ma20_old = sma(bars, i - 5, 20)
    return all(
        (
            prior_move >= cfg.min_prior_move,
            base_high / base_low - 1.0 <= cfg.max_base_width,
            higher_lows,
            bars[i].close > base_high,
            ma10 > ma20 > ma50,
            ma20 > ma20_old,
            volume_ok,
        )
    )


def ep_at(bars: list[Bar], i: int, cfg: Config) -> bool:
    if i < max(20, cfg.prior_window):
        return False
    previous = bars[i - cfg.prior_window : i]
    gap = bars[i].open / bars[i - 1].close - 1.0
    prior_range = max(bar.close for bar in previous) / min(bar.close for bar in previous) - 1.0
    volume_ok = bars[i].volume >= average_volume(bars, i) * cfg.ep_volume_multiple
    catalyst_ok = bars[i].catalyst or not cfg.ep_require_catalyst
    return all(
        (
            gap >= cfg.ep_gap,
            prior_range <= cfg.ep_max_prior_range,
            volume_ok,
            bars[i].close > bars[i].open,
            catalyst_ok,
        )
    )


def build_signals(grouped: dict[str, list[Bar]], strategy: str, cfg: Config) -> dict[date, list[Signal]]:
    signals: dict[date, list[Signal]] = defaultdict(list)
    for symbol, bars in grouped.items():
        for i in range(len(bars) - 1):
            matched: list[str] = []
            is_breakout = strategy in {"breakout", "both"} and breakout_at(bars, i, cfg)
            is_ep = strategy in {"ep", "both"} and ep_at(bars, i, cfg)
            if is_ep:
                matched.append("ep")
            elif is_breakout:
                matched.append("breakout")
            for name in matched:
                signals[bars[i + 1].date].append(
                    Signal(name, symbol, bars[i].date, bars[i + 1].date, bars[i].low, adr(bars, i))
                )
    return signals


class Portfolio:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.cash = cfg.initial_cash
        self.positions: dict[str, Position] = {}
        self.trades: list[Trade] = []

    def fee(self, value: float) -> float:
        return value * self.cfg.cost_bps / 10_000.0

    def equity(self, bars_today: dict[str, Bar], last_close: dict[str, float], at_open: bool = False) -> float:
        value = self.cash
        for symbol, position in self.positions.items():
            if symbol in bars_today:
                price = bars_today[symbol].open if at_open else bars_today[symbol].close
            else:
                price = last_close[symbol]
            value += position.shares * price
        return value

    def enter(self, signal: Signal, bar: Bar, equity: float) -> None:
        if signal.symbol in self.positions or not math.isfinite(signal.adr) or signal.adr <= 0:
            return
        entry = bar.open
        stop = max(signal.signal_low, entry * (1.0 - signal.adr))
        if stop >= entry:
            stop = entry * (1.0 - signal.adr)
        unit_risk = entry - stop
        if unit_risk <= 0:
            return
        by_risk = math.floor(equity * self.cfg.risk_fraction / unit_risk)
        by_position = math.floor(equity * self.cfg.max_position_fraction / entry)
        by_cash = math.floor(self.cash / (entry * (1.0 + self.cfg.cost_bps / 10_000.0)))
        shares = min(by_risk, by_position, by_cash)
        if shares < 1:
            return
        value = shares * entry
        entry_fee = self.fee(value)
        self.cash -= value + entry_fee
        self.positions[signal.symbol] = Position(
            strategy=signal.strategy,
            symbol=signal.symbol,
            market=bar.market,
            entry_date=bar.date,
            entry_price=entry,
            initial_stop=stop,
            stop=stop,
            initial_shares=shares,
            shares=shares,
            entry_fee=entry_fee,
            initial_risk=unit_risk * shares,
        )

    def sell(self, symbol: str, qty: int, price: float) -> None:
        position = self.positions[symbol]
        value = qty * price
        fee = self.fee(value)
        self.cash += value - fee
        position.proceeds_net += value - fee
        position.proceeds_gross += value
        position.shares -= qty

    def close(self, symbol: str, bar: Bar, price: float, reason: str) -> None:
        position = self.positions[symbol]
        self.sell(symbol, position.shares, price)
        cost = position.entry_price * position.initial_shares + position.entry_fee
        pnl = position.proceeds_net - cost
        self.trades.append(
            Trade(
                strategy=position.strategy,
                symbol=symbol,
                market=position.market,
                entry_date=position.entry_date.isoformat(),
                exit_date=bar.date.isoformat(),
                entry_price=round(position.entry_price, 6),
                exit_price=round(position.proceeds_gross / position.initial_shares, 6),
                shares=position.initial_shares,
                pnl=round(pnl, 2),
                return_pct=round(pnl / cost, 6),
                r_multiple=round(pnl / position.initial_risk, 4),
                reason=reason,
            )
        )
        del self.positions[symbol]


def run_backtest(grouped: dict[str, list[Bar]], strategy: str, cfg: Config, start_date: date | None = None) -> tuple[list[Trade], list[dict[str, object]]]:
    signals = build_signals(grouped, strategy, cfg)
    by_date: dict[date, dict[str, Bar]] = defaultdict(dict)
    indices: dict[str, dict[date, int]] = {}
    for symbol, bars in grouped.items():
        indices[symbol] = {}
        for i, bar in enumerate(bars):
            by_date[bar.date][symbol] = bar
            indices[symbol][bar.date] = i

    portfolio = Portfolio(cfg)
    last_close: dict[str, float] = {}
    curve: list[dict[str, object]] = []
    for current_date in sorted(by_date):
        today = by_date[current_date]
        if start_date is not None and current_date < start_date:
            continue

        for symbol in list(portfolio.positions):
            position = portfolio.positions[symbol]
            bar = today.get(symbol)
            if bar and position.exit_next_open:
                portfolio.close(symbol, bar, bar.open, "moving_average")

        opening_equity = portfolio.equity(today, last_close, at_open=True)
        for signal in sorted(signals.get(current_date, []), key=lambda item: (item.strategy, item.symbol)):
            bar = today.get(signal.symbol)
            if bar:
                portfolio.enter(signal, bar, opening_equity)

        for symbol in list(portfolio.positions):
            position = portfolio.positions[symbol]
            bar = today.get(symbol)
            if not bar:
                continue
            entered_today = position.entry_date == current_date
            can_sell = not (entered_today and position.market == "CN")
            if can_sell and bar.low <= position.stop:
                fill = min(bar.open, position.stop)
                portfolio.close(symbol, bar, fill, "stop")
                continue

            position.bars_held += 1
            if not position.partial_taken and position.bars_held >= cfg.partial_after_days:
                qty = max(1, math.floor(position.shares * cfg.partial_fraction))
                if qty < position.shares:
                    portfolio.sell(symbol, qty, bar.close)
                    position.partial_taken = True
                    position.stop = max(position.stop, position.entry_price)

            i = indices[symbol][current_date]
            moving_average = sma(grouped[symbol], i, cfg.exit_ma)
            if math.isfinite(moving_average) and bar.close < moving_average:
                position.exit_next_open = True

        for symbol, bar in today.items():
            last_close[symbol] = bar.close
        curve.append({"date": current_date.isoformat(), "equity": round(portfolio.equity(today, last_close), 2)})

    if by_date:
        final_date = max(by_date)
        final_bars = by_date[final_date]
        for symbol in list(portfolio.positions):
            bar = final_bars.get(symbol) or grouped[symbol][-1]
            portfolio.close(symbol, bar, bar.close, "end_of_data")
        if curve:
            curve[-1]["equity"] = round(portfolio.cash, 2)
    return portfolio.trades, curve


def summarize(trades: list[Trade], curve: list[dict[str, object]], cfg: Config) -> dict[str, object]:
    final_equity = float(curve[-1]["equity"]) if curve else cfg.initial_cash
    total_return = final_equity / cfg.initial_cash - 1.0
    peak = -math.inf
    max_drawdown = 0.0
    for row in curve:
        equity = float(row["equity"])
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity / peak - 1.0)
    years = 0.0
    if len(curve) >= 2:
        years = (date.fromisoformat(str(curve[-1]["date"])) - date.fromisoformat(str(curve[0]["date"]))).days / 365.25
    cagr = (final_equity / cfg.initial_cash) ** (1.0 / years) - 1.0 if years > 0 and final_equity > 0 else 0.0
    wins = [trade for trade in trades if trade.pnl > 0]
    gross_profit = sum(trade.pnl for trade in wins)
    gross_loss = -sum(trade.pnl for trade in trades if trade.pnl < 0)
    return {
        "initial_cash": cfg.initial_cash,
        "final_equity": round(final_equity, 2),
        "total_return": round(total_return, 6),
        "cagr": round(cagr, 6),
        "max_drawdown": round(max_drawdown, 6),
        "trades": len(trades),
        "win_rate": round(len(wins) / len(trades), 6) if trades else 0.0,
        "average_r": round(mean(trade.r_multiple for trade in trades), 4) if trades else 0.0,
        "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss else None,
        "config": asdict(cfg),
    }


def write_outputs(output_dir: str, trades: list[Trade], curve: list[dict[str, object]], summary: dict[str, object]) -> None:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    with (target / "summary.json").open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)
    with (target / "trades.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(Trade.__dataclass_fields__))
        writer.writeheader()
        writer.writerows(asdict(trade) for trade in trades)
    with (target / "equity.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["date", "equity"])
        writer.writeheader()
        writer.writerows(curve)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--start-date", type=date.fromisoformat, help="开始交易日期；更早的数据仅用于指标预热")
    parser.add_argument("--strategy", choices=("breakout", "ep", "both"), default="both")
    parser.add_argument("--output-dir", default="results")
    parser.add_argument("--initial-cash", type=float, default=100_000.0)
    parser.add_argument("--risk", type=float, default=0.005, help="单笔净值风险比例")
    parser.add_argument("--max-position", type=float, default=0.20, help="单票最大净值比例")
    parser.add_argument("--cost-bps", type=float, default=10.0, help="单边手续费与滑点，bps")
    parser.add_argument("--exit-ma", type=int, choices=(10, 20), default=10)
    parser.add_argument("--ep-require-catalyst", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = Config(
        initial_cash=args.initial_cash,
        risk_fraction=args.risk,
        max_position_fraction=args.max_position,
        cost_bps=args.cost_bps,
        exit_ma=args.exit_ma,
        ep_require_catalyst=args.ep_require_catalyst,
    )
    grouped = load_bars(args.input)
    trades, curve = run_backtest(grouped, args.strategy, cfg, start_date=args.start_date)
    summary = summarize(trades, curve, cfg)
    write_outputs(args.output_dir, trades, curve, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
