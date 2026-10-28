"""Korean underlying signals executed on next HK session; separate HKD accounts."""
import argparse
import json
import math
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date
from pathlib import Path

from chang_3221 import simulate, write_csv
from chang_research import indicators, make_signals
from fetch_data import write_rows
from qbacktest import load_bars

PAIRS = {'005930': '07747', '000660': '07709'}
CUTOFF = date(2026, 9, 15)
DATA = Path('data/korea_leveraged_20260915')
OUT = Path('results/korea_leveraged_2026')


def fetch():
    import requests
    DATA.mkdir(parents=True, exist_ok=True)
    for stock, product in PAIRS.items():
        response = requests.get('https://fchart.stock.naver.com/sise.nhn',
            params=dict(symbol=stock, timeframe='day', count=1000, requestType=0), timeout=45)
        response.raise_for_status()
        rows = []
        for item in ET.fromstring(response.content.decode('euc-kr')).iter('item'):
            day, *values = item.attrib['data'].split('|')
            day = f'{day[:4]}-{day[4:6]}-{day[6:]}'
            if '2024-01-01' <= day <= str(CUTOFF):
                rows.append(dict(date=day, symbol=stock, market='KR',
                    **dict(zip(('open','high','low','close','volume'), map(float, values)))))
        write_rows(rows, str(DATA/f'{stock}.csv'))
        response = requests.get('https://web.ifzq.gtimg.cn/appstock/app/fqkline/get',
            params={'param':f'hk{product},day,2024-01-01,{CUTOFF},1000,qfq'}, timeout=45)
        response.raise_for_status()
        payload = response.json()
        (DATA/f'{product}_tencent.json').write_text(json.dumps(payload))
        history = payload['data'][f'hk{product}']
        rows = [dict(date=r[0], symbol=product, market='HK',
            **dict(zip(('open','close','high','low','volume'), map(float,r[1:6]))))
            for r in history.get('qfqday',history.get('day',[])) if '2024-01-01' <= r[0] <= str(CUTOFF)]
        write_rows(rows, str(DATA/f'{product}.csv'))
        for symbol in (stock, product):
            bars = load_bars(str(DATA/f'{symbol}.csv'))[symbol]
            assert bars[-1].date == CUTOFF, (symbol, bars[-1].date)
            print(symbol, len(bars), bars[0].date, bars[-1].date, flush=True)


def map_signals(stocks, products, mode):
    _, audit = make_signals(stocks, mode)
    signals = defaultdict(list)
    mapped = []
    from chang_research import Candidate
    for row in audit:
        day = date.fromisoformat(row['signal_date'])
        if day.year != 2026:
            continue
        symbol = PAIRS[row['symbol']]
        bars = products[symbol]
        entry_i = next((i for i,b in enumerate(bars) if b.date > day), None)
        reason = 'no_next_hk_session'
        if entry_i is not None and entry_i > 0:
            prior = indicators(bars[:entry_i])[-1]
            reason = 'eligible' if math.isfinite(prior['atr']) and prior['atr'] > 0 else 'atr_warmup'
            if reason == 'eligible':
                signals[bars[entry_i].date].append(Candidate(symbol, row['strategy'], day, prior['atr']))
        mapped.append(dict(stock=row['symbol'], product=symbol, signal_date=str(day),
            entry_date=str(bars[entry_i].date) if entry_i is not None else '', reason=reason))
    return signals, mapped


def run():
    stocks = {s:load_bars(str(DATA/f'{s}.csv'))[s] for s in PAIRS}
    products = {s:load_bars(str(DATA/f'{s}.csv'))[s] for s in PAIRS.values()}
    OUT.mkdir(parents=True, exist_ok=True)
    results = {}
    for mode in ('trend', 'dip45', 'both'):
        signals, audit = map_signals(stocks, products, mode)
        for label, selected in [('pair', products), *[(s,{s:bs}) for s,bs in products.items()]]:
            subset = {d:[c for c in cs if c.symbol in selected] for d,cs in signals.items()}
            summary, trades, fills, curve, skipped = simulate(selected, subset)
            key = f'{label}_{mode}'
            results[key] = summary
            folder = OUT/key
            folder.mkdir(exist_ok=True)
            for name,rows in [('trades',trades),('fills',fills),('equity',curve),('skipped',skipped),('signals',audit)]:
                write_csv(folder/f'{name}.csv', rows)
    hold = {}
    for s,bs in products.items():
        year = [b for b in bs if b.date.year == 2026]
        peak = year[0].open
        dd = 0
        for b in year:
            peak = max(peak,b.close)
            dd = min(dd,b.close/peak-1)
        hold[s] = dict(start=str(year[0].date), end=str(year[-1].date),
            return_after_cost=year[-1].close/year[0].open*.999/1.001-1, close_drawdown=dd)
    (OUT/'summary.json').write_text(json.dumps(dict(results=results,hold=hold),indent=2))
    lines = ['# 韩国标的、香港杠杆产品回测', '', '截至2026-09-15；2026年内信号；各账户初始10万港元。', '',
        '| 产品/账户 | 策略 | 收益 | 最大回撤 | 交易数 |', '|---|---|---:|---:|---:|']
    for key,r in results.items():
        label,mode = key.split('_')
        lines.append(f"| {label} | {mode} | {r['total_return']:.2%} | {r['max_drawdown']:.2%} | {r['trades']} |")
    lines += ['', '## 买入持有对照', '']
    for s,r in hold.items():
        lines.append(f"- {s}: {r['start']}开盘至{r['end']}收盘，扣成本收益{r['return_after_cost']:.2%}，收盘最大回撤{r['close_drawdown']:.2%}。")
    lines += ['', '## 口径与限制', '',
        '- 07747对应韩国005930三星电子；07709对应韩国000660海力士。均为香港上市杠杆产品，收益使用实际产品日K而非正股乘2。',
        '- 正股：Naver公开历史日线；产品：腾讯港股前复权请求（若无复权字段则返回day）；保存原始JSON。新浪07747在2025-06-18收盘9.56高于最高9.55，已整条换源；腾讯当日最高为9.56。未经第二供应商全面复核，除权与分派口径仍需审计。',
        '- 韩国收盘信号最早在严格晚于信号日期的下一个香港交易日开盘执行；ATR取该次入场前最后一个香港交易日的数据。未用韩国当日收盘回填香港当日开盘。',
        '- 延用CHANG_3221_RULES.md研究近似：突破/45周线超跌入场；产品自身20%、30%、40%分批止盈；2ATR止损，单笔风险0.5%，单品资金上限20%，单边成本0.1%。',
        '- pair是两产品共享10万港元账户；单产品为各自独立账户，收益不能相加。未与美国37产品混算，避免币种、时区与现金先后顺序错误。',
        '- 整数份额模拟，未落实香港整手交易约束、真实点差和最低佣金；不保证实际可成交。无借款。样本末强制平仓。',
        '- 产品历史杠杆目标可能变化；实际市场价格包含跟踪偏差、溢折价等影响，不假设始终精确2倍。',
        '- 当前挑选标的存在选择偏差，单年样本不足以证明稳定盈利。',
        '- 身份核对：https://www.hkexnews.hk/listedco/listconews/sehk/2026/0609/2026060900966_c.pdf']
    (OUT/'REPORT.md').write_text('\n'.join(lines)+'\n')
    print('\n'.join(lines))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--fetch', action='store_true')
    args = parser.parse_args()
    if args.fetch:
        fetch()
    run()
