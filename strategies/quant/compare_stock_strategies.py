"""Fixed-rule US cash-stock comparison and ex-post concentration stress tests."""
import json
from collections import defaultdict
from dataclasses import asdict
from datetime import date
from pathlib import Path

from b1_research import signals_for
from chang_3221 import simulate, write_csv
from chang_research import make_signals
from leveraged_research import stocks, CUTOFF
from qbacktest import Config, run_backtest, summarize

ROOT = Path('results/us59_strategy_comparison_2026')
NAMES = {
    'q_breakout':'Q整理突破', 'q_ep':'Q跳空EP代理', 'q_both':'Q突破+EP',
    'trend_322':'均线突破+322', 'dip45_322':'45周超跌+322', 'both_322':'突破/超跌+322',
    'trend_control':'均线突破+均线退出', 'dip45_control':'45周超跌+均线退出', 'both_control':'突破/超跌+均线退出',
    'b1_atr':'J<12+大阳线+ATR止损', 'b1_ma60_atr':'J<12且>MA60+大阳线+ATR',
    'b1_ma60_d2':'J<12且>MA60+第2天检查', 'b1_ma60_d3':'J<12且>MA60+第3天检查',
}


def concentration(trades):
    pnl = defaultdict(float)
    for t in trades:
        pnl[t['symbol']] += t['pnl']
    ordered = sorted(pnl.items(),key=lambda x:(-x[1],x[0]))
    positive = [(s,v) for s,v in ordered if v>0]
    total_positive = sum(v for _,v in positive)
    net = sum(pnl.values())
    result = dict(traded_symbols=len(pnl),profitable_symbols=len(positive),net_pnl=net,
        positive_stock_pnl=total_positive,top_symbols=[s for s,_ in positive[:5]])
    for n in (1,3,5):
        top = sum(v for _,v in positive[:n])
        result[f'top{n}_positive_share'] = top/total_positive if total_positive else None
        result[f'top{n}_net_share'] = top/net if net>0 else None
        result[f'net_without_top{n}_fixed_trades'] = (net-top)/100000
    return result,[dict(symbol=s,pnl=v,positive_share=v/total_positive if v>0 and total_positive else 0) for s,v in ordered]


def evaluate(data,key,signals):
    if key.startswith('q_'):
        cfg = Config()
        trades,curve = run_backtest(data,key[2:],cfg,start_date=date(2026,1,1))
        result = summarize(trades,curve,cfg)
        # Include initial cash in drawdown, consistent with the other engine.
        peak,dd = 100000.,0.
        for r in curve:
            peak=max(peak,r['equity'])
            dd=min(dd,r['equity']/peak-1)
        result['max_drawdown']=dd
        return result,[asdict(t) for t in trades],[],curve,[]
    subset={d:[c for c in cs if c.symbol in data] for d,cs in signals[key].items()}
    kwargs={}
    if key.startswith('b1'):
        rule='body5'
        if key.endswith(('_d2','_d3')):
            kwargs=dict(profit_check_day=int(key[-1]),price_stop=False)
    else:
        rule=key.split('_')[-1]
    return simulate(data,subset,exit_rule=rule,**kwargs)


