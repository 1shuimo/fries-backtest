"""Cash-stock B1 with sector cap and explicit OHLC-path breakeven simulation."""
import json
import math
from collections import defaultdict
from datetime import date
from pathlib import Path

from b1_research import signals_for
from chang_3221 import write_csv
from compare_stock_strategies import concentration
from leveraged_research import stocks
from chang_research import indicators
from qbacktest import sma

GROUPS = {
    '半导体及设备':'NVDA AVGO QCOM AMD ARM MRVL INTC TXN ADI NXPI TSM ASML AMAT LRCX KLAC',
    '存储':'MU WDC STX SNDK SKHY',
    '光通信':'COHR LITE CIEN AAOI FN',
    '商业航天':'RKLB ASTS LUNR RDW PL BKSY SPCE SPCX',
    '软件云及安全':'MSFT CRM NOW ADBE ORCL PLTR CRWD DDOG NET SNOW APP PANW FTNT ZS TEAM INTU',
    '互联网平台':'AMZN GOOGL META',
    '硬件及网络设备':'AAPL DELL SMCI ANET CSCO HPE',
    '汽车':'TSLA',
}
SECTOR={s:g for g,ss in GROUPS.items() for s in ss.split()}
ROOT=Path('results/b1_sector30_breakeven_2026')
FEE=.001


def walk_bar(p,b,path):
    """Mutate armed stop; return fill/reason under one explicit intraday path."""
    be=(p['cost']-p['realized'])/(p['qty']*(1-FEE))
    if b.open <= p['stop']:
        return b.open, 'gap_stop'
    points=[b.open,b.high,b.low,b.close] if path=='high_first' else [b.open,b.low,b.high,b.close]
    if b.open > be:
        p['armed']=True
        p['stop']=max(p['stop'],be)
    for a,z in zip(points,points[1:]):
        if z < a and z <= p['stop'] <= a:
            return p['stop'], 'breakeven_stop' if p['armed'] else 'initial_atr_stop'
        if z > be:
            p['armed']=True
            p['stop']=max(p['stop'],be)
    return None


