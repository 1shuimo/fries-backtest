"""Phone-friendly email for the daily monitor: one card per stock, company names, no Markdown tables.

Built from the same objects daily_monitor.report() uses. Inline styles only, because mail apps
(Gmail on phones especially) drop most <style> rules."""
import datetime
import html
import json
import math
from datetime import timedelta
from pathlib import Path

NAMES_FILE = Path(__file__).resolve().parent / 'names.json'

INK, MUTED, LINE, CARD, BG = '#1f2328', '#59636e', '#d8dee4', '#ffffff', '#f3f4f6'
GREEN, RED, AMBER, BLUE = '#1a7f37', '#c2232c', '#9a6700', '#0b57d0'


def load_names(path=NAMES_FILE):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def label(sym, names):
    """'AI（C3.ai）' or just 'AI' when the name is unknown."""
    n = names.get(sym)
    return f'{sym}（{n}）' if n else sym


FUND_KEYS = ('totalRevenue', 'revenueGrowth', 'grossMargins', 'operatingMargins', 'profitMargins', 'freeCashflow',
             'totalCash', 'totalDebt', 'trailingPE', 'forwardPE', 'priceToSalesTrailing12Months', 'targetMeanPrice',
             'recommendationKey', 'numberOfAnalystOpinions', 'shortPercentOfFloat', 'heldPercentInstitutions',
             'currentPrice', 'fiftyTwoWeekHigh', 'fiftyTwoWeekLow', 'beta')
REC = {'strong_buy': '强烈买入', 'buy': '买入', 'hold': '持有', 'underperform': '跑输', 'sell': '卖出',
       'strong_sell': '强烈卖出', 'none': ''}
EARNINGS_WARN_DAYS = 10  # calendar days: a report this close means holding through a likely gap


def company_info(symbols, asof):
    """Description, size and earnings for a few symbols, from Yahoo. Empty dict per symbol when offline."""
    out = {}
    try:
        import yfinance as yf
    except ImportError:
        return out
    for s in symbols:
        d = {}
        try:
            t = yf.Ticker(s)
            info = t.get_info() or {}
            summ = ' '.join(str(info.get('longBusinessSummary') or '').split())
            if summ:
                first = summ.split('. ')
                text = first[0] + ('. ' + first[1] if len(first) > 1 and len(first[0]) < 120 else '')
                d['about'] = (text[:260].rstrip() + '…') if len(text) > 260 else text.rstrip('.') + '.'
            d['industry'] = info.get('industry') or ''
            d['cap'] = info.get('marketCap')
            d['longName'] = info.get('longName') or ''
            d['fund'] = {k: info.get(k) for k in FUND_KEYS if info.get(k) is not None}
        except Exception as e:
            d['error'] = f'{type(e).__name__}'
        try:
            cal = t.get_calendar() or {}
            nxt = [x for x in (cal.get('Earnings Date') or []) if str(x) >= str(asof)]
            if nxt:
                d['next_earnings'] = str(min(nxt))[:10]
        except Exception:
            pass
        try:
            ed = t.get_earnings_dates(limit=8)
            if ed is not None and not ed.empty:
                past = ed[ed['Reported EPS'].notna()]
                past = past[[str(i)[:10] <= str(asof) for i in past.index]]
                if not past.empty:
                    r = past.sort_index().iloc[-1]
                    d['last_earnings'] = {'date': str(past.sort_index().index[-1])[:10],
                                          'eps': _num(r.get('Reported EPS')), 'est': _num(r.get('EPS Estimate')),
                                          'surprise': _num(r.get('Surprise(%)'))}
                if 'next_earnings' not in d:
                    fut = [str(i)[:10] for i in ed.index if str(i)[:10] > str(asof)]
                    if fut:
                        d['next_earnings'] = min(fut)
        except Exception:
            pass
        out[s] = d
    return out


def _num(x):
    try:
        x = float(x)
        return None if math.isnan(x) else x
    except (TypeError, ValueError):
        return None


def _cap(v):
    if not v:
        return ''
    return f'市值约 {v / 1e9:,.0f} 亿美元' if v >= 1e11 else f'市值约 {v / 1e8:,.0f} 亿美元'


def _usd(v):
    v = float(v)
    a = abs(v)
    s = f'{a / 1e8:,.1f} 亿' if a >= 1e8 else f'{a / 1e4:,.0f} 万'
    return ('−' if v < 0 else '') + s + '美元'


