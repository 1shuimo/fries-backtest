#!/usr/bin/env python3
"""Fill strategies/quant/monitor/names.json with company names for pool symbols that have none yet.
Yahoo shortName, looked up once per symbol and kept in the repo, so the daily email can say 'AI（C3.ai）'.
Usage: fetch_names.py [--pool strategies/friestrader/pool.txt] [--limit 60]"""
import argparse
import json
import os
import re
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAMES = os.path.join(REPO, 'strategies', 'quant', 'monitor', 'names.json')

SUFFIX = re.compile(r',?\s+(Inc\.?|Corporation|Corp\.?|Incorporated|Holdings?,? Inc\.?|Ltd\.?|Limited|plc|N\.V\.|S\.A\.|'
                    r'Co\.?|Company|Group,? Inc\.?|L\.P\.)$', re.I)


def tidy(name):
    name = ' '.join(str(name).split())
    for _ in range(2):
        name = SUFFIX.sub('', name).strip(' ,')
    return name


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pool', default=os.path.join(REPO, 'strategies', 'friestrader', 'pool.txt'))
    ap.add_argument('--limit', type=int, default=200, help='at most this many lookups per run')
    a = ap.parse_args()
    import yfinance as yf
    names = json.load(open(NAMES, encoding='utf-8')) if os.path.exists(NAMES) else {}
    syms = [s for s in dict.fromkeys(l.split('#')[0].strip().upper() for l in open(a.pool)) if s and s not in names]
    added = 0
    for s in syms[:a.limit]:
        try:
            info = yf.Ticker(s).get_info()
            n = info.get('shortName') or info.get('longName')
            if n:
                names[s] = tidy(n)
                added += 1
        except Exception as e:
            print(f'{s}: {type(e).__name__}: {e}'[:160])
        time.sleep(0.2)
    with open(NAMES, 'w', encoding='utf-8') as f:
        json.dump(dict(sorted(names.items())), f, ensure_ascii=False, indent=1)
        f.write('\n')
    print(f'names: {added} added, {len(names)} total, {len(syms) - added} still without a name')


if __name__ == '__main__':
    main()
