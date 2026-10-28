#!/usr/bin/env python3
"""Backtest FriesTrader's *mechanical* rules on a custom stock pool.

What is simulated (mirrors risk_rules.json defaults, see rules.json):
  Phase A at each close  -> universe filters (price, volume, 200d trend) + signal gate
  Phase B at next open   -> sells first (vol-scaled stop, tiered take-profit,
                            conviction trim), then loss-limit halt, entry gate
                            (price gap, 20d extension, wash-sale, re-entry lock),
                            rank by pct below 52wk high, size by conviction tier
                            with max positions + cash buffer.

What is NOT simulated (be honest about it):
  - The LLM news thesis. Every signal is treated as a `long` at a fixed
    conviction (default medium). This is "signals only", not FriesTrader's
    real stock selection.
  - Market-cap band and earnings blackout (no point-in-time data).
  - Survivorship bias: a pool picked today already knows who survived.

Data: data/<SYMBOL>.csv with columns Date,Open,High,Low,Close,Volume
(split/dividend adjusted), one file per symbol plus the benchmark.
"""
import argparse, csv, json, math, os, statistics
from datetime import date, timedelta


# ---------------------------------------------------------------- data
def load_csv(path):
    rows = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            try:
                rows.append((date.fromisoformat(r["Date"][:10]), float(r["Open"]), float(r["High"]),
                             float(r["Low"]), float(r["Close"]), float(r["Volume"])))
            except (ValueError, KeyError):
                continue
    rows.sort()
    return rows


class Series:
    def __init__(self, rows):
        self.dates = [r[0] for r in rows]
        self.o = [r[1] for r in rows]; self.h = [r[2] for r in rows]
        self.l = [r[3] for r in rows]; self.c = [r[4] for r in rows]
        self.v = [r[5] for r in rows]
        self.idx = {d: i for i, d in enumerate(self.dates)}