def fund_lines(f, price=None):
    """Short Chinese lines from Yahoo's fundamentals, worst news in red."""
    pct = lambda x: f'{x:+.0%}' if x is not None else None
    L = []
    g = []
    if f.get('totalRevenue'):
        g.append(f'年收入 {_usd(f["totalRevenue"])}')
    if f.get('revenueGrowth') is not None:
        c = GREEN if f['revenueGrowth'] > 0 else RED
        g.append(f'最近一季收入同比 <b style="color:{c}">{pct(f["revenueGrowth"])}</b>')
    if g:
        L.append(('成长', '，'.join(g)))
    m = []
    if f.get('grossMargins') is not None:
        m.append(f'毛利率 {f["grossMargins"]:.0%}')
    if f.get('operatingMargins') is not None:
        c = GREEN if f['operatingMargins'] > 0 else RED
        m.append(f'经营利润率 <b style="color:{c}">{f["operatingMargins"]:.0%}</b>')
    if f.get('profitMargins') is not None:
        m.append('盈利' if f['profitMargins'] > 0 else f'<b style="color:{RED}">亏损</b>')
    if m:
        L.append(('利润', '，'.join(m)))
    c = []
    if f.get('freeCashflow') is not None:
        col = GREEN if f['freeCashflow'] > 0 else RED
        c.append(f'自由现金流 <span style="color:{col}">{_usd(f["freeCashflow"])}</span>')
    if f.get('totalCash') is not None:
        c.append(f'现金 {_usd(f["totalCash"])}')
    if f.get('totalDebt') is not None:
        c.append(f'负债 {_usd(f["totalDebt"])}')
    if c:
        L.append(('现金', '，'.join(c)))
    v = []
    if f.get('trailingPE'):
        v.append(f'市盈率 {f["trailingPE"]:.0f}')
    elif f.get('forwardPE') and f['forwardPE'] > 0:
        v.append(f'预期市盈率 {f["forwardPE"]:.0f}')
    elif f.get('profitMargins') is not None and f['profitMargins'] <= 0:
        v.append('亏损，无市盈率')
    if f.get('priceToSalesTrailing12Months'):
        v.append(f'市销率 {f["priceToSalesTrailing12Months"]:.1f}')
    if v:
        L.append(('估值', '，'.join(v)))
    a = []
    px = price or f.get('currentPrice')
    if f.get('targetMeanPrice') and px:
        up = f['targetMeanPrice'] / px - 1
        a.append(f'目标价均值 {f["targetMeanPrice"]:.2f}（{up:+.0%}）')
    rk = REC.get(str(f.get('recommendationKey') or ''), '')
    if rk:
        a.append(f'评级 {rk}' + (f'（{f["numberOfAnalystOpinions"]} 家）' if f.get('numberOfAnalystOpinions') else ''))
    if f.get('shortPercentOfFloat') is not None:
        s = f['shortPercentOfFloat']
        a.append(f'空头占流通股 ' + (f'<b style="color:{RED}">{s:.0%}</b>' if s >= .15 else f'{s:.0%}'))
    if a:
        L.append(('分析师', '，'.join(a)))
    return L


def brief_rows(b):
    if not b:
        return []
    lines = [x.strip() for x in b['text'].splitlines() if x.strip()]
    body = '<br>'.join(_e(x) for x in lines)
    src = ''
    if b.get('sources'):
        src = (f'<br><span style="font-size:12px;color:{MUTED}">来源：' +
               '；'.join(f'<a href="{_e(u)}" style="color:{BLUE}">{_e(str(tt)[:40])}</a>' for tt, u in b['sources']) + '</span>')
    return [_row('Claude 分析', f'<span style="font-size:14px;line-height:1.6">{body}</span>{src}')]


