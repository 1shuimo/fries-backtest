"""Explicit daily-bar research approximation; not Chang's original system."""
import csv
import argparse
import json
import math
from collections import defaultdict
from datetime import date
from pathlib import Path

from chang_research import indicators, make_signals
from qbacktest import load_bars


def breakeven(position, fee):
    return (position['invested'] - position['net_sales']) / (position['shares'] * (1-fee))


def simulate(data, signals, exit_rule='322', risk=.005, start=date(2026, 1, 1),
             profit_check_day=None, price_stop=True):
    if exit_rule not in ('322', 'control', 'body5'):
        raise ValueError(f'Unknown exit rule: {exit_rule}')
    if profit_check_day is not None and (exit_rule != 'body5' or profit_check_day not in (2,3)):
        raise ValueError('Profit check requires body5 and day 2 or 3')
    fee = .001  # combined commission/slippage assumption, each side
    cash = 100000.
    positions, last, pending = {}, {}, {}
    fills, trades, curve, skipped = [], [], [], []
    days = defaultdict(dict)
    averages = {}
    for symbol, bars in data.items():
        averages[symbol] = {b.date:r['ma60'] for b,r in zip(bars, indicators(bars))}
        for b in bars:
            days[b.date][symbol] = b

    def sell(symbol, bar, qty, price, reason):
        nonlocal cash
        p = positions[symbol]
        qty = min(qty, p['shares'])
        net = qty * price * (1-fee)
        cash += net
        p['net_sales'] += net
        p['shares'] -= qty
        fills.append(dict(date=str(bar.date), symbol=symbol, side='sell', shares=qty,
                          price=price, fee=qty*price*fee, reason=reason))
        if not p['shares']:
            pnl = p['net_sales']-p['invested']
            trades.append(dict(symbol=symbol, strategy=p['strategy'], entry_date=p['entry_date'],
                               exit_date=str(bar.date), pnl=pnl, return_pct=pnl/p['invested'],
                               initial_shares=p['initial_shares'], reason=reason))
            del positions[symbol]
            pending.pop(symbol, None)

    for day in sorted(days):
        if day < start:
            continue
        today = days[day]
        exited_today = set()
        # Stops already known before today's open have priority over queued exits.
        for s in list(positions):
            if s not in today:
                continue
            p, b = positions[s], today[s]
            if price_stop and b.open <= p['stop']:
                sell(s,b,p['shares'],b.open,'gap_stop')
                exited_today.add(s)
            elif s in pending:
                stage = pending.pop(s)
                if stage == 'all':
                    sell(s,b,p['shares'],b.open,'ma60_exit')
                elif stage == 'time_loss':
                    sell(s,b,p['shares'],b.open,f'not_profitable_day_{profit_check_day}')
                    exited_today.add(s)
                else:
                    fractions = (1/3,2/3,1) if exit_rule == 'body5' else (.3,.5,.7)
                    target_sold = math.floor(p['initial_shares'] * fractions[stage-1] + 1e-9)
                    qty = target_sold - (p['initial_shares']-p['shares'])
                    if qty:
                        reason = f'body5_stage_{stage}' if exit_rule == 'body5' else f'take_profit_stage_{stage}'
                        sell(s,b,qty,b.open,reason)
                    p['stage'] = stage
                    if s not in positions:
                        exited_today.add(s)
        equity_open = cash + sum(p['shares']*(today[s].open if s in today else last[s]) for s,p in positions.items())
        for c in sorted(signals.get(day, []), key=lambda c:(c.strategy,c.symbol)):
            if c.signal_date < start:
                continue
            if exit_rule == 'body5' and c.symbol in exited_today:
                skipped.append(dict(date=str(day),symbol=c.symbol,reason='exited_today'))
                continue
            if c.symbol in positions:
                skipped.append(dict(date=str(day),symbol=c.symbol,reason='held'))
                continue
            b = today[c.symbol]
            distance = 2*c.atr
            qty = math.floor(min(equity_open*risk/(distance+b.open*2*fee), equity_open*.2/(b.open*(1+fee)), cash/(b.open*(1+fee))))
            if qty < 1 or distance >= b.open:
                skipped.append(dict(date=str(day),symbol=c.symbol,reason='cash_or_invalid_size'))
                continue
            invested = qty*b.open*(1+fee)
            cash -= invested
            positions[c.symbol] = dict(strategy=c.strategy,entry_date=str(day),entry=b.open,
                initial_shares=qty,shares=qty,invested=invested,net_sales=0.,stop=b.open-distance,
                stage=0,held=0)
            fills.append(dict(date=str(day),symbol=c.symbol,side='buy',shares=qty,price=b.open,
                              fee=qty*b.open*fee,reason=c.strategy))
        for s in list(positions):
            if s not in today:
                continue
            p,b = positions[s],today[s]
            if price_stop and b.low <= p['stop']:
                sell(s,b,p['shares'],min(b.open,p['stop']),'stop')
                continue
            p['held'] += 1
            if exit_rule == '322':
                # Closing-price confirmation, executed at next open: no perfect intraday fills.
                stage = sum(b.close >= p['entry']*(1+t) for t in (.2,.3,.4))
                if stage > p['stage']:
                    pending[s] = stage
                if p['stage'] == 3:
                    # Raised stop becomes active next session, never retroactively intraday.
                    p['stop'] = max(p['stop'],breakeven(p,fee))
            elif exit_rule == 'body5':
                net_pnl = p['net_sales']+p['shares']*b.close*(1-fee)-p['invested']
                if p['held'] == profit_check_day and net_pnl <= 0:
                    pending[s] = 'time_loss'
                elif b.close/b.open-1 >= .05-1e-12:
                    pending[s] = p['stage']+1
            else:
                avg = averages[s][day]
                if (p['strategy']=='trend' and b.close<avg) or (p['strategy']=='dip45' and b.close>=avg) or p['held'] >= (60 if p['strategy']=='trend' else 20):
                    pending[s] = 'all'
        last.update({s:b.close for s,b in today.items()})
        equity = cash+sum(p['shares']*last[s] for s,p in positions.items())
        assert cash >= -1e-6
        curve.append(dict(date=str(day),equity=equity,exposure=(equity-cash)/equity))
    final = max(d for d in days if d>=start)
    terminal_positions = len(positions)
    for s in list(positions):
        assert s in days[final], f'Missing terminal quote: {s}'
        sell(s,days[final][s],positions[s]['shares'],days[final][s].close,'end_of_test')
    curve[-1]['equity'] = cash
    peak, drawdown = 100000., 0.
    for r in curve:
        peak = max(peak,r['equity'])
        drawdown = min(drawdown,r['equity']/peak-1)
    profits = sum(max(t['pnl'],0) for t in trades)
    losses = -sum(min(t['pnl'],0) for t in trades)
    assert abs(sum(t['pnl'] for t in trades)-(cash-100000)) < 1e-6
    summary = dict(total_return=cash/100000-1,max_drawdown=drawdown,trades=len(trades),
        win_rate=sum(t['pnl']>0 for t in trades)/len(trades) if trades else 0,
        profit_factor=profits/losses if losses else None,
        mean_exposure=sum(r['exposure'] for r in curve)/len(curve),
        end_of_test_positions=terminal_positions,
        partial_sells=sum(f['reason'].startswith('take_profit') for f in fills),
        body_sells=sum(f['reason'].startswith('body5_') for f in fills),
        total_cost=sum(f['fee'] for f in fills))
    return summary,trades,fills,curve,skipped


