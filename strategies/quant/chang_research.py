"""Technical proxies inferred from user-supplied 2026-09-15 notes, not a replica."""
from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import date
from pathlib import Path
import csv
import json
import math

from qbacktest import Bar, Config, Portfolio, Signal, load_bars, sma, mean, summarize, write_outputs


@dataclass(frozen=True)
class Candidate:
    symbol: str
    strategy: str
    signal_date: date
    atr: float


def indicators(bars):
    """Weekly MA uses only weeks completed before the current calendar week."""
    completed = []
    week = None
    last_close = None
    true_ranges = []
    rows = []
    for i, bar in enumerate(bars):
        current_week = bar.date.isocalendar()[:2]
        if week is not None and current_week != week:
            completed.append(last_close)
        week = current_week
        weekly_ma = mean(completed[-45:]) if len(completed) >= 45 else math.nan
        previous = bars[i-1].close if i else bar.close
        true_ranges.append(max(bar.high-bar.low, abs(bar.high-previous), abs(bar.low-previous)))
        rows.append(dict(
            ma60=sma(bars,i,60), ma120=sma(bars,i,120), ma250=sma(bars,i,250),
            weekly_ma=weekly_ma, weekly_close=completed[-1] if completed else math.nan,
            return4=bar.close/bars[i-4].close-1 if i>=4 else math.nan,
            volume_ratio=bar.volume/mean(b.volume for b in bars[i-20:i]) if i>=20 and mean(b.volume for b in bars[i-20:i])>0 else math.nan,
            atr=mean(true_ranges[-20:]) if i>=19 else math.nan,
        ))
        last_close = bar.close
    return rows


def make_signals(data, mode):
    signals = defaultdict(list)
    audit = []
    for symbol, bars in data.items():
        stats = indicators(bars)
        previous_dip = False
        for i, bar in enumerate(bars):
            r = stats[i]
            dip = r["weekly_close"] < r["weekly_ma"] and -.1975 <= r["return4"] <= -.075
            trend = i > 0 and bars[i-1].close <= stats[i-1]["ma60"] and bar.close > r["ma60"] and bar.close > r["ma120"] and bar.close > r["ma250"] and r["volume_ratio"] >= 1.7
            label = "dip45" if mode in ("dip45", "both") and dip and not previous_dip else "trend" if mode in ("trend", "both") and trend else None
            previous_dip = dip
            if label and math.isfinite(r["atr"]) and r["atr"] > 0:
                # An absent next bar leaves a watchlist signal, never a fabricated fill.
                entry = bars[i+1].date if i+1 < len(bars) else None
                audit.append(dict(symbol=symbol, strategy=label, signal_date=str(bar.date), entry_date=str(entry) if entry else "", **r))
                if entry:
                    signals[entry].append(Candidate(symbol,label,bar.date,r["atr"]))
    return signals, audit


def simulate(data, signals, cfg, start=date(2026,1,1)):
    by_date = defaultdict(dict)
    indices = {}
    for symbol,bars in data.items():
        indices[symbol] = {b.date:i for i,b in enumerate(bars)}
        for bar in bars:
            by_date[bar.date][symbol] = bar
    p = Portfolio(cfg)
    last = {}
    pending = {}
    curve = []
    exposure = []
    skipped = []
    for day in sorted(by_date):
        if day < start:
            continue
        today = by_date[day]
        for symbol in list(p.positions):
            if symbol in pending and symbol in today:
                p.close(symbol,today[symbol],today[symbol].open,pending.pop(symbol))
        equity_open = p.equity(today,last,at_open=True)
        for c in sorted(signals.get(day,[]),key=lambda x:(x.strategy,x.symbol)):
            bar = today[c.symbol]
            distance = 2*c.atr
            if c.symbol in p.positions:
                skipped.append(dict(date=str(day),symbol=c.symbol,reason="already_held"))
                continue
            if distance >= bar.open:
                skipped.append(dict(date=str(day),symbol=c.symbol,reason="stop_below_zero"))
                continue
            # Signal ATR was known at yesterday's close; today's open is used only for execution sizing.
            p.enter(Signal(c.strategy,c.symbol,c.signal_date,day,0,distance/bar.open),bar,equity_open)
            if c.symbol not in p.positions:
                skipped.append(dict(date=str(day),symbol=c.symbol,reason="cash_or_size"))
        for symbol in list(p.positions):
            if symbol not in today:
                continue
            position, bar = p.positions[symbol], today[symbol]
            if bar.low <= position.stop:
                p.close(symbol,bar,min(bar.open,position.stop),"stop_2atr")
                continue
            position.bars_held += 1
            avg = sma(data[symbol],indices[symbol][day],60)
            if position.strategy == "dip45" and bar.close >= avg:
                pending[symbol] = "recovered_ma60"
            elif position.strategy == "trend" and bar.close < avg:
                pending[symbol] = "lost_ma60"
            elif position.bars_held >= (20 if position.strategy == "dip45" else 60):
                pending[symbol] = "time_exit"
        for s,b in today.items():
            last[s] = b.close
        value = p.equity(today,last)
        curve.append(dict(date=str(day),equity=round(value,2)))
        exposure.append((value-p.cash)/value)
    # Force liquidation on the shared end date, including cost, for comparable cash P&L.
    if curve:
        final = date.fromisoformat(curve[-1]["date"])
        for s in list(p.positions):
            assert s in by_date[final], f"Stale endpoint for {s}"
            b = by_date[final][s]
            p.close(s,b,b.close,"end_of_data")
        curve[-1]["equity"] = round(p.cash,2)
    return p.trades,curve,exposure,skipped