def info_rows(sym, info, asof, price=None):
    """Card rows about the company and its earnings; warns when the next report is close."""
    d = info.get(sym) or {}
    rows = []
    about = ' · '.join(x for x in (d.get('industry'), _cap(d.get('cap'))) if x)
    if d.get('about') or about:
        rows.append(_row('公司', (f'<span style="font-size:13px;color:{MUTED}">{_e(about)}</span><br>' if about else '')
                         + f'<span style="font-size:13px">{_e(d.get("about", ""))}</span>'))
    for k, v in fund_lines(d.get('fund') or {}, price):
        rows.append(_row(k, f'<span style="font-size:14px">{v}</span>'))
    le = d.get('last_earnings')
    if le and le.get('eps') is not None:
        beat = le['est'] is not None and le['eps'] >= le['est']
        txt = f'{le["date"]}：每股收益 {le["eps"]:.2f}'
        if le['est'] is not None:
            txt += f'，预期 {le["est"]:.2f}，<b style="color:{GREEN if beat else RED}">{"超预期" if beat else "不及预期"}</b>'
        rows.append(_row('上次财报', txt))
    ne = d.get('next_earnings')
    if ne:
        days = (datetime.date.fromisoformat(ne) - asof).days
        if 0 <= days <= EARNINGS_WARN_DAYS:
            rows.append(_row('下次财报', f'<b style="color:{RED}">{ne}（{days} 天后）</b><br><span style="font-size:13px;'
                             f'color:{RED}">持仓会跨过财报，财报后跳空可能直接越过止损价</span>'))
        else:
            rows.append(_row('下次财报', f'{ne}（{days} 天后）'))
    elif d and not d.get('error'):
        rows.append(_row('下次财报', f'<span style="color:{MUTED}">Yahoo 没有日期，买前在富途「财报」里看一眼</span>'))
    return rows


def next_session(day, holidays):
    d = day + timedelta(days=1)
    while d.weekday() >= 5 or str(d) in holidays:
        d += timedelta(days=1)
    return d


def _e(s):
    return html.escape(str(s))


def _row(k, v):
    return (f'<tr><td style="padding:6px 0;color:{MUTED};font-size:14px;width:34%;vertical-align:top">{_e(k)}</td>'
            f'<td style="padding:6px 0;color:{INK};font-size:15px;vertical-align:top">{v}</td></tr>')


def _card(head, sub, rows, accent):
    return (f'<div style="background:{CARD};border:1px solid {LINE};border-left:4px solid {accent};border-radius:10px;'
            f'padding:14px 16px;margin:0 0 12px">'
            f'<div style="font-size:20px;font-weight:700;color:{INK};line-height:1.3">{head}</div>'
            f'<div style="font-size:13px;color:{MUTED};margin:2px 0 8px">{sub}</div>'
            f'<table role="presentation" style="width:100%;border-collapse:collapse">{"".join(rows)}</table></div>')


def _head(sym, names):
    n = names.get(sym)
    return _e(sym) + (f' <span style="font-weight:400;color:{MUTED};font-size:16px">{_e(n)}</span>' if n else '')


ACTION_BADGE = {
    'breakeven': ('明早开盘全部卖出', RED, '曾经赚钱，现在整笔转亏（保本退出）'),
    'day3': ('明早开盘全部卖出', RED, '第 3 天收盘还没赚钱'),
    'overdue': ('马上全部卖出', RED, '规则已经触发清仓'),
    'body5': ('明早开盘卖一部分', AMBER, '今天出现大阳线（收盘比开盘涨 ≥5%）'),
    'hold': ('继续持有', GREEN, '止损单保持不动'),
    'check': ('数据有问题', AMBER, '请检查 positions.csv'),
}


def headline(asof, positions, cands, names, holidays):
    nxt = next_session(asof, holidays)
    sells = [p for p in positions if p.action in ('breakeven', 'day3', 'overdue', 'body5') and p.sell_qty]
    buys = [c for c in cands if c['qty']]
    when = f'{asof:%m-%d} 收盘 → {nxt:%m-%d} 开盘'
    if not sells and not buys:
        return when, '今晚没有要做的操作'
    parts = []
    if sells:
        parts.append('卖 ' + '、'.join(f'{label(p.symbol, names)} {p.sell_qty} 股' for p in sells))
    if buys:
        parts.append('买 ' + '、'.join(f'{label(c["symbol"], names)} {c["qty"]} 股' for c in buys))
    return when, '；'.join(parts)


def earnings_soon(cands, info, asof):
    out = []
    for c in cands:
        ne = ((info or {}).get(c['symbol']) or {}).get('next_earnings')
        if c['qty'] and ne and 0 <= (datetime.date.fromisoformat(ne) - asof).days <= EARNINGS_WARN_DAYS:
            out.append(c['symbol'])
    return out


def subject(asof, positions, cands, names, holidays):
    when, what = headline(asof, positions, cands, names, holidays)
    return f'盯盘 {when}：{what}'


