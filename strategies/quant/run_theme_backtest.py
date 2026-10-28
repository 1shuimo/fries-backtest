"""Reproduce the user's 2026 US sector-universe experiment."""
from datetime import date
from pathlib import Path

from qbacktest import Config, load_bars, run_backtest, summarize, write_outputs

GROUPS = {
    "mag7": "AAPL MSFT GOOGL AMZN META NVDA TSLA".split(),
    "storage": "MU WDC STX SNDK".split(),
    "optical": "COHR LITE CIEN AAOI FN".split(),
    "space": "RKLB ASTS LUNR RDW PL BKSY SPCE".split(),
}


def main():
    data = load_bars("data/us_themes_23_sina.csv")
    expected = {s for group in GROUPS.values() for s in group}
    if set(data) != expected:
        raise ValueError(f"Universe mismatch: missing={expected - set(data)}")
    for symbol, bars in data.items():
        assert bars[-1].date == date(2026, 9, 4), (symbol, bars[-1].date)
        assert sum(b.date < date(2026, 1, 1) for b in bars) >= 83, symbol
    root = Path("results/2026_us_themes")
    root.mkdir(parents=True, exist_ok=True)
    cfg = Config()
    report = ["# 2026 美股主题池回测", "", "区间：2026年初至9月4日；年初空仓，2025年数据只用于预热。", "", "初始资金100,000美元；每笔风险0.5%，单票上限20%，单边成本0.1%，MA10退出。", ""]
    report.extend(f"- {name}: {', '.join(symbols)}" for name, symbols in GROUPS.items())
    report += ["", "| 股票池 | 策略 | 收益 | 最大回撤 | 交易数 | 胜率 |", "|---|---|---:|---:|---:|---:|"]
    for name, symbols in {"all": sorted(expected), **GROUPS}.items():
        for strategy in ("breakout", "ep", "both"):
            trades, curve = run_backtest({s: data[s] for s in symbols}, strategy, cfg, start_date=date(2026, 1, 1))
            assert all(t.entry_date >= "2026-01-01" for t in trades)
            result = summarize(trades, curve, cfg)
            result.update(universe=symbols, start=curve[0]["date"], end=curve[-1]["date"], source="Sina via AKShare, qfq")
            write_outputs(str(root / f"{name}_{strategy}"), trades, curve, result)
            report.append(f"| {name} | {strategy} | {result['total_return']:.2%} | {result['max_drawdown']:.2%} | {result['trades']} | {result['win_rate']:.2%} |")
    report += ["", "各板块独立使用100,000美元本金，收益不能相加。合并池共享本金；同时出现信号按策略名、股票代码排序分配可用现金。", "", "本实验沿用日线代理规则：当日收盘确认，下一交易日开盘入场；EP未核实消息催化，未还原盘中opening range。主题池为当前指定的代表股票，非完整行业或历史动态股票池。"]
    (root / "REPORT.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print("\n".join(report))


if __name__ == "__main__":
    main()
