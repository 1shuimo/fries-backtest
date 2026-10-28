"""Actual daily 2x ETF bars with underlying-stock entry signals; no synthetic prices."""
import argparse
import json
import math
from collections import defaultdict
from dataclasses import replace
from datetime import date
from pathlib import Path

from chang_3221 import simulate, write_csv
from chang_research import indicators, make_signals
from qbacktest import load_bars

# One verified US daily-long-2x product per underlying. Selection is NOT by returns.
SOURCES = {
 'direxion':'https://www.direxion.com/single-stock-etfs',
 'granite':'https://graniteshares.com/etfs/leveraged/',
 'rex':'https://www.rexshares.com/t-rex-leveraged-etfs/',
 'tradr':'https://www.tradretfs.com/',
 'defiance':'https://www.defianceetfs.com/prospectuses/',
 'arm':'https://leverageshares.com/us/etfs/leverage-shares-2x-arm-long-etf',
 'crm':'https://leverageshares.com/us/etfs/leverage-shares-2x-long-crm-daily-etf/',
}
GROUPS = {
 'direxion':'AAPL:AAPU MSFT:MSFU GOOGL:GGLL AMZN:AMZU META:METU NVDA:NVDU TSLA:TSLL AVGO:AVL MU:MUU QCOM:QCMU AMD:AMUU INTC:LINT TXN:TXNU ASML:ASMU MRVL:MRVU ORCL:ORCU CSCO:CSCL PANW:PALU PLTR:PLTU ADBE:ADBU SPCX:LOFF',
 'granite':'CRWD:CRWL DELL:DLLL NOW:NOWL SMCI:SMCL TSM:TSMU SKHY:SKUU',
 'rex':'SNOW:SNOU RDW:RDWU SNDK:SNDU',
 'tradr':'AAOI:AAOX APP:APPX ASTS:ASTX CIEN:CIEX COHR:COHX LITE:LITX LRCX:LRCU NXPI:NXPX WDC:WDCX DDOG:DOGD NET:NETX',
 'defiance':'AMAT:AMA ANET:ANEL LUNR:LUNL RKLB:RKLX',
 'arm':'ARM:ARMG', 'crm':'CRM:CRMG',
}
MAP = {s:dict(etf=e,source=SOURCES[g]) for g,pairs in GROUPS.items() for s,e in (pair.split(':') for pair in pairs.split())}
MAP.update({
 'STX':dict(etf='STXX',source=SOURCES['tradr']),
 'FN':dict(etf='FNG',source='https://www.cboe.com/us/equities/listings/listed_products/issuer_detail/LPLV/'),
 'HPE':dict(etf='HPEL',source='https://www.cboe.com/us/equities/listings/listed_products/issuer_detail/LPLV/'),
 'ADI':dict(etf='ADIU',source='https://leverageshares.com/us/etfs/leverage-shares-2x-long-adi-daily-etf/'),
 'KLAC':dict(etf='KLAG',source='https://leverageshares.com/us/etfs/leverage-shares-2x-long-klac-daily-etf/'),
 'PL':dict(etf='PLU',source='https://www.defianceetfs.com/plu/'),
})
CUTOFF = date(2026,9,15)
FOLDER = Path('data/leveraged_20260915')
ROOT = Path('results/leveraged_2026')


def stocks():
    out = {}
    for folder in ('data/chang_20260915','data/chang_bigtech_20260915'):
        for path in Path(folder).glob('*.csv'):
            out.update(load_bars(str(path)))
    assert len(out)==59
    return {s:[b for b in bars if b.date<=CUTOFF] for s,bars in out.items()}


def fetch_one(item):
    import akshare as ak
    from fetch_data import write_rows
    s,meta = item
    path = FOLDER/f"{meta['etf']}.csv"
    try:
        if not path.exists():
            frame = ak.stock_us_daily(symbol=meta['etf'],adjust='qfq')
            ds = frame['date'].astype(str)
            frame = frame[(ds>='2024-01-01') & (ds<=str(CUTOFF))]
            rows=[dict(date=str(b['date'])[:10],symbol=meta['etf'],market='US',
                       **{k:float(b[k]) for k in ('open','high','low','close','volume')}) for _,b in frame.iterrows()]
            if not rows or not all(math.isfinite(r[k]) for r in rows for k in ('open','high','low','close','volume')):
                raise ValueError('empty or nonfinite')
            write_rows(rows,str(path))
        bars=load_bars(str(path))[meta['etf']]
        if bars[-1].date!=CUTOFF:
            raise ValueError(f'stale end {bars[-1].date}')
        result=dict(**meta,status='downloaded',rows=len(bars),first=str(bars[0].date),last=str(bars[-1].date))
    except Exception as exc:
        result=dict(**meta,status='data_failed',error=str(exc))
    print(s,result,flush=True)
    return s,result


