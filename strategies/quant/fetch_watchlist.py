"""Fetch only added US stocks, preserving prior validated snapshots."""
import json
from pathlib import Path
import akshare as ak
from import_watchlist import ROOT
from leveraged_research import stocks,CUTOFF
from fetch_data import write_rows
from qbacktest import load_bars


def main():
    universe=json.loads((ROOT/'universe.json').read_text())
    old=stocks()
    status={s:dict(status='available',source='previous_sina_cache',rows=len(bs)) for s,bs in old.items()}
    for s in universe['merged_symbols']:
        if s in old:
            continue
        path=ROOT/f'{s}.csv'
        try:
            if not path.exists():
                frame=ak.stock_us_daily(symbol=s,adjust='qfq')
                rows=[dict(date=str(r['date'])[:10],symbol=s,market='US',
                    **{k:float(r[k]) for k in ('open','high','low','close','volume')})
                    for _,r in frame.iterrows() if '2024-01-01'<=str(r['date'])[:10]<=str(CUTOFF)]
                if not rows:
                    raise ValueError('empty history')
                write_rows(rows,str(path))
            bs=load_bars(str(path))[s]
            if bs[-1].date!=CUTOFF:
                raise ValueError(f'stale end {bs[-1].date}')
            status[s]=dict(status='available',source='Sina AKShare qfq',rows=len(bs),first=str(bs[0].date),last=str(bs[-1].date))
        except Exception as exc:
            status[s]=dict(status='failed',error=str(exc))
        print(s,status[s],flush=True)
        (ROOT/'coverage.json').write_text(json.dumps(status,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
