"""Chinese fundamental brief per stock, written by Claude from Yahoo's numbers plus its own web search.

Only runs when ANTHROPIC_API_KEY is set (GitHub secret). Returns {symbol: {"text": str, "sources": [(title, url)]}}.
Facts and figures only, no buy/sell advice: the trading decision stays with the rules."""
import json
import os

MODEL = 'claude-opus-5-5'
SYSTEM = (
    '你是给个人投资者写盘前简报的证券分析师。读者已经有一套纯技术面的交易规则，'
    '你的任务是补上基本面和消息面，让他知道自己买的是一家什么公司、最近发生了什么。\n'
    '要求：\n'
    '- 先用网页搜索核实：最近一次财报（日期、收入和每股收益与预期对比、业绩指引上调还是下调、财报后股价反应）、'
    '下一次财报日期、最近 30 天的重大消息（诉讼、增发、并购、高管变动、评级大幅调整、行业政策）。\n'
    '- 输出 5 条，中文，每条一到两句，依次为：【公司】做什么、靠什么赚钱；【财务】增长、利润率、现金流、负债，'
    '用给你的数字并说明好坏；【估值】和增速相比贵不贵、分析师目标价；【最近】财报和重大消息；'
    '【风险】这笔交易最需要注意的一两件事（例如几天后发财报、亏损烧钱、空头比例高、刚增发）。\n'
    '- 只写查得到的事实和数字，写清日期；查不到就写"未查到"，不要猜。来源之间说法不一致时写出分歧。\n'
    '- 不给买卖建议，不评价交易规则本身，不写开场白和总结。每条以【】标签开头，单独一行。'
)


def _payload(sym, name, d, setup, asof):
    return json.dumps({'日期': str(asof), '代码': sym, '公司': name or d.get('longName') or sym,
                       'Yahoo 数据': {k: v for k, v in d.items() if k not in ('error',)},
                       '交易计划': setup}, ensure_ascii=False, default=str)


def _call(client, messages):
    kw = dict(model=MODEL, max_tokens=16000, system=SYSTEM, messages=messages,
              output_config={'effort': 'medium'},
              tools=[{'type': 'web_search_20260209', 'name': 'web_search', 'max_uses': 5}],
              betas=['server-side-fallback-2026-07-01'])
    try:
        return client.beta.messages.create(fallbacks='default', **kw)
    except TypeError:  # an SDK release without the keyword: send it in the body
        return client.beta.messages.create(extra_body={'fallbacks': 'default'}, **kw)


def brief(sym, name, d, setup, asof):
    if not os.environ.get('ANTHROPIC_API_KEY'):
        return None
    import anthropic
    client = anthropic.Anthropic(timeout=240.0, max_retries=2)
    prompt = ('根据下面的数据，并用网页搜索核实最新财报和消息，写这只股票的盘前基本面简报。\n' +
              _payload(sym, name, d, setup, asof))
    messages = [{'role': 'user', 'content': prompt}]
    try:
        for _ in range(4):
            resp = _call(client, messages)
            if resp.stop_reason != 'pause_turn':
                break
            # Server-side search loop hit its limit: send the turn back so it continues.
            messages = [messages[0], {'role': 'assistant', 'content': [b.model_dump(exclude_none=True) for b in resp.content]}]
    except anthropic.APIStatusError as e:
        print(f'{sym}: Claude API {e.status_code}: {str(e)[:160]}')
        return None
    except anthropic.APIConnectionError as e:
        print(f'{sym}: Claude API connection error: {e}')
        return None
    if resp.stop_reason == 'refusal':
        print(f'{sym}: Claude declined ({getattr(resp.stop_details, "category", None)})')
        return None
    text, sources = [], []
    for b in resp.content:
        if b.type != 'text':
            continue
        text.append(b.text)
        for c in (getattr(b, 'citations', None) or []):
            url = getattr(c, 'url', None)
            if url and url not in [u for _, u in sources]:
                sources.append((getattr(c, 'title', None) or url, url))
    out = ''.join(text).strip()
    u = resp.usage
    print(f'{sym}: Claude brief {len(out)} chars, in {u.input_tokens} / out {u.output_tokens} tokens')
    return {'text': out, 'sources': sources[:4]} if out else None


def briefs(stocks, info, names, asof):
    """stocks: [(symbol, setup_dict)]"""
    out = {}
    for sym, setup in stocks:
        b = brief(sym, names.get(sym), info.get(sym) or {}, setup, asof)
        if b:
            out[sym] = b
    return out