def main():
    data = {}
    for path in sorted(Path("data/chang_20260915").glob("*.csv")):
        data.update(load_bars(str(path)))
    if len(data) != 38:
        raise ValueError(f"Expected 38 symbols, got {len(data)}")
    # Align to the latest complete date shared by every downloaded security.
    cutoff = min(bars[-1].date for bars in data.values())
    data = {s:[b for b in bars if b.date<=cutoff] for s,bars in data.items()}
    root = Path("results/chang_2026")
    root.mkdir(parents=True,exist_ok=True)
    cfg = Config(initial_cash=100000,risk_fraction=.005,max_position_fraction=.20,cost_bps=10)
    report = ["# 常总笔记技术代理研究", "", f"38只美股，2026年初至{cutoff}；2024年起数据用于预热。", "", "根据9月15日笔记事后定义，属于回溯研究，不是样本外验证或原作者实盘成绩。", "", "| 模块 | 收益 | 最大回撤 | 笔数 | 胜率 | 平均资金投入 |", "|---|---:|---:|---:|---:|---:|"]
    coverage = {s:dict(first=str(b[0].date),last=str(b[-1].date),bars=len(b),ma45_ready=math.isfinite(indicators(b)[-1]["weekly_ma"]),ma250_ready=len(b)>=250) for s,b in data.items()}
    (root/"coverage.json").write_text(json.dumps(coverage,indent=2))
    for mode in ("dip45","trend","both"):
        signals,audit = make_signals(data,mode)
        trades,curve,exposure,skipped = simulate(data,signals,cfg)
        summary = summarize(trades,curve,cfg)
        # Only expose settings actually used by this engine, not Qullamaggie defaults.
        summary["config"] = dict(initial_cash=cfg.initial_cash,risk_fraction=cfg.risk_fraction,max_position_fraction=cfg.max_position_fraction,cost_bps=cfg.cost_bps,stop_atr_multiple=2,dip_max_bars=20,trend_max_bars=60,exit_ma=60)
        summary.update(start="2026-01-01",end=str(cutoff),mean_exposure=mean(exposure),universe=sorted(data),is_original_replica=False)
        write_outputs(str(root/mode),trades,curve,summary)
        (root/mode/"signals.json").write_text(json.dumps([r for r in audit if r["signal_date"]>="2026-01-01"],ensure_ascii=False,indent=2,allow_nan=True))
        (root/mode/"skipped.json").write_text(json.dumps(skipped,indent=2))
        report.append(f"| {mode} | {summary['total_return']:.2%} | {summary['max_drawdown']:.2%} | {len(trades)} | {summary['win_rate']:.2%} | {mean(exposure):.2%} |")
    report += ["", "规则与原文歧义、测试假设见项目根目录 CHANG_SYSTEM.md。不完整的基本面/资金面数据未伪装成3D共振；没有实现未解释的3-2-2-1加仓。", "", "本股票池包含当前选定的主题股，存在选择偏差。复权价格为研究单位，真实原价下的整数股、公司行动和冲击成本未完整建模。"]
    (root/"REPORT.md").write_text("\n".join(report)+"\n",encoding="utf-8")
    print("\n".join(report))


if __name__ == "__main__":
    main()