def build_html(asof, equity, positions, cands, cash_left, names, holidays, link='', notes=(), info=None, briefs=None):
    from daily_monitor import breakeven_price, CHECK_DAY  # late import: avoid a cycle
    when, what = headline(asof, positions, cands, names, holidays)
    nxt = next_session(asof, holidays)
    out = [f'<div style="background:{BG};padding:16px 12px;font-family:-apple-system,BlinkMacSystemFont,\'PingFang SC\','
           f'\'Hiragino Sans GB\',\'Microsoft YaHei\',sans-serif;color:{INK}">',
           f'<div style="max-width:560px;margin:0 auto">',
           f'<div style="font-size:13px;color:{MUTED}">每日盯盘 · {when}（北京时间 {nxt:%m-%d} 晚上开盘）</div>',
           f'<div style="font-size:22px;font-weight:700;margin:4px 0 16px;line-height:1.35">{_e(what)}</div>']
    soon = earnings_soon(cands, info, asof)
    if soon:
        out.append(f'<div style="background:#fff1f0;border:1px solid {RED};border-radius:10px;padding:10px 14px;'
                   f'margin:0 0 14px;font-size:14px;color:{RED}">⚠ {_e("、".join(soon))} {EARNINGS_WARN_DAYS} 天内发财报。'
                   f'规则本身不看财报，回测里也包含跨财报的交易；但财报后跳空可能直接越过止损价，亏损会超过 0.5%。</div>')

    out.append(f'<div style="font-size:16px;font-weight:700;margin:8px 0">持仓</div>')
    if not positions:
        out.append(f'<div style="font-size:14px;color:{MUTED};margin:0 0 12px">还没有登记持仓。买入后把成交价和股数填进 '
                   f'monitor/positions.csv，这里才会提醒止损和卖出。</div>')
    for p in positions:
        badge, color, why = ACTION_BADGE.get(p.action, (p.action, MUTED, ''))
        rows = []
        if p.action in ('breakeven', 'day3', 'overdue', 'body5') and p.sell_qty:
            rows.append(_row('卖出', f'<b style="color:{color}">{p.sell_qty} 股</b>（剩 {p.qty - p.sell_qty} 股）'))
        if p.action != 'check':
            rows.append(_row('止损单', f'<b>{p.stop:.2f}</b>（{_e(p.stop_kind)}，固定不动）'))
            rows.append(_row('持仓', f'{p.qty} 股，买入 {p.entry_price:.2f}，今收 {p.close:.2f}'))
            rows.append(_row('整笔盈亏', f'<span style="color:{GREEN if p.net > 0 else RED}">{p.net:+,.0f} 美元'
                                          f'（{p.net / p.cost:+.1%}）</span>'))
            be = breakeven_price(p)
            if p.action not in ('breakeven', 'day3', 'overdue') and math.isfinite(be):
                if p.armed:
                    rows.append(_row('保本线', f'收盘 ≤ {be:.2f} 就次日全卖'))
                elif p.held < CHECK_DAY:
                    rows.append(_row('第 3 天', f'第 {CHECK_DAY} 天收盘 ≤ {be:.2f} 就次日全卖（现在第 {p.held} 天）'))
        if p.action not in ('breakeven', 'day3', 'overdue'):
            rows += info_rows(p.symbol, info or {}, asof, p.close)
        for n in p.notes:
            rows.append(_row('注意', _e(n)))
        out.append(_card(_head(p.symbol, names), f'<b style="color:{color}">{_e(badge)}</b> · {_e(why)}', rows, color))

    out.append(f'<div style="font-size:16px;font-weight:700;margin:16px 0 8px">今晚可以买</div>')
    buys = [c for c in cands if c['qty']]
    if not cands:
        out.append(f'<div style="font-size:14px;color:{MUTED};margin:0 0 12px">清单里没有股票满足买入条件。</div>')
    for c in buys:
        if c['stop'] is not None:
            stop = (f'<b>{c["stop"]:.2f}</b>（前低，比今收低 {c["dist"] / c["close"]:.1%}）'
                    f'<br><span style="font-size:13px;color:{MUTED}">成交后马上在富途挂止损单</span>')
        else:
            stop = ('<b>买入当天的最低价</b><br><span style="font-size:13px;color:'
                    f'{MUTED}">前低离得太远（超过 10%），收盘后按当天最低价挂止损单，明早邮件给出准确价格</span>')
        rows = [_row('开盘买入', f'<b style="color:{BLUE}">{c["qty"]} 股</b>（约 ${c["qty"] * c["close"]:,.0f}，'
                              f'占净值 {c["qty"] * c["close"] / equity:.1%}）'),
                _row('止损单', stop),
                _row('最多亏', f'约 ${c["qty"] * c["dist"]:,.0f}（净值的 {c["qty"] * c["dist"] / equity:.1%}）'),
                _row('今天', f'收 {c["close"]:.2f} · J {c["j"]:.1f} · 高于 60 日线 {c["close"] / c["ma60"] - 1:+.1%}')]
        rows += info_rows(c['symbol'], info or {}, asof, c['close'])
        rows += brief_rows((briefs or {}).get(c['symbol']))
        sub = _e(c['sector']) + (' · 今天第一次出现买点' if c['new'] else ' · 连续几天满足条件')
        out.append(_card(_head(c['symbol'], names), sub, rows, BLUE))
    skipped = [c for c in cands if not c['qty']]
    if skipped:
        out.append(f'<div style="font-size:13px;color:{MUTED};margin:0 0 12px">满足条件但不买：'
                   + '，'.join(f'{_e(label(c["symbol"], names))}（{_e(c["why"])}）' for c in skipped) + '</div>')
    if buys:
        out.append(f'<div style="font-size:13px;color:{MUTED};margin:0 0 12px;line-height:1.6">股数按今天收盘价估算。'
                   f'开盘价差得多的话，按「净值 × 0.5% ÷（开盘价 − 止损价）」重算，单只不超过净值 20%。'
                   f'买完记得把成交价和股数填进 positions.csv。</div>')

    for n in notes:
        if n:
            out.append(f'<div style="font-size:13px;color:{MUTED};margin:0 0 8px">{_e(n)}</div>')
    out.append(f'<div style="font-size:12px;color:{MUTED};border-top:1px solid {LINE};padding-top:10px;margin-top:16px;'
               f'line-height:1.6">规则：收盘 J&lt;12 且站上 60 日线 → 次日开盘买；止损挂前低（10% 以内），否则用买入当天最低价；'
               f'赚过钱后转亏、或第 3 天还没赚钱 → 次日开盘全卖；大阳线 → 次日卖最初股数的 1/3。'
               f'账户净值按 ${equity:,.0f} 计算。只是规则提醒，不是投资建议。'
               + (f'<br><a href="{_e(link)}" style="color:{BLUE}">完整报告</a>' if link else '') + '</div>')
    out.append('</div></div>')
    return ''.join(out)