def simulate(data,signals,check_day=2,path='high_first',sector_map=None,exit_rule='body5',arm_pct=0.0):
    """arm_pct: close_only breakeven exit arms only once net trade P&L exceeds arm_pct x cost (0 = original rule)."""
    assert path in ('high_first','low_first','close_only')
    assert exit_rule in ('body5','322','control','q_ma10')
    SECTOR = sector_map if sector_map is not None else globals()['SECTOR']
    groups=sorted(set(SECTOR.values()))
    stats={s:{b.date:(r['ma60'],sma(bs,i,10)) for i,(b,r) in enumerate(zip(bs,indicators(bs)))} for s,bs in data.items()} if exit_rule in ('control','q_ma10') else {}
    cash=100000.
    pos,last,pending={},{},{}
    rebalance=set()
    fills,trades,curve,exposures,skips=[],[],[],[],[]
    days=defaultdict(dict)
    ambiguity=0
    for s,bs in data.items():
        for b in bs:
            if b.date>=date(2026,1,1):
                days[b.date][s]=b

    def sell(s,b,qty,price,reason):
        nonlocal cash
        p=pos[s]
        qty=min(qty,p['qty'])
        cash+=qty*price*(1-FEE)
        p['realized']+=qty*price*(1-FEE)
        p['qty']-=qty
        fills.append(dict(date=str(b.date),symbol=s,side='sell',qty=qty,price=price,reason=reason))
        if not p['qty']:
            pnl=p['realized']-p['cost']
            if abs(pnl)<1e-8:
                pnl=0.  # Numerical roundoff is not a profitable stock.
            trades.append(dict(symbol=s,sector=SECTOR[s],entry_date=p['entry_date'],exit_date=str(b.date),
                pnl=pnl,reason=reason))
            del pos[s]
            pending.pop(s,None)

    for day,today in sorted(days.items()):
        exited=set()
        def price(s):
            return today[s].open if s in today else last[s]
        def equity():
            return cash+sum(p['qty']*price(s) for s,p in pos.items())
        def value(group):
            return sum(p['qty']*price(s) for s,p in pos.items() if SECTOR[s]==group)
        for s in list(pos):
            if s not in today:
                continue
            p,b=pos[s],today[s]
            if path!='close_only' and b.open<=p['stop']:
                sell(s,b,p['qty'],b.open,'gap_stop')
            elif s in pending:
                action=pending.pop(s)
                if action=='time':
                    sell(s,b,p['qty'],b.open,f'day{check_day}_not_profitable')
                elif isinstance(action,str):
                    sell(s,b,p['qty'],b.open,action)
                else:
                    fraction=(.3,.5,.7)[action-1] if exit_rule=='322' else .5 if exit_rule=='q_ma10' else action/3
                    target=math.floor(p['initial']*fraction+1e-9)
                    qty=max(0,target-(p['initial']-p['qty']))
                    if qty:
                        sell(s,b,qty,b.open,f'{exit_rule}_stage{action}')
                    p['stage']=action
            if s not in pos:
                exited.add(s)
        # Only previously observed close breaches schedule forced sales.
        for group in sorted(rebalance):
            for s in sorted([s for s in pos if SECTOR[s]==group and s in today],key=lambda s:(-pos[s]['qty']*price(s),s)):
                excess=value(group)-.30*equity()
                if excess<=1e-8:
                    break
                qty=math.ceil(excess/(price(s)*(1-.30*FEE)))
                sell(s,today[s],qty,price(s),'sector30_trim')
                if s not in pos:
                    exited.add(s)
        for c in sorted(signals.get(day,[]),key=lambda c:c.symbol):
            s=c.symbol
            if s in pos or s in exited or s not in today:
                continue
            b=today[s]
            eq=equity()
            room=max(0,(.30*eq-value(SECTOR[s]))/(1+.30*FEE))
            distance=2*c.atr
            qty=math.floor(min(eq*.005/(distance+2*b.open*FEE),eq*.20/(b.open*(1+FEE)),cash/(b.open*(1+FEE)),room/b.open))
            if qty<1 or distance>=b.open:
                skips.append(dict(date=str(day),symbol=s,reason='cash_sector_or_size'))
                continue
            cost=qty*b.open*(1+FEE)
            cash-=cost
            pos[s]=dict(qty=qty,initial=qty,cost=cost,realized=0.,stop=b.open-distance,armed=False,
                stage=0,held=0,entry=b.open,strategy=c.strategy,entry_date=str(day))
            fills.append(dict(date=str(day),symbol=s,side='buy',qty=qty,price=b.open,reason=c.strategy))
            assert value(SECTOR[s])<=.30*equity()+1e-6
        for s in list(pos):
            if s not in today:
                continue
            p,b=pos[s],today[s]
            be=(p['cost']-p['realized'])/(p['qty']*(1-FEE))
            if path!='close_only' and not p['armed'] and b.open<=be<b.high and b.low<=be:
                ambiguity+=1
            hit=walk_bar(p,b,path) if path!='close_only' else None
            if hit:
                sell(s,b,p['qty'],hit[0],hit[1])
                continue
            p['held']+=1
            net=p['realized']+p['qty']*b.close*(1-FEE)-p['cost']
            if path=='close_only' and b.close<=p['stop']:
                pending[s]='close_atr_stop'
            elif path=='close_only' and p['armed'] and net<=0:
                pending[s]='close_breakeven_exit'
            elif p['held']==check_day and net<=0:
                pending[s]='time'
            elif exit_rule=='body5' and b.close/b.open-1>=.05-1e-12:
                pending[s]=p['stage']+1
            elif exit_rule=='322':
                stage=sum(b.close>=p['entry']*(1+t) for t in (.2,.3,.4))
                if stage>p['stage']:
                    pending[s]=stage
            elif exit_rule=='control':
                avg=stats[s][day][0]
                if (p['strategy']=='trend' and b.close<avg) or (p['strategy']=='dip45' and b.close>=avg) or p['held'] >= (60 if p['strategy']=='trend' else 20):
                    pending[s]='ma60_or_time_exit'
            elif exit_rule=='q_ma10':
                if b.close<stats[s][day][1]:
                    pending[s]='ma10_exit'
                elif p['held']>=3 and p['stage']==0:
                    pending[s]=1
            if path=='close_only' and net>arm_pct*p['cost']:
                p['armed']=True
        last.update({s:b.close for s,b in today.items()})
        eq=cash+sum(p['qty']*last[s] for s,p in pos.items())
        by_group=defaultdict(float)
        for s,p in pos.items():
            by_group[SECTOR[s]]+=p['qty']*last[s]
        rebalance={g for g,v in by_group.items() if v>.30*eq+1e-8}
        curve.append(dict(date=str(day),equity=eq,exposure=(eq-cash)/eq))
        exposures.extend(dict(date=str(day),sector=g,weight=by_group[g]/eq,trim_next_open=g in rebalance) for g in groups)
        assert cash>=-1e-6
    final=max(days)
    terminal=len(pos)
    for s in list(pos):
        sell(s,days[final][s],pos[s]['qty'],days[final][s].close,'end_of_test')
    curve[-1]['equity']=cash
    peak,dd=100000.,0.
    for r in curve:
        peak=max(peak,r['equity'])
        dd=min(dd,r['equity']/peak-1)
    assert abs(sum(t['pnl'] for t in trades)-(cash-100000))<1e-6
    conc,contrib=concentration(trades)
    summary=dict(total_return=cash/100000-1,max_drawdown=dd,trades=len(trades),
        win_rate=sum(t['pnl']>0 for t in trades)/len(trades) if trades else 0.,
        mean_exposure=sum(r['exposure'] for r in curve)/len(curve),ambiguous_bars=ambiguity,
        breakeven_exits=sum(t['reason'] in ('breakeven_stop','close_breakeven_exit') for t in trades),gap_exits=sum(t['reason']=='gap_stop' for t in trades),
        sector_trims=sum(f['reason']=='sector30_trim' for f in fills),end_of_test_positions=terminal,
        max_close_sector_weight=max(r['weight'] for r in exposures),**conc)
    return summary,trades,fills,curve,exposures,contrib,skips


