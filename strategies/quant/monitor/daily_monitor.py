"""Daily action list for B1+MA60+day-3 check (the best 2026 YTD variant in docs/RESULTS.md),
with the user's stop: previous swing low if within 10% of entry, else the buy day's low (intraday stop order).

Mirrors sector_b1.simulate(path='close_only', check_day=3, exit_rule='body5', stop_info=...):
  buy   : close J(9,3,3) < 12 and close > SMA60 -> buy next open; not already held.
  size  : min(equity*0.5% / stop distance, equity*20%, cash, sector room to 30%).
  stop  : intraday price stop, fixed; gap below it fills at the open.
  exits : decided on the close, executed next open, in this order:
          once net P&L > 0 at a close, net <= 0 -> sell all (breakeven)
          day 3 (entry day = 1) net <= 0        -> sell all
          close/open-1 >= 5%                    -> sell 1/3 of initial (cumulative 1/3, 2/3, all)
Read-only. Prints a Markdown report; never places orders.
"""
import argparse
import csv
import json
import math
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from b1_research import kdj  # noqa: E402
from chang_research import indicators  # noqa: E402
from import_watchlist import OVERRIDES  # noqa: E402
from qbacktest import Bar  # noqa: E402
from sector_b1 import SECTOR as KNOWN_SECTORS  # noqa: E402

REPO = HERE.parents[2]
POOL = REPO / 'strategies' / 'friestrader' / 'pool.txt'
FEE = .001
RISK, MAX_POS, SECTOR_CAP, CHECK_DAY, BODY = .005, .20, .30, 3, .05
NON_STOCK_SECTIONS = ('ETF',)
SECTION_CN = {
    'Semiconductors & equipment': '半导体及设备', 'Storage / hardware / networking / optical': '硬件及网络设备',
    'AI infra / data centers / software': '软件云及安全', 'Mega caps / internet': '互联网平台',
    'Fintech / financials': '金融', 'Space / defense / nuclear / energy': '能源及电力',
    'Metals & mining': '材料及矿业', 'Healthcare': '医疗健康', 'Consumer / industrial / other': '工业及服务',
}


def load_pool(path=POOL):
    """Symbols with their pool.txt section header as sector; ETF sections and indices dropped."""
    sectors, section = {}, '其他'
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line.startswith('#'):
            head = line.lstrip('#').strip()
            if head and not head.startswith(('Pool', 'Excluded', 'Stocks/ETFs')) and ':' not in head:
                section = head
            continue
        s = line.split('#')[0].strip().upper()
        if s and not s.startswith('.') and not any(t in section for t in NON_STOCK_SECTIONS):
            # Same mutually exclusive sectors as the backtest where known, else the pool.txt group.
            sectors[s] = KNOWN_SECTORS.get(s) or OVERRIDES.get(s) or SECTION_CN.get(section, section)
    return sectors


MAX_STOP_PCT, PIVOT_K, PIVOT_LOOKBACK = .10, 3, 60
FRESH_MIN = .90  # share of the pool that must have today's bar before a report is trusted
NYSE_HOLIDAYS = {  # full-day closures
    '2026-01-01', '2026-01-19', '2026-02-16', '2026-04-03', '2026-05-25', '2026-06-19', '2026-07-03',
    '2026-09-07', '2026-11-26', '2026-12-25',
    '2027-01-01', '2027-01-18', '2027-02-15', '2027-03-26', '2027-05-31', '2027-06-18', '2027-07-05',
    '2027-09-06', '2027-11-25', '2027-12-24',
}


def swing_low(bars, i, k=PIVOT_K, lookback=PIVOT_LOOKBACK):
    """Previous swing low of the uptrend as seen on signal bar i: the most recent confirmed pivot low
    (lowest low of the k bars either side, all on or before bar i) that lies below bar i's low."""
    for p in range(i - k, max(k, i - lookback) - 1, -1):
        lo = bars[p].low
        if lo < bars[i].low and lo <= min(x.low for x in bars[p - k:p + k + 1]):
            return lo
    return None


def pick_stop(entry, swing):
    """Swing low if it is below entry and within 10%; otherwise None = use the buy day's low."""
    return swing if swing is not None and 0 < entry - swing <= MAX_STOP_PCT * entry else None


