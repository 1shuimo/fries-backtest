"""B1 above MA60 with one-time day-2/day-3 profitability checks, no price stop."""
import json
from dataclasses import replace
from pathlib import Path

from b1_research import signals_for
from chang_3221 import simulate, write_csv
from leveraged_research import stocks, FOLDER, MAP, CUTOFF
from korea_leveraged import PAIRS, DATA
from qbacktest import load_bars

ROOT = Path('results/b1_ma60_time_exit_2026')


def save(label,data,signals,audit,day):
    summary,trades,fills,curve,skipped = simulate(data,signals,exit_rule='body5',
        profit_check_day=day,price_stop=False)
    summary['time_exits'] = sum(t['reason'].startswith('not_profitable_') for t in trades)
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
    results, per = {}, []
    for label,source,products,mapping in [('US37',{s:stock[s] for s in us},us,{s:MAP[s]['etf'] for s in us}),('HK2',kr,hk,PAIRS)]:
        signals,audit = signals_for(source,products,above_ma60=True)
        for day in (2,3):
            results[f'{label}_D{day}'] = save(f'{label}_D{day}',products,signals,audit,day)
            for s,bs in products.items():
                subset = {d:[c for c in cs if c.symbol==s] for d,cs in signals.items()}
                result = save(f'{mapping[s]}_D{day}',{s:bs},subset,[r for r in audit if r['symbol']==s],day)
                per.append(dict(symbol=s,product=mapping[s],group=label,check_day=day,**result))
    write_csv(ROOT/'per_product.csv',per)
    (ROOT/'summary.json').write_text(json.dumps(dict(cutoff=str(CUTOFF),portfolios=results,products=per),indent=2))
    lines = ['# J<12且站上60日线，2/3日不盈利退出', '', '2026年初至2026-09-15；与旧版相同37只美国杠杆产品和2只香港杠杆产品。', '',
        '| 组合 | 收益 | 最大回撤 | 交易数 | 胜率 | 平均仓位 | 时间退出次数 |', '|---|---:|---:|---:|---:|---:|---:|']
    for key,r in results.items():
        lines.append(f"| {key} | {r['total_return']:.2%} | {r['max_drawdown']:.2%} | {r['trades']} | {r['win_rate']:.1%} | {r['mean_exposure']:.1%} | {r['time_exits']} |")
    lines += ['', '## 单品独立账户（不能相加）', '', '| 正股 | 产品 | 检查日 | 收益 | 最大回撤 | 交易数 |', '|---|---|---:|---:|---:|---:|']
    for r in sorted(per,key=lambda r:(r['group'],r['product'],r['check_day'])):
        lines.append(f"| {r['symbol']} | {r['product']} | {r['check_day']} | {r['total_return']:.2%} | {r['max_drawdown']:.2%} | {r['trades']} |")
    lines += ['', '## 规则', '',
        '- 买入：正股KDJ(9,3,3)的J<12且当日收盘>当日60日简单均线；不要求新下穿J=12。下一产品交易日开盘买入，持仓不加仓，不同日卖完再买。',
        '- 入场交易日算第1天，只数产品有行情的交易日。第2/3天收盘各检查一次，净盈亏≤0时下一产品交易日开盘卖尽。此前盈利过但检查日已转亏也退出。',
        '- 净盈亏=已减仓净回款+剩余份额×收盘×(1−卖出成本)−初始含买入成本投入。已实现和未实现合算。',
        '- 检查日已盈利则通过，以后不再进行该时间退出检查；后来转亏不会自动退出。这不是保本止损。取消2ATR价格止损，无兜底价格止损。',
        '- 产品close/open−1≥5%（含超过7%）触发次日开盘卖初始份额1/3；三次清仓。时间退出与大阳线同时触发时卖尽优先。',
        '- 仅沿用2ATR仓位计算尺度：资金×0.5%÷(2ATR+双边预估成本)，上限单品20%。由于取消价格止损，0.5%不再是单笔亏损上限；沿用distance≥开盘价跳过的尺寸保护。',
        '- 各账户初始10万本币，单边0.1%成本，US/HK不混币种。日K、复权、整手/最低佣金、流动性等限制同旧报告。样本末强制结算。',
        '- 同时改变了均线过滤和退出方式，与旧版差异不能单独归因于某一条。当前候选池存在选择偏差，单年结果不是样本外验证。',
        '- 未下载新行情，数据截止与旧版一致。逐笔记录、信号和资金曲线在各目录内。']
    (ROOT/'REPORT.md').write_text('\n'.join(lines)+'\n')
    print('\n'.join(lines[:11]))


if __name__ == '__main__':
    run()
