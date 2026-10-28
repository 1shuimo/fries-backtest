"""User-list, old-pool and merged-pool comparison under next-open-only rules."""
import json
from collections import defaultdict
from dataclasses import replace
from datetime import date
from pathlib import Path

from b1_research import signals_for
from chang_research import Candidate,indicators,make_signals
from chang_3221 import write_csv
from import_watchlist import ROOT as DATA
from leveraged_research import stocks,CUTOFF
from qbacktest import load_bars,build_signals,Config
from sector_b1 import simulate

ROOT=Path('results/watchlist_merged_next_open_2026')
SPECS={
    'b1_ma60_d2':('B1＋60日线＋第2天检查','body5',2),
    'b1_ma60_d3':('B1＋60日线＋第3天检查','body5',3),
    'b1_ma60':('B1＋60日线，无时间退出','body5',None),
    'b1':('B1，无60日线过滤','body5',None),
    'trend_322':('均线突破＋322','322',None),
    'dip45_322':('45周超跌＋322','322',None),
    'both_322':('突破/超跌＋322','322',None),
    'trend_control':('均线突破＋均线退出','control',None),
    'dip45_control':('45周超跌＋均线退出','control',None),
    'both_control':('突破/超跌＋均线退出','control',None),
    'q_breakout':('Q整理突破＋MA10代理','q_ma10',None),
    'q_ep':('Q跳空EP＋MA10代理','q_ma10',None),
    'q_both':('Q突破/EP＋MA10代理','q_ma10',None),
}


def normalize_azn(bars):
    """Two former ADSs equal one ordinary share; express history in current units."""
    return [replace(b,open=b.open*2,high=b.high*2,low=b.low*2,close=b.close*2,volume=b.volume/2)
            if b.date<date(2026,2,2) else b for b in bars]


def signal_sets(data):
    out={}
    out['b1'],_=signals_for(data,data)
    filtered,_=signals_for(data,data,above_ma60=True)
    for key in ('b1_ma60','b1_ma60_d2','b1_ma60_d3'):
        out[key]=filtered
    for mode in ('trend','dip45','both'):
        sig,_=make_signals(data,mode)
        for rule in ('322','control'):
            out[f'{mode}_{rule}']=sig
    stats={s:{b.date:r['atr'] for b,r in zip(bs,indicators(bs))} for s,bs in data.items()}
    for mode in ('breakout','ep','both'):
        raw=build_signals(data,mode,Config())
        out[f'q_{mode}']={d:[Candidate(c.symbol,c.strategy,c.signal_date,stats[c.symbol][c.signal_date]) for c in cs] for d,cs in raw.items()}
    return {k:{d:[c for c in cs if c.signal_date>=date(2026,1,1)] for d,cs in sig.items() if d.year==2026} for k,sig in out.items()}