def load_bars(path, symbol, asof=None):
    bars = []
    with open(path, newline='') as f:
        for r in csv.DictReader(f):
            d = date.fromisoformat(r['Date'][:10])
            if asof and d > asof:
                continue
            try:
                o, h, l, c, v = (float(r[k]) for k in ('Open', 'High', 'Low', 'Close', 'Volume'))
            except ValueError:
                continue
            if min(o, h, l, c) <= 0:
                continue
            bars.append(Bar(d, symbol, 'US', o, max(h, o, c), min(l, o, c), c, v))
    return bars


@dataclass
class Position:
    symbol: str
    entry_date: date
    entry_price: float
    initial: int
    qty: int
    sold_proceeds: float | None = None
    # filled by replay
    stop_override: float | None = None
    stop: float = math.nan
    stop_kind: str = ''
    swing: float | None = None
    atr: float = math.nan
    held: int = 0
    armed: bool = False
    stage: int = 0
    realized: float = 0.
    net: float = 0.
    close: float = math.nan
    action: str = ''
    sell_qty: int = 0
    notes: list = field(default_factory=list)
    sim_fills: list = field(default_factory=list)

    @property
    def cost(self):
        return self.initial * self.entry_price * (1 + FEE)


def read_positions(path):
    out = []
    if not Path(path).exists():
        return out
    with open(path, newline='', encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            if not (r.get('symbol') or '').strip():
                continue
            initial = int(float(r['initial_qty']))
            qty = int(float(r.get('qty') or initial))
            sp = (r.get('sold_proceeds') or '').strip()
            st = (r.get('stop') or '').strip()
            out.append(Position(r['symbol'].strip().upper(), date.fromisoformat(r['entry_date'].strip()),
                                float(r['entry_price']), initial, qty, float(sp) if sp else None,
                                float(st) if st else None))
    return out


def close_decision(p, bar, net):
    """Same if/elif order as sector_b1.simulate close_only (price stop is intraday, handled separately)."""
    if p.armed and net <= 0:
        return 'breakeven'
    if p.held == CHECK_DAY and net <= 0:
        return 'day3'
    if bar.close / bar.open - 1 >= BODY - 1e-12:
        return p.stage + 1
    return None


def replay(p, bars):
    """Replay the rules from entry to the last bar (rule sales at the next open, price stop intraday),
    mirroring sector_b1.simulate(stop_info=...). Then reconcile with the user's actual qty / proceeds."""
    dates = [b.date for b in bars]
    if p.entry_date not in dates:
        p.notes.append(f'没有 {p.entry_date} 的日线，无法复盘，请检查买入日期')
        p.action = 'check'
        return p
    i0 = dates.index(p.entry_date)
    if i0 <= PIVOT_K:
        p.notes.append('买入前历史不足，无法找前低')
        p.action = 'check'
        return p
    p.atr = indicators(bars)[i0 - 1]['atr']
    if p.stop_override is not None:
        p.stop, p.stop_kind = p.stop_override, '手动设定'
    else:
        p.swing = swing_low(bars, i0 - 1)
        chosen = pick_stop(p.entry_price, p.swing)
        if chosen is not None:
            p.stop, p.stop_kind = chosen, '前低'
        else:
            p.stop, p.stop_kind = bars[i0].low, '买入日最低价'
    stop_live = p.stop_kind != '买入日最低价'   # day-low stop only exists after the entry day closes
    sim_qty, pending, exited = p.initial, None, None

    def sell(bar, q, price, why):
        nonlocal sim_qty, exited
        p.realized += q * price * (1 - FEE)
        sim_qty -= q
        if q:
            p.sim_fills.append((bar.date, q))
        if not sim_qty:
            exited = (bar.date, why)

    for bar in bars[i0:]:
        if bar.date > p.entry_date:
            if bar.open <= p.stop:
                sell(bar, sim_qty, bar.open, 'stop')
                break
            if pending is not None:
                if isinstance(pending, int):
                    target = math.floor(p.initial * pending / 3 + 1e-9)
                    q = max(0, min(sim_qty, target - (p.initial - sim_qty)))
                    p.stage = pending
                else:
                    q = sim_qty
                sell(bar, q, bar.open, pending)
                if exited:
                    break
                pending = None
        if stop_live and bar.low <= p.stop:
            sell(bar, sim_qty, min(bar.open, p.stop), 'stop')
            break
        stop_live = True
        p.held += 1
        net = p.realized + sim_qty * bar.close * (1 - FEE) - p.cost
        pending = close_decision(p, bar, net)
        if net > 0:
            p.armed = True
    last = bars[-1]
    p.close = last.close
    p.held = len(bars) - i0
    # reconcile with actual holdings
    actual_sold = p.initial - p.qty
    if p.sold_proceeds is not None:
        p.realized = p.sold_proceeds * (1 - FEE)
    else:
        sim_sold = p.initial - sim_qty
        p.realized += (actual_sold - sim_sold) * last.close * (1 - FEE)
    p.stage = max(p.stage if not exited else 0, min(3, round(actual_sold / p.initial * 3)))
    p.net = p.realized + p.qty * last.close * (1 - FEE) - p.cost
    if exited and p.qty:
        if exited[1] == 'stop':
            p.notes.append(f'{exited[0]} 盘中已跌到止损价 {p.stop:.2f}——如果止损单已成交请更新 positions.csv；'
                           f'如果没设止损单、仍持有 {p.qty} 股，按规则应立即卖出')
        else:
            reason = {'breakeven': '盈转亏', 'day3': '第3天未盈利'}.get(exited[1], '分批卖完')
            p.notes.append(f'按规则 {exited[0]} 开盘已应清仓（{reason}），你还持有 {p.qty} 股')
        p.action, p.sell_qty = 'overdue', p.qty
        return p
    if not p.qty:
        return p
    # today's decision with actual numbers (armed history from replay)
    if exited:
        p.armed = True
    decision = close_decision(p, last, p.net)
    if p.net > 0:
        p.armed = True
    if decision in ('breakeven', 'day3'):
        p.action, p.sell_qty = decision, p.qty
    elif isinstance(decision, int):
        target = math.floor(p.initial * decision / 3 + 1e-9) if decision < 3 else p.initial
        p.action, p.sell_qty = 'body5', max(0, min(p.qty, target - actual_sold))
        if decision >= 3:
            p.sell_qty = p.qty
    else:
        p.action = 'hold'
    return p


def breakeven_price(p):
    return (p.cost - p.realized) / (p.qty * (1 - FEE)) if p.qty else math.nan


def scan(data, sectors):
    rows = []
    for s, bars in data.items():
        if len(bars) < 61:
            continue
        j = kdj(bars)
        st = indicators(bars)
        r, b = st[-1], bars[-1]
        if not all(math.isfinite(v) for v in (j[-1], r['ma60'], r['atr'])) or r['atr'] <= 0:
            continue
        if j[-1] < 12 and b.close > r['ma60']:
            sw = swing_low(bars, len(bars) - 1)
            stop = pick_stop(b.close, sw)
            # Day-low fallback: the stop is unknown until tomorrow's close; size on today's low as an estimate.
            dist = b.close - stop if stop is not None else max(b.close - b.low, .01 * b.close)
            rows.append(dict(symbol=s, sector=sectors.get(s, '其他'), close=b.close, j=j[-1], ma60=r['ma60'],
                             atr=r['atr'], swing=sw, stop=stop, dist=dist, low=b.low,
                             new=not (j[-2] < 12 and bars[-2].close > st[-2]['ma60'])))
    return sorted(rows, key=lambda r: r['symbol'])


def size_candidates(cands, positions, equity, sectors, prices):
    held = {p.symbol for p in positions if p.qty}
    sector_val = defaultdict(float)
    invested = 0.
    for p in positions:
        if p.qty:
            v = p.qty * prices.get(p.symbol, p.entry_price)
            sector_val[sectors.get(p.symbol, '其他')] += v
            invested += v
    # Tomorrow's rule sales happen at the same open before buys (as in sector_b1), so their cash counts.
    for p in positions:
        if p.qty and p.action in FULL_EXIT + ('body5',):
            v = p.sell_qty * prices.get(p.symbol, p.entry_price)
            sector_val[sectors.get(p.symbol, '其他')] -= v
            invested -= v
    cash = equity - invested
    for c in cands:
        if c['symbol'] in held:
            c['qty'], c['why'] = 0, '已持有，不加仓'
            continue
        px, dist = c['close'], c['dist']
        room = max(0., (SECTOR_CAP * equity - sector_val[c['sector']]) / (1 + SECTOR_CAP * FEE))
        limits = {'风险0.5%': equity * RISK / (dist + 2 * px * FEE), '单票20%': equity * MAX_POS / (px * (1 + FEE)),
                  '现金': max(0., cash) / (px * (1 + FEE)), '行业30%': room / px}
        binding = min(limits, key=limits.get)
        qty = math.floor(limits[binding])
        if qty < 1 or dist >= px:
            c['qty'], c['why'] = 0, f'{binding}已满'
            continue
        c['qty'], c['why'] = qty, binding
        cash -= qty * px * (1 + FEE)
        sector_val[c['sector']] += qty * px
    return cands, cash


FULL_EXIT = ('breakeven', 'day3', 'overdue')
ACTION_TEXT = {
    'breakeven': '🔴 明早开盘全部卖出：曾盈利、现在整笔转亏（保本退出）',
    'day3': '🔴 明早开盘全部卖出：第3天收盘仍未盈利',
    'overdue': '🔴 立即全部卖出：规则已触发清仓',
    'body5': '🟡 明早开盘分批止盈：今天大阳线（收盘/开盘 ≥ 5%）',
    'hold': '🟢 继续持有，止损单保持',
    'check': '⚠️ 数据有问题，请检查 positions.csv',
}


def fmt_swing(c):
    if c['swing'] is None:
        return '近60日无前低'
    return f"{c['swing']:.2f}（−{1 - c['swing'] / c['close']:.1%}）"


FALLBACK_NAMES = {'yahoo-intraday': 'Yahoo 分时合成', 'stooq': 'Stooq', 'nasdaq': 'Nasdaq'}
FALLBACK_FILE = None  # set in main() to <data>/_fallback.json


def fallback_note(asof):
    """Which symbols got today's bar from a backup source instead of Yahoo daily."""
    try:
        fb = json.loads(Path(FALLBACK_FILE).read_text())
    except (TypeError, OSError, ValueError):
        return ''
    if fb.get('date') != str(asof) or not fb.get('filled'):
        return ''
    by = defaultdict(list)
    for s, src in sorted(fb['filled'].items()):
        by[FALLBACK_NAMES.get(src, src)].append(s)
    return ('Yahoo 日线当时还没更新，以下股票的当天 K 线来自备用源（收盘价可能和官方收盘价有几分钱差异）：'
            + '；'.join(f'{k} {len(v)} 只（{", ".join(v)}）' for k, v in by.items()))


def report(asof, equity, positions, cands, cash_left, sectors, missing, stale):
    L = [f'# 盯盘提醒 {asof}（B1 + MA60 + 第3天检查）', '',
         f'数据：{asof} 收盘（Yahoo 复权日线）。账户净值设定 ${equity:,.0f}（改 `monitor/config.json`）。',
         '买入、分批卖出、保本/第3天卖出都在**下一个交易日开盘**执行；**止损是富途止损单（盘中触价即卖）**。'
         '只是规则提醒，不是投资建议。', '']
    L += ['## 一、持仓操作', '']
    if not positions:
        L += ['`monitor/positions.csv` 里没有持仓。买入后记得填上，否则无法提醒止损和卖出。', '']
    for p in positions:
        L.append(f"### {p.symbol}　{ACTION_TEXT.get(p.action, p.action)}")
        if p.action in FULL_EXIT + ('body5',) and p.sell_qty:
            L.append(f'- **卖出 {p.sell_qty} 股**（剩余 {p.qty - p.sell_qty} 股）')
        if p.action != 'check':
            be = breakeven_price(p)
            L += [f'- 买入 {p.entry_date} @ {p.entry_price:.2f}，最初 {p.initial} 股，现 {p.qty} 股；今收 {p.close:.2f}',
                  f'- 整笔盈亏（含已卖、含费）：{p.net:+,.0f} 美元（{p.net / p.cost:+.1%}）；持有第 {p.held} 天',
                  f'- **止损单：{p.stop:.2f}**（{p.stop_kind}，距买入价 −{1 - p.stop / p.entry_price:.1%}，固定不动）']
            if p.action in FULL_EXIT:
                pass
            elif p.armed:
                L.append(f'- 保本线已启用：收盘价 ≤ **{be:.2f}** 即整笔不赚钱 → 次日开盘全卖')
            else:
                L.append(f'- 保本线未启用（尚未有收盘盈利）；盈利后收盘价跌回 {be:.2f} 以下即全卖')
            if p.held < CHECK_DAY and p.action not in FULL_EXIT:
                L.append(f'- 第 {CHECK_DAY} 天收盘若整笔仍 ≤ 0（收盘价 ≤ {be:.2f}）→ 次日开盘全卖')
            if p.stage < 3 and p.action not in FULL_EXIT:
                nxt = math.floor(p.initial * (p.stage + 1) / 3 + 1e-9) - (p.initial - p.qty)
                L.append(f'- 已分批 {p.stage}/3；下一根大阳线（收盘/开盘 ≥ 5%）→ 次日开盘再卖约 {max(nxt, 0)} 股')
        L += [f'- ⚠️ {n}' for n in p.notes] + ['']
    L += ['## 二、明早可以买入', '']
    if not cands:
        L += ['今天清单里没有满足 J<12 且站上 MA60 的股票。', '']
    else:
        L += ['| 代码 | 行业 | 今收 | J | 高于MA60 | 前低 | 止损 | 止损幅度 | 建议股数 | 约占净值 | 最大亏损 |',
              '|---|---|---:|---:|---:|---:|---|---:|---:|---:|---:|']
        for c in cands:
            q = f"**{c['qty']}**（{c['why']}）" if c['qty'] else f"— {c['why']}"
            if c['stop'] is not None:
                stop = f"**{c['stop']:.2f}**（前低）"
                pct = f"{c['dist'] / c['close']:.1%}"
            else:
                stop = '买入当天最低价（前低超10%）'
                pct = f"约{c['dist'] / c['close']:.1%}?"
            L.append(f"| {c['symbol']}{' 🆕' if c['new'] else ''} | {c['sector']} | {c['close']:.2f} | {c['j']:.1f} | "
                     f"{c['close'] / c['ma60'] - 1:+.1%} | {fmt_swing(c)} | {stop} | {pct} | {q} | "
                     f"{c['qty'] * c['close'] / equity:.1%} | ${c['qty'] * c['dist']:,.0f} |")
        L += ['', '怎么操作：',
              '1. 明天开盘买入上表股数（股数按今收估算；若开盘价偏离今收较多，按 净值×0.5% ÷ (开盘价−止损价) 重算，且不超过净值 20%）。',
              '2. 有前低的：成交后立刻在富途挂**止损单**（触发价 = 表中止损价）。',
              '3. 「买入当天最低价」的：收盘后把当天最低价设为止损单；明天的报告会给出准确价格。'
              '（股数是按今天振幅估的，若买入当天振幅很大，实际风险会高于 0.5%。）',
              '4. 把成交价、股数填进 `monitor/positions.csv`，之后每天的报告会跟踪止损、保本、第3天和分批止盈。', '',
              f"🆕 = 今天首次满足条件；其他是连续第 N 天满足（回测里同样会买）。按代码字母顺序分配现金（与回测一致），"
              f"买完约剩现金 ${cash_left:,.0f}。", '']
    L += ['## 三、规则速查', '',
          '- 买：收盘 J(9,3,3)<12 且 收盘>MA60 → 次日开盘买。单笔最大亏损 = 净值 0.5%（股数 = 0.5%净值 ÷ 止损距离），单票 ≤20%，单行业 ≤30%。',
          '- 止损（盘中止损单，固定不动）：上涨趋势的前一个低点（买入价下方 10% 以内）；超过 10% 或没有前低则用买入当天的最低价。'
          '跳空低开到止损价下方会按开盘价成交。',
          '- 保本：任一收盘整笔赚钱后，再出现收盘整笔不赚钱 → 次日开盘全卖。',
          '- 第3天：买入日算第1天，第3天收盘整笔不赚钱 → 次日开盘全卖。',
          '- 止盈：大阳线 收盘/开盘−1 ≥ 5% → 次日开盘卖最初股数 1/3；第2根到 2/3；第3根清仓。', '']
    fb = fallback_note(asof)
    if fb:
        L += [fb, '']
    if missing or stale:
        L += [f'没有行情文件：{", ".join(missing) or "无"}；行情没有 {asof} 这一天（停牌、退市或新上市）：{", ".join(stale) or "无"}', '']
    return '\n'.join(L)


def summary(asof, positions, cands):
    """One line for push channels, e.g. '盯盘10-30：卖0 买1 | 买 RGC 2237股 止损6.50(前低)'."""
    sells = [p for p in positions if p.action in FULL_EXIT + ('body5',) and p.sell_qty]
    buys = [c for c in cands if c['qty']]
    parts = [f"盯盘{asof:%m-%d}：卖{len(sells)} 买{len(buys)}"]
    parts += [f"卖 {p.symbol} {p.sell_qty}股" for p in sells]
    parts += [f"买 {c['symbol']} {c['qty']}股 止损" + (f"{c['stop']:.2f}(前低)" if c['stop'] is not None else '买入日低点')
              for c in buys]
    return ' | '.join(parts)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--data', default=str(REPO / 'data'))
    ap.add_argument('--asof', help='last complete session (default: latest date in SPY/NVDA data)')
    ap.add_argument('--positions', default=str(HERE / 'positions.csv'))
    ap.add_argument('--config', default=str(HERE / 'config.json'))
    ap.add_argument('--out', help='write report to this file as well')
    ap.add_argument('--summary-out', help='write a one-line summary (for push notifications) to this file')
    ap.add_argument('--require-date', help='exit 3 if this date is a NYSE holiday, 4 if under 90%% of the pool has a bar '
                    'for it yet (Yahoo not updated); no report in either case')
    ap.add_argument('--check-only', action='store_true', help='with --require-date: only check data freshness')
    a = ap.parse_args()
    data_dir = Path(a.data)
    global FALLBACK_FILE
    FALLBACK_FILE = data_dir / '_fallback.json'
    sectors = load_pool()
    positions = read_positions(a.positions)
    for p in positions:
        sectors.setdefault(p.symbol, '其他')
    asof = date.fromisoformat(a.asof) if a.asof else max(
        load_bars(data_dir / f'{s}.csv', s)[-1].date for s in ('SPY', 'NVDA') if (data_dir / f'{s}.csv').exists())
    if a.require_date:
        want = date.fromisoformat(a.require_date)
        if want.weekday() >= 5 or str(want) in NYSE_HOLIDAYS:
            print(f'{want} is not a NYSE trading day', file=sys.stderr)
            sys.exit(3)
        have = [s for s in sectors if (data_dir / f'{s}.csv').exists()]
        fresh = sum(1 for s in have if any(b.date == want for b in load_bars(data_dir / f'{s}.csv', s)[-5:]))
        print(f'data freshness for {want}: {fresh}/{len(have)} symbols have the bar', file=sys.stderr)
        if fresh < FRESH_MIN * len(have):
            sys.exit(4)
        if a.check_only:
            sys.exit(0)
    data, missing, stale = {}, [], []
    for s in sectors:
        f = data_dir / f'{s}.csv'
        if not f.exists():
            missing.append(s)
            continue
        bars = load_bars(f, s, asof)
        if not bars or bars[-1].date != asof:
            stale.append(s)
            continue
        data[s] = bars
    equity = float(json.loads(Path(a.config).read_text())['account_equity_usd'])
    for p in positions:
        if p.symbol in data:
            replay(p, data[p.symbol])
        else:
            p.action = 'check'
            p.notes.append(f'{p.symbol} 没有 {asof} 的行情（不在 pool.txt 里？请加进去）')
    prices = {s: bs[-1].close for s, bs in data.items()}
    cands = [c for c in scan(data, sectors)]
    cands, cash_left = size_candidates(cands, positions, equity, sectors, prices)
    text = report(asof, equity, positions, cands, cash_left, sectors, missing, stale)
    print(text)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(text + '\n')
    if a.summary_out:
        Path(a.summary_out).write_text(summary(asof, positions, cands) + '\n')


if __name__ == '__main__':
    main()
