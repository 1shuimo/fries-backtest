"""User-defined B1: underlying J<12, execution-product body>=5% thirds."""
import json
import math
from bisect import bisect_right
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

from chang_3221 import simulate, write_csv
from chang_research import Candidate, indicators
from leveraged_research import stocks, FOLDER, MAP, CUTOFF
from korea_leveraged import PAIRS, DATA
from qbacktest import load_bars

ROOT = Path('results/b1_j12_body5_2026')


def kdj(bars):
    k = d = 50.
    result = []
    for i,b in enumerate(bars):
        if i < 8:
            result.append(math.nan)
            continue
        window = bars[i-8:i+1]
        low, high = min(x.low for x in window), max(x.high for x in window)
        rsv = 100*(b.close-low)/(high-low) if high > low else 50.
        k = (2*k+rsv)/3
        d = (2*d+k)/3
        result.append(3*k-2*d)
    return result


def signals_for(source, products, above_ma60=False):
    signals = defaultdict(list)
    audit = []
    for s,bs in source.items():
        pb = products[s]
        dates = [b.date for b in pb]
        stats = indicators(pb)
        source_stats = indicators(bs) if above_ma60 else [{} for _ in bs]
        for b,j,r in zip(bs,kdj(bs),source_stats):
            if b.date.year != 2026 or not j < 12:
                continue
            if above_ma60 and not b.close > r['ma60']:
                audit.append(dict(symbol=s,signal_date=str(b.date),j=j,entry_date='',reason='not_above_ma60'))
                continue
            i = bisect_right(dates,b.date)
            reason = 'no_next_session'
            if 0 < i < len(pb):
                atr = stats[i-1]['atr']
                reason = 'eligible' if math.isfinite(atr) and atr > 0 else 'atr_warmup'
                if reason == 'eligible':
                    signals[dates[i]].append(Candidate(s,'b1_j12',b.date,atr))
            audit.append(dict(symbol=s,signal_date=str(b.date),j=j,
                entry_date=str(dates[i]) if i < len(pb) else '',reason=reason))
    # During HK holidays multiple Korean signals may queue for the same HK open.
    # Retain the latest available signal for each symbol, without duplicate sizing.
    return {day:list({c.symbol:c for c in cs}.values()) for day,cs in signals.items()}, audit


def save_run(label, data, signals, audit):
    summary,trades,fills,curve,skipped = simulate(data,signals,exit_rule='body5')
    folder = ROOT/label
    folder.mkdir(parents=True,exist_ok=True)
    for name,rows in [('trades',trades),('fills',fills),('equity',curve),('skipped',skipped),('signals',audit)]:
        write_csv(folder/f'{name}.csv',rows)
    return summary


def run():
    stock = stocks()
    coverage = json.loads((FOLDER/'coverage.json').read_text())
    us = {s:[replace(b,symbol=s) for b in load_bars(str(FOLDER/f"{r['etf']}.csv"))[r['etf']]]
          for s,r in coverage.items() if r['status']=='downloaded'}
    kr = {s:load_bars(str(DATA/f'{s}.csv'))[s] for s in PAIRS}
    hk = {s:[replace(b,symbol=s) for b in load_bars(str(DATA/f'{p}.csv'))[p]] for s,p in PAIRS.items()}
    summaries = {}
    rows = []
    for label,source,products,mapping in [
        ('US37',{s:stock[s] for s in us},us,{s:MAP[s]['etf'] for s in us}),
        ('HK2',kr,hk,PAIRS)]:
        signals,audit = signals_for(source,products)
        summaries[label] = save_run(label,products,signals,audit)
        for s,bs in products.items():
            single = {day:[c for c in cs if c.symbol==s] for day,cs in signals.items()}
            result = save_run(mapping[s],{s:bs},single,[r for r in audit if r['symbol']==s])
            rows.append(dict(symbol=s,product=mapping[s],group=label,**result))
    write_csv(ROOT/'per_product.csv',rows)
    (ROOT/'summary.json').write_text(json.dumps(dict(cutoff=str(CUTOFF),portfolios=summaries,products=rows),indent=2))
    lines = ['# B1（J<12）买入、阳线实体≥5%分三次退出', '',
        '2026-01-01至2026-09-15；使用2024年起历史预热；独立于旧结果。', '',
        '| 组合 | 收益 | 最大回撤 | 完整交易数 | 胜率 | 平均仓位 |', '|---|---:|---:|---:|---:|---:|']
    for s,r in summaries.items():
        lines.append(f"| {s} | {r['total_return']:.2%} | {r['max_drawdown']:.2%} | {r['trades']} | {r['win_rate']:.1%} | {r['mean_exposure']:.1%} |")
    lines += ['', '## 每个产品的独立账户（不是组合贡献，不能相加）', '',
        '| 正股 | 产品 | 收益 | 最大回撤 | 交易数 | 阳线卖出次数 |', '|---|---|---:|---:|---:|---:|']
    for r in sorted(rows,key=lambda r:(r['group'],r['product'])):
        lines.append(f"| {r['symbol']} | {r['product']} | {r['total_return']:.2%} | {r['max_drawdown']:.2%} | {r['trades']} | {r['body_sells']} |")
    lines += ['', '## 明确规则与假设', '',
        '- B1仅为用户给定的J<12条件，并非完整B1体系。KDJ(9,3,3)：满9根计算RSV；K=(2×前K+RSV)/3，D=(2×前D+K)/3，J=3K−2D；初始K=D=50；9根最高=最低时RSV=50。J不截断。',
        '- 买入看正股日线J<12，不要求刚下穿。空仓才买，持仓不加仓；退出后可由新的收盘信号再次入场。排除同日卖完马上再买。',
        '- 信号后严格下一个产品交易日开盘买；韩国假期错位按下一个香港交易日。止损ATR取入场前最后一个产品交易日；无当日收盘提前买入。',
        '- 卖出看产品自身close/open−1≥5%，超过7%同样触发；不是相对昨收涨幅，不是相对买入成本盈利。亏损状态下出现大阳线也减仓。',
        '- 每根符合条件阳线仅触发一次；次日开盘卖初始数量约1/3，累计1/3、2/3、全部。整数取整，第三次卖尽。入场当日阳线可触发次日卖出。',
        '- 2ATR20初始止损保持不变；开盘跳空低于止损按开盘价，止损优先。没有额外保本抬升或20/30/40%止盈。',
        '- 单笔风险0.5%、单品资金上限20%、初始10万各市场本币、单边0.1%成本；美元US37与港元HK2分开核算，未跨币种合并。',
        '- 股票排序决定同时信号的有限现金分配；日K不能完整复现成交路径与流动性。整数份额，未建模港股整手/最低佣金。',
        '- 复用此前可用的37只美股产品与两只港股产品；前次排除的16只异常数据和6只未确认产品仍未纳入。数据来源及复权限制沿用前次报告。',
        '- 样本末未平仓按最后收盘强制结算；完整交易数包括止损、第三次清仓和样本末结算，不等于分批成交次数。',
        '- 当前候选池、单年回测有选择偏差；此结果不是样本外验证，也不证明稳定盈利。',
        '- KDJ公式参考：https://capital.com/en-eu/learn/technical-analysis/kdj-indicator']
    (ROOT/'REPORT.md').write_text('\n'.join(lines)+'\n')
    print('\n'.join(lines))


if __name__ == '__main__':
    run()