def run():
    data=stocks()
    assert set(data)==set(SECTOR)
    assert len(SECTOR)==sum(len(ss.split()) for ss in GROUPS.values())
    signals,audit=signals_for(data,data,above_ma60=True)
    results={}
    for day in (2,3):
        for path in ('high_first','low_first'):
            key=f'D{day}_{path}'
            r,trades,fills,curve,exposure,contrib,skips=simulate(data,signals,day,path)
            excluded=set(r['top_symbols'])
            stress,*_=simulate({s:bs for s,bs in data.items() if s not in excluded},signals,day,path)
            r['exclude_top5_return']=stress['total_return']
            results[key]=r
            folder=ROOT/key
            folder.mkdir(parents=True,exist_ok=True)
            for name,rows in [('trades',trades),('fills',fills),('equity',curve),('sector_weights',exposure),('contributions',contrib),('skipped',skips),('signals',audit)]:
                write_csv(folder/f'{name}.csv',rows)
            print(key,json.dumps(r),flush=True)
    (ROOT/'summary.json').write_text(json.dumps(results,indent=2))
    lines=['# 行业30%＋持续价格止损＋盈转亏退出', '', '59只美股正股；2026年初至9月15日；初始10万美元；单边0.1%成本。', '',
        '| 版本 | 收益 | 最大回撤 | 完整交易数 | 保本止损数 | 剔除盈利前5股重跑 |', '|---|---:|---:|---:|---:|---:|']
    for key,r in results.items():
        lines.append(f"| {key} | {r['total_return']:.2%} | {r['max_drawdown']:.2%} | {r['trades']} | {r['breakeven_exits']} | {r['exclude_top5_return']:.2%} |")
    lines+=['','## 规则与关键限制','',
        '- 买入正股J<12且close>MA60；次日开盘；单票上限20%，单笔初始2ATR20风险预算0.5%；入场第2/3天不盈利次日退出；大阳线实体≥5%次日卖初始份额1/3。',
        '- 初始2ATR价格止损始终有效；盘中扣成本后的交易总盈亏转正即启用保本止损。净盈亏含已减仓回款，保本价=(初始含费成本−已实现净回款)/(剩余股数×0.999)，止损只上移、不下移。首次轻微盈利即启用，没有额外缓冲。',
        '- 跳空跌穿已有止损直接按开盘价减成本卖出，不能保证零亏损。正常触价按止损价减成本模拟；未建模停牌、盘口深度和极端滑点。',
        '- high_first是假设日内开盘→最高→最低→收盘；low_first是假设开盘→最低→最高→收盘。两者仅为路径敏感性，不是实际盘中回放或收益上下界；真实多次往返无法由日K还原。',
        '- 每行业持仓市值/实时模拟净值上限30%，买入时落实。持仓上涨可能被动超标，收盘发现后下个开盘按最大持仓优先减至30%；不是盘中连续硬上限。每日行业权重与超限记录已保存。',
        '- 行业减仓算作已卖份额：随后大阳线目标仍是累计卖初始份额1/3、2/3、全部，不会重复多卖。',
        '- 各股票日内路径独立模拟，不能跨股票还原同步价位；所有新买入在开盘完成，不能使用当日稍后的卖出现金反向资助当日开盘。',
        '- 分类为本研究互斥主题分类，不是官方GICS。NVDA归半导体、MSFT归软件；存储和光通信与半导体仍有相关性，多个模块30%不代表总体科技风险低。',
        '- 剔除前5为事后集中度压力测试，不是事前选择依据。数据缓存/复权/当前股票池选择偏差/期末强制平仓等限制同上一版。',
        '', '## 行业映射','']
    lines += [f'- {g}：{ss}' for g,ss in GROUPS.items()]
    (ROOT/'REPORT.md').write_text('\n'.join(lines)+'\n')


if __name__=='__main__':
    run()
