"""Add software and verified 2026 listings; preserve the original experiment."""
from dataclasses import asdict
from datetime import date
from pathlib import Path
import json
import akshare as ak
from fetch_data import write_rows
from qbacktest import Config, load_bars, run_backtest, summarize, write_outputs, sma, average_volume
from run_theme_backtest import GROUPS

SOFTWARE = "CRM NOW ADBE ORCL PLTR CRWD DDOG NET SNOW APP".split()
LISTED = {"SPCX": "2026-06-12", "SKHY": "2026-07-10"}
ROOT = Path("results/2026_us_expanded")


def diagnostics(data):
    examples = []
    for symbol in ["LITE", "AAOI", "COHR", "MU"]:
        bars = data[symbol]
        candidates = []
        for i in range(83, len(bars)):
            if bars[i].date.year != 2026:
                continue
            base = bars[i-20:i]
            bh, bl = max(b.high for b in base), min(b.low for b in base)
            if bars[i].close <= bh:
                continue
            prior = bh / min(b.close for b in bars[i-83:i-20]) - 1
            width = bh / bl - 1
            volume = bars[i].volume / average_volume(bars, i)
            higher = min(b.low for b in base[10:]) >= min(b.low for b in base[:10]) * .98
            ma10, ma20, ma50 = [sma(bars, i, w) for w in (10, 20, 50)]
            checks = {"prior_move>=30%": prior >= .3, "base_width<=25%": width <= .25,
                      "higher_lows": higher, "ma10>ma20>ma50": ma10 > ma20 > ma50,
                      "ma20_rising": ma20 > sma(bars, i-5, 20), "volume>=1.5x": volume >= 1.5}
            if all(checks.values()):
                continue
            candidates.append(dict(symbol=symbol, date=str(bars[i].date), prior_move=prior,
                                   base_width=width, volume_multiple=volume, ma10=ma10, ma20=ma20,
                                   ma50=ma50, failed=[k for k,v in checks.items() if not v]))
        if candidates:
            examples.append(min(candidates, key=lambda x: (len(x["failed"]), x["date"])))
    return examples


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    rows = []
    failures = {}
    for symbol in SOFTWARE + list(LISTED):
        cache = Path(f"data/expanded_{symbol}.csv")
        if cache.exists():
            print("cached", symbol, flush=True)
            continue
        try:
            frame = ak.stock_us_daily(symbol=symbol, adjust="qfq")
            dates = frame["date"].astype(str)
            frame = frame[(dates >= LISTED.get(symbol, "2025-06-01")) & (dates <= "2026-09-04")]
            if frame.empty:
                raise ValueError("No bars in requested date range")
            rows = [dict(date=str(b["date"])[:10], symbol=symbol, market="US", catalyst=0,
                         **{k:float(b[k]) for k in ("open", "high", "low", "close", "volume")}) for _,b in frame.iterrows()]
            write_rows(rows, str(cache))
            print(symbol, len(rows), rows[0]["date"], rows[-1]["date"], flush=True)
        except Exception as exc:
            failures[symbol] = str(exc)
            print("FAILED", symbol, str(exc), flush=True)
    data = load_bars("data/us_themes_23_sina.csv")
    for symbol in SOFTWARE + list(LISTED):
        cache = Path(f"data/expanded_{symbol}.csv")
        if cache.exists():
            data.update(load_bars(str(cache)))
    groups = {**GROUPS, "software": SOFTWARE}
    groups["storage"] = GROUPS["storage"] + ["SKHY"]
    groups["space"] = GROUPS["space"] + ["SPCX"]
    coverage = {s:dict(first=str(b[0].date), last=str(b[-1].date), bars=len(b)) for s,b in data.items()}
    assert all(b[-1].date == date(2026,9,4) for b in data.values()), coverage
    cfg = Config()
    report = ["# 2026 扩充主题池", "", "数据截止2026-09-04；新浪/AKShare前复权日线，参数沿用默认值。", "", "| 股票池 | 策略 | 收益 | 最大回撤 | 交易数 |", "|---|---|---:|---:|---:|"]
    for name, symbols in {"all": sorted(data), **groups}.items():
        available = {s:data[s] for s in symbols if s in data}
        for strategy in ("breakout", "ep", "both"):
            trades, curve = run_backtest(available, strategy, cfg, start_date=date(2026,1,1))
            result = summarize(trades, curve, cfg)
            result.update(universe=list(available), requested=symbols, data_source="Sina/AKShare qfq", cutoff="2026-09-04")
            write_outputs(str(ROOT / f"{name}_{strategy}"), trades, curve, result)
            report.append(f"| {name} | {strategy} | {result['total_return']:.2%} | {result['max_drawdown']:.2%} | {result['trades']} |")
    detail = dict(coverage=coverage, failures=failures, examples=diagnostics(data), listing_cutoffs=LISTED)
    (ROOT / "audit.json").write_text(json.dumps(detail, ensure_ascii=False, indent=2))
    report += ["", "新上市股票只使用上市后的美股数据，不拼接旧代码证券或韩股数据。Breakout需要83根历史日线，EP需要63根；历史不足时不会产生信号。", "", "各板块独立本金，合并池共享本金；主题池为当前指定股票，日线代理不能还原盘中入场，EP未核实消息催化。", "", "下载失败：" + str(failures)]
    (ROOT / "REPORT.md").write_text("\n".join(report)+"\n", encoding="utf-8")
    print("\n".join(report))
    print(json.dumps(detail, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