def run():
    data=stocks()
    assert len(data)==59 and all(bs[-1].date==CUTOFF for bs in data.values())
    signals={}
    for mode in ('trend','dip45','both'):
        sig,_=make_signals(data,mode)
        for rule in ('322','control'):
            signals[f'{mode}_{rule}']=sig
    plain,_=signals_for(data,data)
    filtered,_=signals_for(data,data,above_ma60=True)
    signals.update(b1_atr=plain,b1_ma60_atr=filtered,b1_ma60_d2=filtered,b1_ma60_d3=filtered)
    results={}
    all_contributions=[]
    for key,name in NAMES.items():
        summary,trades,fills,curve,skipped=evaluate(data,key,signals)
        conc,contrib=concentration(trades)
        all_contributions.extend(dict(strategy=key,**r) for r in contrib)
        summary.update(conc)
        for n in (1,3,5):
            exclude=set(conc['top_symbols'][:n])
            stress,*_=evaluate({s:bs for s,bs in data.items() if s not in exclude},key,signals)
            summary[f'exclude_top{n}_return']=stress['total_return']
            summary[f'exclude_top{n}_drawdown']=stress['max_drawdown']
        folder=ROOT/key
        folder.mkdir(parents=True,exist_ok=True)
        for label,rows in [('trades',trades),('fills',fills),('equity',curve),('skipped',skipped),('contributions',contrib)]:
            write_csv(folder/f'{label}.csv',rows)
        monthly={}
        previous=100000.
        ends={}
        for r in curve:
            ends[str(r['date'])[:7]]=r['equity']
        for month,equity in ends.items():
            monthly[month]=equity/previous-1
            previous=equity
        summary['monthly_returns']=monthly
        summary['positive_months']=sum(v>0 for v in monthly.values())
        results[key]=summary
        print(key,round(summary['total_return']*100,2),round(summary['max_drawdown']*100,2),conc['top_symbols'],flush=True)
    (ROOT/'summary.json').write_text(json.dumps(dict(cutoff=str(CUTOFF),universe=sorted(data),results=results),indent=2))
    write_csv(ROOT/'contributions.csv',all_contributions)
    flat=[dict(strategy=key,name=NAMES[key],**{k:v for k,v in r.items() if not isinstance(v,(dict,list))}) for key,r in results.items()]
    # Different engines expose different statistics; make the export schema explicit.
    columns=['strategy','name','total_return','max_drawdown','trades','win_rate','profitable_symbols','traded_symbols',
        'top1_positive_share','top3_positive_share','top5_positive_share','exclude_top1_return','exclude_top3_return','exclude_top5_return','positive_months']
    write_csv(ROOT/'comparison.csv',[{k:r.get(k) for k in columns} for r in flat])
    ranked=sorted(results,key=lambda k:results[k]['total_return'],reverse=True)
    lines=['# 59只美股正股：固定策略对比与盈利集中度', '',
        '区间：2026年初至2026-09-15。只买正股，不使用杠杆产品，不融资；初始10万美元。', '',
        '| 策略 | 收益 | 最大回撤 | 交易数 | 盈利股票/交易股票 | 前3盈利股占正贡献 | 剔除前3重跑收益 | 剔除前5重跑收益 |',
        '|---|---:|---:|---:|---:|---:|---:|---:|']
    for key in ranked:
        r=results[key]
        share=f"{r['top3_positive_share']:.1%}" if r['top3_positive_share'] is not None else '无盈利股票'
        lines.append(f"| {NAMES[key]} | {r['total_return']:.2%} | {r['max_drawdown']:.2%} | {r['trades']} | {r['profitable_symbols']}/{r['traded_symbols']} | {share} | {r['exclude_top3_return']:.2%} | {r['exclude_top5_return']:.2%} |")
    lines+=['','## B1第2天版本的额外诊断','',
        f"- 平均仓位{results['b1_ma60_d2']['mean_exposure']:.1%}；期末强制结算{results['b1_ma60_d2']['end_of_test_positions']}笔尚未自然退出的持仓。",
        '- 月收益：'+ '；'.join(f'{month} {value:.2%}' for month,value in results['b1_ma60_d2']['monthly_returns'].items()),
        '- 股票之间分散不等于跨时间、跨行业分散。当前表现仍主要来自4—5月；这一版本没有持续的价格止损。',
        '', '## 各策略头部贡献','']
    for key in ranked:
        r=results[key]
        leaders=sorted([c for c in all_contributions if c['strategy']==key],key=lambda c:-c['pnl'])[:5]
        lines.append(f"- {NAMES[key]}："+'；'.join(f"{c['symbol']} ${c['pnl']:,.0f}" for c in leaders))
    lines+=['','## 方法与限制','',
        '- 策略参数固定为此前版本，未网格调参。所有版本使用同一59股池与历史截止日，但各策略的具体止损/退出规则不同。完整逐笔、资金曲线、贡献表保存在子目录。',
        '- 买入信号收盘确认、次日开盘交易。统一单边0.1%成本、单票资金上限20%。ATR版本按2ATR与净值0.5%计算仓位；时间退出版本仅继承该仓位尺度，没有价格止损，0.5%不是最大亏损。',
        '- Q日线代理沿用原引擎：信号日低点/ADR止损，第3个交易日收盘减半并抬到成本，余仓跌破MA10次日开盘退出。定时减半按收盘价成交是理想化假设；不同于其他版本全部次日开盘减仓。EP未核实催化事件，不是完整原版复刻。',
        '- 均线突破：上穿MA60且高于MA120/250、成交量≥此前20日均量1.7倍。超跌：此前已完成周收盘低于45周均线且4日跌7.5%–19.75%。322为此前明确的研究近似，不是原作者完整体系。',
        '- 均线退出对照：突破持仓跌破MA60/60个交易日退出；超跌反弹到MA60/20个交易日退出；保留2ATR止损。',
        '- B1：J<12；过滤版本要求正股收盘>MA60；正股阳线实体≥5%次日卖初始份额1/3，三次清仓。时间退出入场日算第1天，第2/3天净盈亏≤0次日清仓；盈利则以后不再检查。',
        '- 正贡献分母=各股票先加总所有交易后的正净利润之和，不包含净亏损股票；不是赢家交易毛利润。另存前1/3/5占总净利润比例（可能超过100%）。盈利广度以有交易的股票为分母。',
        '- 剔除头部股票：根据完整样本的盈利排名，分别移除前1/3/5只股票后重新分配资金并回测；这是事后依赖性诊断，不能用作事前收益预测。额外保存固定交易直接扣除头部利润的结果。',
        '- 同时信号按策略/代码排序。没有行业中性、等风险行业权重或动态选股。盈利分散到多只股票也可能仍集中于科技/半导体风格。',
        '- 使用此前AKShare/新浪前复权缓存，未更新至今天，未全面完成第二供应商复核。当前指定59股存在选择/幸存者偏差，IPO标的仅上市后预热足够才能交易。',
        '- 年初空仓，不构造IPO前行情；样本末持仓强制结算计入交易。单年反复比较本身有选择偏差，不等于样本外有效，更不能证明稳定盈利。',
        '- 股票池：'+', '.join(sorted(data))]
    (ROOT/'REPORT.md').write_text('\n'.join(lines)+'\n')
    print('\n'.join(lines[:19]))


if __name__=='__main__':
    run()