def run():
    manifest=json.loads((DATA/'universe.json').read_text())
    coverage=json.loads((DATA/'coverage.json').read_text())
    original=stocks()
    data=dict(original)
    for s,r in coverage.items():
        if s not in data and r['status']=='available':
            data[s]=load_bars(str(DATA/f'{s}.csv'))[s]
    # Old WOLF equity was cancelled; do not feed its history into new-equity indicators.
    if 'WOLF' in data:
        data['WOLF']=[b for b in data['WOLF'] if b.date>=date(2025,9,29)]
    if 'AZN' in data:
        data['AZN']=normalize_azn(data['AZN'])
    assert all(bs[-1].date==CUTOFF for bs in data.values())
    sectors=manifest['sectors']
    subsets={'original59':original,'list_only':{s:bs for s,bs in data.items() if s in manifest['list_symbols']},'merged':data}
    sig=signal_sets(data)
    results={}
    for pool,selected in subsets.items():
        results[pool]={}
        for key,(label,rule,day) in SPECS.items():
            r,trades,fills,curve,weights,contrib,skips=simulate(selected,sig[key],day,'close_only',sectors,rule)
            if pool=='merged':
                for n in (1,3,5):
                    exclude=set(r['top_symbols'][:n])
                    stress,*_=simulate({s:bs for s,bs in selected.items() if s not in exclude},sig[key],day,'close_only',sectors,rule)
                    r[f'exclude_top{n}_return']=stress['total_return']
            monthly={}
            ends={str(row['date'])[:7]:row['equity'] for row in curve}
            previous=100000.
            for month,eq in ends.items():
                monthly[month]=eq/previous-1
                previous=eq
            r['monthly_returns']=monthly
            results[pool][key]=r
            folder=ROOT/pool/key
            folder.mkdir(parents=True,exist_ok=True)
            for name,rows in [('trades',trades),('fills',fills),('equity',curve),('sector_weights',weights),('contributions',contrib),('skipped',skips)]:
                write_csv(folder/f'{name}.csv',rows)
            print(pool,key,f"{r['total_return']:.2%}",f"DD {r['max_drawdown']:.2%}",flush=True)
    counts={k:len(v) for k,v in subsets.items()}
    (ROOT/'summary.json').write_text(json.dumps(dict(cutoff=str(CUTOFF),counts=counts,results=results),ensure_ascii=False,indent=2))
    audit=[]
    for row in manifest['audit']:
        audit.append(dict(**row,data_status=coverage.get(row['symbol'],{}).get('status','not_requested'),
            error=coverage.get(row['symbol'],{}).get('error','')))
    write_csv(ROOT/'list_audit.csv',audit)
    write_csv(ROOT/'merged_universe.csv',[dict(symbol=s,sector=sectors[s],in_user_list=s in manifest['list_symbols'],
        first=str(bs[0].date),last=str(bs[-1].date),rows=len(bs)) for s,bs in sorted(data.items())])
    flat=[]
    for pool,rs in results.items():
        for key,r in rs.items():
            flat.append(dict(pool=pool,strategy=SPECS[key][0],total_return=r['total_return'],max_drawdown=r['max_drawdown'],
                trades=r['trades'],win_rate=r['win_rate'],mean_exposure=r['mean_exposure'],profitable_stocks=r['profitable_symbols'],
                traded_stocks=r['traded_symbols'],top3_positive_share=r['top3_positive_share'],
                exclude_top5_return=r.get('exclude_top5_return','')))
    write_csv(ROOT/'comparison.csv',flat)
    ranked=sorted(results['merged'],key=lambda k:results['merged'][k]['total_return'],reverse=True)
    lines=['# 自选名单与旧池合并：仅下一开盘操作', '',
        f"原名单{manifest['rows']}条，正股候选{len(manifest['list_symbols'])}只；原池59只，合并候选{len(manifest['merged_symbols'])}只。可用：名单池{counts['list_only']}只，合并池{counts['merged']}只。",
        '区间2026年初至9月15日；起始10万美元；不融资；单边0.1%成本。原CSV仅提供代码和行业，不把最新报价或年内涨幅作为历史信号。', '',
        '| 策略 | 原59股收益 | 名单池收益 | 合并池收益 | 合并池回撤 | 盈利/交易股票 | 前3占正贡献 | 剔除前5重跑收益 |',
        '|---|---:|---:|---:|---:|---:|---:|---:|']
    for key in ranked:
        r=results['merged'][key]
        share=f"{r['top3_positive_share']:.1%}" if r['top3_positive_share'] is not None else '无'
        lines.append(f"| {SPECS[key][0]} | {results['original59'][key]['total_return']:.2%} | {results['list_only'][key]['total_return']:.2%} | {r['total_return']:.2%} | {r['max_drawdown']:.2%} | {r['profitable_symbols']}/{r['traded_symbols']} | {share} | {r['exclude_top5_return']:.2%} |")
    lines+=['','## 统一的交易及风险规则','',
        '- 所有正常买卖只在开盘成交，依据此前已完成收盘数据；不使用盘中高低价触发止损。最终日期收盘仅强制结算并扣成本，是估值终止处理而非真实开盘交易。',
        '- 初始2ATR20价格止损：收盘价≤初始止损线，下一交易日开盘全部退出。跳空按下一开盘实际价处理，可能远低于触发价格；若前收未触发，只在新一日收盘判断，不在开盘临时新增卖出信号。',
        '- 盈转亏：整笔交易扣费净盈亏（已卖净回款+剩余市值扣预估卖出费−初始含费成本）曾在收盘为正，之后收盘≤0，下一开盘清仓。不看盘中短暂浮盈，不保证零亏损。',
        '- 行业上限30%、单股上限20%，买入时实施；上涨导致收盘超行业额度时，次日开盘优先减最大持仓。被动超限可持续至次开盘。新股仓位按净值0.5%除2ATR及成本估算，但延迟退出可能超过该亏损预算。',
        '- B1使用KDJ(9,3,3)且J<12。带MA60过滤的要求close>MA60；大阳线close/open−1≥5%下一开盘卖初始份额1/3，三次清仓。第2/3天检查以入场日算第1个交易日，只检查一次；不盈利则次日开盘清仓。',
        '- 突破、45周超跌、322等信号和分批比例沿用此前研究定义，但全部叠加上述共同风险规则；不是未经修改的原作者交易体系。',
        '- Q代理只沿用突破/EP日线信号：为公平比较也使用2ATR仓位/收盘止损、行业限制与盈转亏退出；第3天收盘排队减半，次开盘执行，剩余跌破MA10次开盘退出。EP未核实催化，非盘中开盘区间原版。',
        '- 前3占正贡献：按每股票先汇总净利润，分母为所有净盈利股票的利润之和。剔除头部重跑是事后集中度诊断，不是样本外预测。',
        '- 当前自选池含明显赢家筛选及幸存者偏差；代码顺序会影响同时信号时有限现金的分配。所有策略同样排序，不按本期回报挑选入场。',
        '- 行业映射基于用户CSV所属行业、旧池映射及明示主题覆盖，非官方GICS。相似主题仍高度相关；不能把多个30%理解为总科技风险≤30%。',
        '- 历史价格使用AKShare/新浪前复权；校验OHLC、有限正价格、重复日期、末日时效。没有全面独立行情商复核、流动性/停牌/实际竞价滑点模拟。',
        '- GOOG映射为旧池GOOGL，避免同一发行人双计；SIVEF、MRAAY因OTC/身份与数据未验证而暂不纳入，不表示不是股票。',
        '- WOLF仅使用2025-09-29重组后的数据，不让已注销旧股历史进入指标；SECZ自实际数据首日2026-07-02开始，未满60根时不通过MA60过滤。',
        '- AZN在2026-02-02改为普通股上市：按官方2 ADR=1普通股，将此前OHLC×2、成交量÷2，统一到当前普通股单位；不把报价单位翻倍当作收益。原始CSV保留不改。',
        '- AZN单位转换来源：https://www.astrazeneca.com/media-centre/press-releases/2026/astrazeneca-to-complete-direct-listing-on-NYSE.html',
        '- WOLF重组来源：https://www.sec.gov/Archives/edgar/data/895419/000119312525223057/d69265d8k.htm',
        '- SECZ上市来源：https://investors.securitize.io/news/news-details/2026/Securitize-Completes-Business-Combination-with-Cantor-Equity-Partners-II/default.aspx',
        '', '## 未通过数据校验的候选','']
    lines += [f"- {s}：{r.get('error')}" for s,r in coverage.items() if r['status']!='available']
    lines+=['','## 合并池行业划分','']
    for group in sorted(set(sectors[s] for s in data)):
        lines.append(f"- {group}："+', '.join(s for s in sorted(data) if sectors[s]==group))
    lines+=['','## 合并池策略头部贡献及月度表现','']
    for key in ranked:
        r=results['merged'][key]
        lines.append(f"- {SPECS[key][0]}：前5 {' '.join(r['top_symbols'])}；平均仓位{r['mean_exposure']:.1%}；期末强制结算{r['end_of_test_positions']}笔。月收益："+'；'.join(f'{m} {v:.2%}' for m,v in r['monthly_returns'].items()))
    (ROOT/'REPORT.md').write_text('\n'.join(lines)+'\n')
    print('COUNTS',counts)
    print('\n'.join(lines[:21]))


if __name__=='__main__':
    run()
