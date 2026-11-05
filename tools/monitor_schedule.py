"""Resolve the last closed US session without treating a new midnight as a close.

Full-day holidays remain the monitor's responsibility. Automatic delivery is
limited to after 16:00 ET or before 09:30 ET to avoid stale opening instructions.
"""
import argparse
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

NY = ZoneInfo('America/New_York')


def resolve(now, asof=''):
    local = now.astimezone(NY)
    closed = local.date() if local.time() >= time(16) else local.date() - timedelta(days=1)
    if asof:
        target = date.fromisoformat(asof)
        if str(target) != asof or target > closed:
            raise ValueError('asof must be YYYY-MM-DD and no later than the last closed session date')
        return target, ''
    if time(9, 30) <= local.time() < time(16):
        return closed, 'outside delivery window: the next market open has passed'
    return closed, ''


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--asof', default='')
    ap.add_argument('--output', required=True)
    ap.add_argument('--check-date', help='fail if the automatic delivery window expired or target changed')
    args = ap.parse_args()
    target, reason = resolve(datetime.now(NY), args.asof)
    if args.check_date and (reason or str(target) != args.check_date):
        ap.error(reason or 'target session changed while this run was working')
    with open(args.output, 'a') as f:
        f.write(f'date={target}\nskip={str(bool(reason)).lower()}\nreason={reason}\n')
    print(f'New York target session: {target}; {reason or "eligible"}')


if __name__ == '__main__':
    main()
