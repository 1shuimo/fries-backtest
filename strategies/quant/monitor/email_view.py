"""Phone-friendly email for the daily monitor: one card per stock, company names, no Markdown tables.

Built from the same objects daily_monitor.report() uses. Inline styles only, because mail apps
(Gmail on phones especially) drop most <style> rules."""
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


def subject(asof, positions, cands, names, holidays):
    when, what = headline(asof, positions, cands, names, holidays)
    return f'盯盘 {when}：{what}'


def build_html(asof, equity, positions, cands, cash_left, names, holidays, link='', notes=()):
    from daily_monitor import breakeven_price, CHECK_DAY  # late import: avoid a cycle
    when, what = headline(asof, positions, cands, names, holidays)
    nxt = next_session(asof, holidays)
    out = [f'<div style="background:{BG};padding:16px 12px;font-family:-apple-system,BlinkMacSystemFont,\'PingFang SC\','
           f'\'Hiragino Sans GB\',\'Microsoft YaHei\',sans-serif;color:{INK}">',
           f'<div style="max-width:560px;margin:0 auto">',
           f'<div style="font-size:13px;color:{MUTED}">每日盯盘 · {when}（北京时间 {nxt:%m-%d} 晚上开盘）</div>',
           f'<div style="font-size:22px;font-weight:700;margin:4px 0 16px;line-height:1.35">{_e(what)}</div>']

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


def build_text(asof, equity, positions, cands, names, holidays):
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
    L += ['', f'账户净值按 ${equity:,.0f} 计算。只是规则提醒，不是投资建议。']
    return '\n'.join(L)