def write_csv(path, rows):
    if not rows:
        return
    with path.open('w',newline='',encoding='utf-8-sig') as f:
        w = csv.DictWriter(f,fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--add-bigtech', action='store_true')
    args = parser.parse_args()
    data = {}
    for path in sorted(Path('data/chang_20260915').glob('*.csv')):
        data.update(load_bars(str(path)))
    assert len(data)==38
    if args.add_bigtech:
        from fetch_chang_data import BIGTECH_ADDITIONS
        for symbol in BIGTECH_ADDITIONS:
            assert symbol not in data, f'Duplicate symbol: {symbol}'
            data.update(load_bars(f'data/chang_bigtech_20260915/{symbol}.csv'))
        assert len(data)==38+len(BIGTECH_ADDITIONS)
    cutoff = min(bars[-1].date for bars in data.values())
    data = {s:[b for b in bars if b.date<=cutoff] for s,bars in data.items()}
    root = Path('results/chang_3221_2026')
    if args.add_bigtech:
        root = Path(f'results/chang_3221_bigtech_{len(data)}_2026')
    root.mkdir(parents=True,exist_ok=True)
    results = {}
    for mode in ('trend','dip45','both'):
        signals,_ = make_signals(data,mode)
        for rule in ('322','control'):
            result,trades,fills,curve,skipped = simulate(data,signals,rule)
            key = f'{mode}_{rule}'
            results[key] = result
            folder = root/key
            folder.mkdir(exist_ok=True)
            for name,rows in [('trades',trades),('fills',fills),('equity',curve),('skipped',skipped)]:
                write_csv(folder/f'{name}.csv',rows)
    coverage = {s:dict(first=str(b[0].date),last=str(b[-1].date),bars=len(b),
                ma45_ready=math.isfinite(indicators(b)[-1]['weekly_ma']),ma250_ready=len(b)>=250) for s,b in data.items()}
    (root/'summary.json').write_text(json.dumps(dict(start='2026-01-01',end=str(cutoff),
        universe=sorted(data),coverage=coverage,results=results),ensure_ascii=False,indent=2))
    lines = ['# 常士杉笔记与3221字幕：近似规则回测','',f'区间：2026-01-01 至 {cutoff}；{len(data)}只当前候选股。非原版复刻，非样本外验证。','',
        '| 买入/退出 | 收益 | 最大回撤 | 完整交易 | 胜率 | 平均仓位 | 分批卖出次数 |',
        '|---|---:|---:|---:|---:|---:|---:|']
    for key,r in results.items():
        lines.append(f"| {key} | {r['total_return']:.2%} | {r['max_drawdown']:.2%} | {r['trades']} | {r['win_rate']:.1%} | {r['mean_exposure']:.1%} | {r['partial_sells']} |")
    lines += ['', '规则、费用和局限见项目根目录 CHANG_3221_RULES.md。期末按收盘价扣费模拟平仓，胜率含这些期末平仓，不代表已自然结束的交易。',
              '', '最大回撤按日收盘账户净值计算，不包含盘中最深回撤。收益非年化。']
    (root/'REPORT.md').write_text('\n'.join(lines)+'\n')
    print('\n'.join(lines))


if __name__ == '__main__':
    main()