# ---------------------------------------------------------------- engine
class Backtest:
    def __init__(self, data, bench, rules, start, end, conviction, slippage, capital):
        self.data, self.bench, self.R = data, bench, rules
        self.start, self.end = start, end
        self.conviction, self.slip, self.capital = conviction, slippage, capital
        self.cash = capital
        self.pos = {}           # sym -> dict(qty, cost, entry_date, tp_fired:set, overweight)
        self.last_sell = {}     # sym -> dict(reason, price, day_no, gain)
        self.loss_sales = {}    # sym -> [date]
        self.realized = []      # (date, pnl)
        self.trades, self.equity, self.events = [], [], []
        self.proposals = []     # from previous close
        self.halted_days = 0

    # -- helpers
    def px(self, sym, d, field):
        s = self.data[sym]; i = s.idx.get(d)
        return None if i is None else getattr(s, field)[i]

    def total_value(self, d, field):
        v = self.cash
        for sym, p in self.pos.items():
            price = self.px(sym, d, field) or p["last"]
            v += p["qty"] * price
        return v

    def sell(self, d, sym, qty, price, reason, day_no):
        p = self.pos[sym]
        qty = min(qty, p["qty"])
        fill = price * (1 - self.slip)
        pnl = (fill - p["cost"]) * qty
        self.cash += fill * qty
        p["qty"] -= qty
        self.realized.append((d, pnl))
        self.trades.append(dict(date=d.isoformat(), symbol=sym, side="sell", reason=reason,
                                qty=round(qty, 6), price=round(fill, 4), pnl=round(pnl, 2),
                                entry_date=p["entry_date"].isoformat(),
                                hold_days=(d - p["entry_date"]).days))
        if pnl < 0:
            self.loss_sales.setdefault(sym, []).append(d)
        if p["qty"] <= 1e-9:
            self.last_sell[sym] = dict(reason=reason, price=fill, day_no=day_no, gain=pnl >= 0)
            del self.pos[sym]

    # -- Phase B part 1: sells at open
    def phase_b_sells(self, d, day_no):
        S, TP, CT = self.R["stop_loss"], self.R["take_profit"]["tiers"], self.R["conviction_trim"]
        tv_open = self.total_value(d, "o")
        target = self.R["conviction_pct"][self.conviction] * tv_open
        for sym in list(self.pos):
            s = self.data[sym]; i = s.idx.get(d)
            if i is None:
                continue
            p, price = self.pos[sym], s.o[i]
            # stop-loss
            ei = s.idx[p["entry_date"]]
            if p["tp_fired"]:
                ref = max(s.h[ei:i]) if i > ei else p["cost"]
            else:
                ref = p["cost"]
            closes = s.c[max(0, i - S["lookback"] - 1):i]
            if S["mode"] == "fixed":
                stop_pct = S["hard_stop_pct"]
            elif len(closes) < S["min_bars"] + 1:
                stop_pct = S["fallback_stop_pct"]
            else:
                rets = [closes[k] / closes[k - 1] - 1 for k in range(1, len(closes))]
                stop_pct = max(S["min_stop_pct"], min(S["max_stop_pct"],
                               S["volatility_stdev_multiplier"] * statistics.stdev(rets)))
            if price <= ref * (1 - stop_pct):
                self.sell(d, sym, p["qty"], price, "stop_loss", day_no)
                continue
            # tiered take-profit, cascading
            gain = price / p["cost"] - 1
            for t in TP:
                if t["gain_pct"] not in p["tp_fired"] and gain >= t["gain_pct"]:
                    p["tp_fired"].add(t["gain_pct"])
                    self.sell(d, sym, p["qty"] * t["sell_fraction_of_position"], price,
                              f"take_profit_{int(t['gain_pct']*100)}", day_no)
                    if sym not in self.pos:
                        break
            if sym not in self.pos:
                continue
            # conviction trim (conviction is constant, so only 'high' never trims)
            if CT["enabled"] and self.conviction != "high":
                val = p["qty"] * price
                if (val - target) / target > CT["overweight_trigger_pct"]:
                    p["overweight"] += 1
                    if p["overweight"] >= CT["min_overweight_conviction_cycles"]:
                        self.sell(d, sym, (val - target) / price, price, "conviction_trim", day_no)
                        if sym in self.pos:
                            self.pos[sym]["overweight"] = 0
                else:
                    p["overweight"] = 0

    # -- loss limits (realized, vs net deposits = starting capital)
    def entries_halted(self, d):
        L = self.R["loss_limits"]
        day = sum(x for dd, x in self.realized if dd == d)
        wk0 = d - timedelta(days=d.weekday())
        week = sum(x for dd, x in self.realized if wk0 <= dd <= d)
        return (day / self.capital <= -L["daily_loss_limit_pct_of_account"] or
                week / self.capital <= -L["weekly_loss_limit_pct_of_account"])

    # -- Phase B part 2: buys at open
    def phase_b_buys(self, d, day_no):
        if not self.proposals:
            return
        if self.entries_halted(d):
            self.halted_days += 1
            for c in self.proposals:
                self.events.append((d, c["symbol"], "loss_limit_halt"))
            return
        G, PS = self.R, self.R["position_sizing"]
        ranked = sorted(self.proposals, key=lambda c: -c["pct_below_52wk_high"])
        tv = self.total_value(d, "o")
        for c in ranked:
            sym = c["symbol"]
            if sym in self.pos:
                continue
            s = self.data[sym]; i = s.idx.get(d)
            if i is None:
                continue
            ask = s.o[i] * (1 + self.slip)
            block = []
            if (ask - c["thesis_price"]) / c["thesis_price"] > G["entry_price_gap"]["max_pct"]:
                block.append("entry_price_gap")
            ma = sum(s.c[i - 20:i]) / 20
            if G["entry_extension"]["enabled"] and (ask - ma) / ma > G["entry_extension"]["max_extension_pct"]:
                block.append("entry_extension")
            W = G["wash_sale_avoidance"]
            if W["enabled"] and any((d - x).days <= W["lookback_window_days"] for x in self.loss_sales.get(sym, [])):
                block.append("wash_sale")
            ls = self.last_sell.get(sym)
            if ls and ask > ls["price"]:
                if not ls["gain"] or day_no - ls["day_no"] < G["sell_reentry_lock"]["gain_close_max_trading_days"]:
                    block.append("sell_reentry_lock")
            if block:
                for b in block:
                    self.events.append((d, sym, b))
                continue
            amount = G["conviction_pct"][self.conviction] * tv
            amount = min(amount, PS["max_position_pct_of_account"] * tv)
            if len(self.pos) + 1 > PS["max_concurrent_positions"]:
                self.events.append((d, sym, "max_concurrent_positions")); continue
            if self.cash - amount < PS["min_cash_buffer_pct"] * tv:
                self.events.append((d, sym, "cash_buffer")); continue
            qty = amount / ask
            self.cash -= amount
            self.pos[sym] = dict(qty=qty, cost=ask, entry_date=d, tp_fired=set(), overweight=0, last=ask)
            self.trades.append(dict(date=d.isoformat(), symbol=sym, side="buy", reason=",".join(c["signals"]),
                                    qty=round(qty, 6), price=round(ask, 4), pnl=0.0,
                                    entry_date=d.isoformat(), hold_days=0))
            self.events.append((d, sym, "bought"))

    # -- Phase A at close
    def phase_a(self, d):
        U, T = self.R["universe"], self.R["signal_thresholds"]
        out = []
        for sym, s in self.data.items():
            i = s.idx.get(d)
            if i is None or i < 252 or sym in self.pos:
                continue
            c = s.c[i]
            if U["penny_stock_filter_enabled"] and c < U["penny_stock_price_threshold_usd"]:
                continue
            avgv = sum(s.v[i - 20:i]) / 20
            if avgv < U["min_avg_daily_volume"]:
                continue
            if U["trend_filter_enabled"]:
                n = U["trend_filter_lookback_trading_days"]
                if c < sum(s.c[i - n + 1:i + 1]) / n:
                    continue
            hi52, lo52 = max(s.h[i - 251:i + 1]), min(s.l[i - 251:i + 1])
            sig = []
            if abs(c / s.c[i - 60] - 1) >= T["price_move_60d_pct"]:
                sig.append("move60")
            if avgv > 0 and s.v[i] >= T["volume_spike_multiple"] * avgv:
                sig.append("volume_spike")
            if c >= hi52 * (1 - T["pct_from_52wk_extreme"]) or c <= lo52 * (1 + T["pct_from_52wk_extreme"]):
                sig.append("near_52wk_extreme")
            if sig:
                out.append(dict(symbol=sym, thesis_price=c, signals=sig, pct_below_52wk_high=1 - c / hi52))
        self.proposals = out

    def run(self):
        days = [d for d in self.bench.dates if self.start <= d <= self.end]
        for n, d in enumerate(days):
            self.phase_b_sells(d, n)
            self.phase_b_buys(d, n)
            for sym, p in self.pos.items():
                c = self.px(sym, d, "c")
                if c:
                    p["last"] = c
            self.equity.append((d, self.total_value(d, "c")))
            self.phase_a(d)
        return days