def eligible_signals(stock_data, etfs, mode):
    raw,_=make_signals(stock_data,mode)
    stats={s:{b.date:r for b,r in zip(bs,indicators(bs))} for s,bs in etfs.items()}
    etf_signals, control_signals = defaultdict(list),defaultdict(list)
    audit=[]
    for day,cs in raw.items():
        if day.year!=2026:
            continue
        for c in cs:
            reason='eligible'
            if c.symbol not in etfs:
                reason='unavailable_product'
            elif day not in stats[c.symbol] or c.signal_date not in stats[c.symbol]:
                reason='no_product_bar'
            elif not math.isfinite(stats[c.symbol][c.signal_date]['atr']):
                reason='product_atr_warmup'
            if reason=='eligible':
                etf_signals[day].append(replace(c,atr=stats[c.symbol][c.signal_date]['atr']))
                control_signals[day].append(c)
            audit.append(dict(symbol=c.symbol,signal_date=str(c.signal_date),entry_date=str(day),reason=reason))
    return etf_signals,control_signals,audit


def run():
    source=stocks()
    status=json.loads((FOLDER/'coverage.json').read_text())
    etfs={}
    for s,r in status.items():
        if r['status']=='downloaded':
            bs=load_bars(str(FOLDER/f"{r['etf']}.csv"))[r['etf']]
            # Keep underlying keys for deterministic allocation and comparison.
            etfs[s]=[replace(b,symbol=s) for b in bs]
    ROOT.mkdir(parents=True,exist_ok=True)
    summaries={}
    per_stock=[]
    for mode in ('trend','dip45','both'):
        es,ss,audit=eligible_signals(source,etfs,mode)
        for name,data,signals in [('etf',etfs,es),('matched_stock',{s:source[s] for s in etfs},ss)]:
            summary,trades,fills,curve,skipped=simulate(data,signals)
            key=f'{mode}_{name}'
            summaries[key]=summary
            folder=ROOT/key
            folder.mkdir(exist_ok=True)
            for label,rows in [('trades',trades),('fills',fills),('equity',curve),('skipped',skipped),('signals',audit)]:
                write_csv(folder/f'{label}.csv',rows)
        if mode=='both':
            for s,bs in etfs.items():
                single={d:[c for c in cs if c.symbol==s] for d,cs in es.items()}
                summary,*_=simulate({s:bs},single)
                year=[b for b in bs if b.date.year==2026]
                matching={b.date:b for b in source[s]}
                first,last=year[0],year[-1]
                per_stock.append(dict(symbol=s,etf=MAP[s]['etf'],first=str(first.date),last=str(last.date),
                    etf_hold_return=last.close/first.open*.999/1.001-1,
                    stock_same_period_hold_return=matching[last.date].close/matching[first.date].open*.999/1.001-1,
                    strategy_account_return=summary['total_return'],strategy_trades=summary['trades'],
                    strategy_drawdown=summary['max_drawdown']))
    write_csv(ROOT/'per_stock.csv',per_stock)
    write_csv(ROOT/'coverage.csv',[dict(symbol=s,etf=r.get('etf',''),status=r['status'],
        first=r.get('first',''),last=r.get('last',''),error=r.get('error',''),source=r.get('source','')) for s,r in sorted(status.items())])
    (ROOT/'summary.json').write_text(json.dumps(dict(cutoff=str(CUTOFF),coverage=status,results=summaries),indent=2))
    lines=['# 实际单股2倍ETF回测（研究近似）','',f'原池59只；验证映射{len(MAP)}只；数据可用{len(etfs)}只。截止{CUTOFF}。',
           '', '| 信号/交易标的 | 收益 | 最大回撤 | 交易数 | 平均仓位 |','|---|---:|---:|---:|---:|']
    for k,r in summaries.items():
        lines.append(f"| {k} | {r['total_return']:.2%} | {r['max_drawdown']:.2%} | {r['trades']} | {r['mean_exposure']:.1%} |")
    lines+=['','完整口径见 LEVERAGED_RULES.md。正股信号，ETF自身止盈止损，风险预算相同但资金占用不相同；非正股收益乘2。',
            'per_stock.csv 的持有收益从各产品2026年第一个可用交易日开盘算起，年中上市者不是完整年初至今回报。独立策略账户收益按各自10万美元计算，不能直接相加。']
    lines+=['','## 逐产品结果','', '| 正股 | ETF | 持有起日 | ETF持有收益 | 同期正股持有收益 | 独立策略账户收益 | 策略交易数 |',
            '|---|---|---|---:|---:|---:|---:|']
    for r in sorted(per_stock,key=lambda r:r['symbol']):
        lines.append(f"| {r['symbol']} | {r['etf']} | {r['first']} | {r['etf_hold_return']:.2%} | {r['stock_same_period_hold_return']:.2%} | {r['strategy_account_return']:.2%} | {r['strategy_trades']} |")
    lines+=['','## 未纳入的标的','']
    for s,r in sorted(status.items()):
        if r['status']!='downloaded':
            lines.append(f"- {s} / {r.get('etf','未确认')}: {r.get('error',r['status'])}")
    (ROOT/'REPORT.md').write_text('\n'.join(lines)+'\n')
    print('\n'.join(lines))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--fetch',action='store_true')
    args=parser.parse_args()
    if args.fetch:
        FOLDER.mkdir(parents=True,exist_ok=True)
        # AKShare's embedded JavaScript decoder is not reliably thread-safe.
        status=dict(fetch_one(item) for item in MAP.items())
        for s in stocks():
            if s not in status:
                status[s]=dict(status='no_verified_mapping',note='Not verified in searched issuer lists; does not prove no product exists')
        (FOLDER/'coverage.json').write_text(json.dumps(status,indent=2))
    else:
        run()
