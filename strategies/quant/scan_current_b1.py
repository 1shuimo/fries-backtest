"""Refresh completed daily bars and screen the existing 180-stock universe."""
import json
import math
from concurrent.futures import ProcessPoolExecutor,as_completed
from datetime import date
from pathlib import Path

from b1_research import kdj
from chang_research import indicators
from qbacktest import load_bars
from watchlist_backtest import normalize_azn

ASOF=date(2026,10,27)
ROOT=Path('data/scan_20261027')


def fetch(symbol):
    import akshare as ak
    from fetch_data import write_rows
    path=ROOT/f'{symbol}.csv'
    try:
        if not path.exists():
            frame=ak.stock_us_daily(symbol=symbol,adjust='qfq')
            rows=[dict(date=str(r['date'])[:10],symbol=symbol,market='US',
                **{k:float(r[k]) for k in ('open','high','low','close','volume')})
                for _,r in frame.iterrows() if '2026-01-01'<=str(r['date'])[:10]<=str(ASOF)]
            if not rows:
                raise ValueError('empty data')
            write_rows(rows,str(path))
        bars=load_bars(str(path))[symbol]
        if bars[-1].date!=ASOF:
            raise ValueError(f'stale: {bars[-1].date}')
        if symbol=='AZN':
            bars=normalize_azn(bars)
        if len(bars)<60:
            raise ValueError(f'insufficient MA60 history: {len(bars)}')
        j=kdj(bars)
        stats=indicators(bars)
        r=stats[-1]
        if not all(math.isfinite(v) for v in (j[-1],r['ma60'],r['atr'])):
            raise ValueError('nonfinite indicator')
        qualifying=j[-1]<12 and bars[-1].close>r['ma60']
        prior_qualifying=j[-2]<12 and bars[-2].close>stats[-2]['ma60']
        return dict(symbol=symbol,status='ok',date=str(ASOF),close=bars[-1].close,
            j=j[-1],ma60=r['ma60'],above_ma60=bars[-1].close/r['ma60']-1,atr20=r['atr'],
            qualifying=qualifying,new_signal=qualifying and not prior_qualifying,rows=len(bars))
    except Exception as exc:
        return dict(symbol=symbol,status='failed',error=str(exc))


def run():
    manifest=json.loads(Path('data/watchlist_20260917/universe.json').read_text())
    old_coverage=json.loads(Path('data/watchlist_20260917/coverage.json').read_text())
    universe=sorted(s for s,r in old_coverage.items() if r['status']=='available')
    assert len(universe)==180
    ROOT.mkdir(parents=True,exist_ok=True)
    rows=[]
    with ProcessPoolExecutor(max_workers=4) as pool:
        futures=[pool.submit(fetch,s) for s in universe]
        for future in as_completed(futures):
            r=future.result()
            r['sector']=manifest['sectors'][r['symbol']]
            rows.append(r)
            if r['status']=='failed' or r.get('qualifying') or len(rows)%20==0:
                print(len(rows),json.dumps(r,ensure_ascii=False),flush=True)
            (ROOT/'scan.json').write_text(json.dumps(sorted(rows,key=lambda r:r['symbol']),ensure_ascii=False,indent=2))
    candidates=sorted((r for r in rows if r.get('qualifying')),key=lambda r:(r['sector'],r['symbol']))
    print('CANDIDATES',json.dumps(candidates,ensure_ascii=False,indent=2))
    print('COVERAGE',len(rows),sum(r['status']=='ok' for r in rows))


if __name__=='__main__':
    run()