# ---------------------------------------------------------------- metrics
def stats(curve):
    if len(curve) < 2:
        return {}
    vals = [v for _, v in curve]
    yrs = (curve[-1][0] - curve[0][0]).days / 365.25
    rets = [vals[k] / vals[k - 1] - 1 for k in range(1, len(vals))]
    peak, mdd = vals[0], 0.0
    for v in vals:
        peak = max(peak, v); mdd = min(mdd, v / peak - 1)
    sd = statistics.stdev(rets) if len(rets) > 1 else 0
    return dict(total_return=round(vals[-1] / vals[0] - 1, 4),
                cagr=round((vals[-1] / vals[0]) ** (1 / yrs) - 1, 4) if yrs > 0 else None,
                max_drawdown=round(mdd, 4),
                sharpe=round(statistics.mean(rets) / sd * math.sqrt(252), 2) if sd else None,
                volatility=round(sd * math.sqrt(252), 4))


def buy_hold(series_list, days, capital):
    """Equal-weight buy & hold, each symbol bought at its first close in range."""
    if not series_list:
        return []
    per = capital / len(series_list)
    shares, cash = {}, 0.0
    curve = []
    for d in days:
        v = cash
        for k, s in enumerate(series_list):
            i = s.idx.get(d)
            if k not in shares:
                if i is None:
                    v += per; continue
                shares[k] = per / s.c[i]
            last = s.c[i] if i is not None else s.c[max(j for j, x in enumerate(s.dates) if x <= d)]
            v += shares[k] * last
        curve.append((d, v))
    return curve


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--pool", default="pool.txt")
    ap.add_argument("--rules", default="rules.json")
    ap.add_argument("--benchmark", default="SPY")
    ap.add_argument("--start", default="2021-01-01")
    ap.add_argument("--end", default=date.today().isoformat())
    ap.add_argument("--conviction", default="medium", choices=["low", "medium", "high"])
    ap.add_argument("--slippage", type=float, default=0.0005)
    ap.add_argument("--capital", type=float, default=10000)
    ap.add_argument("--out", default="results")
    a = ap.parse_args()

    rules = json.load(open(a.rules))
    pool = [l.split("#")[0].strip().upper() for l in open(a.pool)]
    pool = [p for p in dict.fromkeys(pool) if p]
    data, missing = {}, []
    for sym in pool:
        f = os.path.join(a.data, f"{sym}.csv")
        rows = load_csv(f) if os.path.exists(f) else []
        if len(rows) < 260:
            missing.append(sym); continue
        data[sym] = Series(rows)
    bench = Series(load_csv(os.path.join(a.data, f"{a.benchmark}.csv")))
    start, end = date.fromisoformat(a.start), date.fromisoformat(a.end)

    bt = Backtest(data, bench, rules, start, end, a.conviction, a.slippage, a.capital)
    days = bt.run()
    os.makedirs(a.out, exist_ok=True)

    sells = [t for t in bt.trades if t["side"] == "sell"]
    closed = {}
    for t in sells:  # group partial exits into round trips
        k = (t["symbol"], t["entry_date"]); closed[k] = closed.get(k, 0) + t["pnl"]
    wins = [v for v in closed.values() if v > 0]
    exit_reasons, block_reasons = {}, {}
    for t in sells:
        r = "take_profit" if t["reason"].startswith("take_profit") else t["reason"]
        exit_reasons[r] = exit_reasons.get(r, 0) + 1
    for _, _, b in bt.events:
        block_reasons[b] = block_reasons.get(b, 0) + 1
    per_sym = {}
    for (sym, _), v in closed.items():
        e = per_sym.setdefault(sym, dict(round_trips=0, pnl=0.0)); e["round_trips"] += 1; e["pnl"] += v

    bench_curve = [(d, bench.c[bench.idx[d]] / bench.c[bench.idx[days[0]]] * a.capital) for d in days]
    ew_curve = buy_hold(list(data.values()), days, a.capital)
    summary = dict(
        period=[days[0].isoformat(), days[-1].isoformat()], trading_days=len(days),
        pool_size=len(pool), symbols_with_data=len(data), missing_or_short=missing,
        conviction_assumed=a.conviction, slippage=a.slippage, capital=a.capital,
        strategy=stats(bt.equity), equal_weight_buy_hold=stats(ew_curve),
        benchmark={a.benchmark: stats(bench_curve)},
        round_trips=len(closed), win_rate=round(len(wins) / len(closed), 3) if closed else None,
        avg_win=round(statistics.mean(wins), 2) if wins else None,
        avg_loss=round(statistics.mean([v for v in closed.values() if v <= 0]), 2) if len(wins) < len(closed) else None,
        avg_hold_days=round(statistics.mean(t["hold_days"] for t in sells), 1) if sells else None,
        exit_reasons=exit_reasons, entry_decisions=block_reasons, loss_limit_halt_days=bt.halted_days,
        final_open_positions=sorted(bt.pos),
        per_symbol=dict(sorted(((k, dict(round_trips=v["round_trips"], pnl=round(v["pnl"], 2)))
                                for k, v in per_sym.items()), key=lambda kv: -kv[1]["pnl"])),
    )
    json.dump(summary, open(os.path.join(a.out, "summary.json"), "w"), indent=1)
    with open(os.path.join(a.out, "trades.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(bt.trades[0]) if bt.trades else ["date"]); w.writeheader(); w.writerows(bt.trades)
    with open(os.path.join(a.out, "equity.csv"), "w", newline="") as f:
        w = csv.writer(f); w.writerow(["date", "strategy", "equal_weight", a.benchmark])
        for (d, v), (_, e), (_, b) in zip(bt.equity, ew_curve, bench_curve):
            w.writerow([d.isoformat(), round(v, 2), round(e, 2), round(b, 2)])
    print(json.dumps({k: summary[k] for k in ("period", "strategy", "equal_weight_buy_hold", "benchmark",
                                               "round_trips", "win_rate", "exit_reasons")}, indent=1))


if __name__ == "__main__":
    main()