def build_text(asof, equity, positions, cands, names, holidays, info=None, briefs=None):
    """Plain-text version (mail clients that block HTML, and push channels)."""
    when, what = headline(asof, positions, cands, names, holidays)
    L = [f'盯盘 {when}', what, '']
    for p in positions:
        badge = ACTION_BADGE.get(p.action, (p.action, '', ''))[0]
        line = f'【持仓】{label(p.symbol, names)}：{badge}'
        if p.sell_qty and p.action in ('breakeven', 'day3', 'overdue', 'body5'):
            line += f'，卖 {p.sell_qty} 股'
        if p.action != 'check':
            line += f'；止损单 {p.stop:.2f}'
        L.append(line)
    for c in cands:
        if not c['qty']:
            continue
        stop = f'{c["stop"]:.2f}（前低，−{c["dist"] / c["close"]:.1%}）' if c['stop'] is not None else '买入当天最低价'
        L.append(f'【买入】{label(c["symbol"], names)}：开盘买 {c["qty"]} 股（约 ${c["qty"] * c["close"]:,.0f}），'
                 f'止损单 {stop}，最多亏约 ${c["qty"] * c["dist"]:,.0f}')
        d = (info or {}).get(c['symbol']) or {}
        if d.get('about'):
            L.append('    ' + d['about'])
        if d.get('next_earnings'):
            days = (datetime.date.fromisoformat(d['next_earnings']) - asof).days
            L.append(f'    下次财报 {d["next_earnings"]}（{days} 天后）' + ('  ⚠ 会跨过财报' if 0 <= days <= EARNINGS_WARN_DAYS else ''))
        b = (briefs or {}).get(c['symbol'])
        if b:
            L += ['    ' + x.strip() for x in b['text'].splitlines() if x.strip()]
    L += ['', f'账户净值按 ${equity:,.0f} 计算。只是规则提醒，不是投资建议。']
    return '\n'.join(L)
