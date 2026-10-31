# 每日盯盘（B1 + MA60 + 第3天检查）

回测里表现最好的那一版（2026 年至今 +21.8%，回撤 −9.6%，见 `docs/RESULTS.md`）的**每日执行清单**。
只读行情、只出提醒，**不下单**。你自己在富途下单。

每个美股交易日收盘后（北京时间早上约 6:15），GitHub Actions 会：
1. 下载 `strategies/friestrader/pool.txt`（你的富途自选）全部日线；
2. 运行 `daily_monitor.py`；
3. 把报告写进 `monitor/reports/日期.md`，并通过 `tools/notify.py` 推送给你（见下）。
另有一个 Claude 定时任务在北京时间早上读这份报告，在 Claude 里推送一份中文摘要。

## 通知渠道（任选一个或多个，在 GitHub 仓库 Settings → Secrets and variables → Actions → New repository secret 添加）

| 渠道 | 需要添加的 Secret | 怎么拿 |
|---|---|---|
| 微信（Server酱） | `SERVERCHAN_KEY` | https://sct.ftqq.com 微信扫码登录 → SendKey |
| 微信（PushPlus） | `PUSHPLUS_TOKEN` | https://www.pushplus.plus 微信扫码登录 → 一对一推送 token |
| iPhone 推送（Bark） | `BARK_URL` | App Store 装 Bark → 复制 `https://api.day.app/xxxx` |
| 邮件 | `SMTP_HOST` `SMTP_USER` `SMTP_PASS` `MAIL_TO`（可选 `SMTP_PORT`，默认 465） | QQ 邮箱：`smtp.qq.com`，设置→账户→开启 SMTP 得到授权码填 `SMTP_PASS`；Gmail：`smtp.gmail.com` + 应用专用密码 |
| Telegram | `TELEGRAM_BOT_TOKEN` `TELEGRAM_CHAT_ID` | @BotFather 建 bot |

微信/邮件收到完整报告；Bark 收到一行摘要（点开跳到完整报告）。一个渠道失败不影响其他渠道。
配好后在 Actions → daily-monitor → Run workflow 手动跑一次（asof 填最近交易日）即可测试。

## 你需要维护的两个文件

**`config.json`**：`account_equity_usd` 改成你账户总净值（美元），用来算股数。

**`positions.csv`**：买入/卖出后更新（GitHub 网页或手机 App 直接编辑即可）。

| 列 | 含义 |
|---|---|
| symbol | 代码，如 NVDA |
| entry_date | 实际买入日期 YYYY-MM-DD |
| entry_price | 实际买入均价 |
| initial_qty | 最初买入股数（分批卖出按它的 1/3 算） |
| qty | 当前剩余股数 |
| sold_proceeds | 已卖出部分累计到手金额（可留空，留空则按规则卖出价估算） |
| stop | 可留空＝按规则自动算；填了就用你填的止损价 |

清仓后把那一行删掉。

## 规则（与回测一致）

**买入**：收盘 KDJ(9,3,3) 的 J < 12，且收盘价 > 60 日均线 → **次日开盘买入**。已持有的不加仓。
- 股数 = min(净值×0.5% ÷ 止损距离，净值×20% ÷ 价格，可用现金，行业剩余额度)。即止损打掉最多亏净值 0.5%。
- 同一行业持仓合计不超过净值 30%。

**止损**（富途止损单，盘中触价即卖，设好后固定不动）：
- 上涨趋势的前一个低点：信号日之前最近一个波段低点（左右各 3 根 K 线里最低），且低于信号日最低价；
  它在买入价下方 **10% 以内**就用它。
- 否则（前低超过 10% 或近 60 日没有）→ 用**买入当天 K 线最低价**，收盘后挂单；此时股数按信号日振幅估算。
- 开盘跳空到止损价下方按开盘价成交。

**其他卖出**（收盘确认 → 次日开盘卖出）：
- 保本：只要某天收盘整笔交易（含已卖部分、含费用）是赚钱的，之后任何一天收盘整笔转为不赚钱 → 全卖。
- 第3天检查：买入当天算第 1 天，第 3 天收盘整笔仍不赚钱 → 全卖。
- 止盈：大阳线 收盘/开盘 − 1 ≥ 5% → 卖出**最初股数的 1/3**；第二根卖到 2/3，第三根清仓。

**回测对比**（`python3 monitor/backtest_stops.py`，pool.txt 162 只个股，2026-01 至 10-30）：

| 止损 | 收益 | 最大回撤 | 交易 | 胜率 | 止损出局 |
|---|---:|---:|---:|---:|---:|
| 原版：买入价−2ATR，收盘确认 | +19.7% | −14.2% | 479 | 30% | 18 |
| 现用：前低（≤10%）否则买入日低点，盘中止损 | **+23.2%** | **−13.2%** | 474 | 21% | 305 |

前低止损约占 46% 的信号，止损幅度中位数 4.8%。胜率更低、止损次数多很多，但单笔亏损小，
仓位相应更大，净效果略好。差距只有约 3.5 个百分点，样本只有 10 个月，不应理解为显著更优。

## 本地运行

```bash
cd strategies/quant
python3 monitor/daily_monitor.py              # 用 ../../data/ 最新一根完整日线
python3 monitor/daily_monitor.py --asof 2026-10-27
```

## 局限

- 回测只有 2026 年 10 个月、股票池事后挑选，结果不代表未来，不是投资建议。
- 止损单遇跳空低开会按开盘价成交，亏损可能超过 0.5%。日线无法还原盘中先碰止损还是先冲高。
- 建议股数按当日收盘价估算，实际以开盘价为准。买入日低点止损的股数是估算，当天振幅大时风险会超过 0.5%。
