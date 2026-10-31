# Quant 策略研究代码

本目录保留独立回测引擎，与仓库根目录的 FriesTrader 引擎并存，未将不同成交口径强行合并。所有命令先 `cd strategies/quant`。行情缓存、原始私人自选 CSV、下载的字幕及本地结果不随代码发布。

## 策略入口

| 文件 | 用途 |
| --- | --- |
| `watchlist_backtest.py` | 原股票池、自选池、合并池的 13 种规则对比；次日开盘执行 |
| `sector_b1.py` | 行业仓位限制、价格止损、盈转亏退出；含历史日内路径敏感性版本 |
| `b1_research.py` / `b1_time_exit.py` | KDJ J<12、MA60 过滤和持有 2/3 日检查实验 |
| `chang_research.py` / `chang_3221.py` | 45 周线超跌、均线突破与 322 分批卖出代理；见 `CHANG_3221_RULES.md` |
| `qbacktest.py` | Qullamaggie 突破/EP 日线代理，下面保留原使用说明 |
| `compare_stock_strategies.py` | 正股策略及盈利集中度对比 |
| `leveraged_research.py` / `korea_leveraged.py` | 历史杠杆产品实验；不是最新正股组合的一部分 |
| `scan_current_b1.py` | 固定 2026-10-27 完整日线筛选快照，不是实时盯盘服务 |
| `monitor/daily_monitor.py` | 每日盯盘：B1+MA60+第3天检查的买入候选、止损价、持仓卖出动作；由 `daily-monitor.yml` 每个交易日运行 |

### 最新合并池实验口径

`watchlist_backtest.py` 使用 `close_only`：收盘确认、下一交易日开盘成交。B1 为 KDJ(9,3,3) J<12，可叠加收盘高于 SMA60；持有第 2/3 个交易日（买入日为第 1 日）仍未盈利时安排下一开盘退出，已经盈利则不重复时间检查。阳线实体 `close/open-1 >= 5%`（超过 7% 也算）触发下个开盘卖出初始仓位的 1/3。

初始价格止损为 2ATR，收盘触发后下一开盘退出；曾在收盘盈利、之后整笔交易含已实现收益和费用的净盈亏转为非正也退出。不是盘中触价立即成交，跳空可能扩大亏损。行业买入上限 30%，被动超限下一开盘调整；单票最多 20%。手续费单边 0.1%，初始资金 10 万美元。期末强制收盘平仓是估值例外。

### 重建实验输入

```bash
python3 -m pip install -r requirements-data.txt
python3 fetch_chang_data.py
python3 fetch_chang_data.py --add-bigtech
python3 import_watchlist.py /path/to/your/watchlist.csv
python3 fetch_watchlist.py
python3 watchlist_backtest.py
```

自选 CSV 需要 `代码,市场,名称,所属行业` 列，沿用原导出格式。上述实验数据截止日期固定为 2026-09-15，股票池和覆盖数有快照断言；更换池子或日期需要同步检查脚本，不是通用动态配置界面。免费源可能变化，需核对复权、公司行动和缺失行情。当前池回溯有选择与幸存者偏差；不得将回测收益解释为稳定赚钱的证明。

## 早期 Qullamaggie-style daily backtest（独立成交口径）

一个只依赖 Python 标准库的日线回测器，包含两套可量化的代理规则：

- `breakout`：强势股紧密整理后的收盘突破。
- `ep`：跳空、放量的 Episodic Pivot（日线代理）。

原始方法使用 1/5/60 分钟 opening range。本项目只有日线数据，因此统一采用 **T 日收盘确认、T+1 日开盘成交**，避免用 T 日完整成交量和最低价进行早盘成交的前视偏差。它不是原策略的精确复制。

## 输入格式

一个 long-format CSV：

```csv
date,symbol,market,open,high,low,close,volume,catalyst
2024-01-02,AAPL,US,187.15,188.44,183.89,185.64,82488700,0
2024-01-02,600000,CN,6.61,6.67,6.58,6.63,23456789,0
```

必需字段是 `date,symbol,open,high,low,close,volume`。`market` 缺省为 `US`；`CN` 仓位在买入当天不执行止损，以近似 A 股 T+1。`catalyst` 可选。

## 运行

```bash
python3 qbacktest.py \
  --input data/prices.csv \
  --strategy both \
  --output-dir results
```

输出：

- `summary.json`：收益、回撤、胜率、平均 R 等。
- `trades.csv`：逐笔交易。
- `equity.csv`：每日净值。

主要参数可以通过 `python3 qbacktest.py --help` 查看。默认每笔风险 0.5%，单票最多 20%，单边手续费/滑点合计 10 bps。

## 下载样例数据

可选依赖：

```bash
python3 -m pip install -r requirements-data.txt
```

美股（Yahoo Finance）：

```bash
python3 fetch_data.py yfinance \
  --symbols AAPL,MSFT,NVDA \
  --start 2015-01-01 --end 2026-08-26 \
  --output data/us.csv
```

A 股（AKShare/东方财富接口）：

```bash
python3 fetch_data.py akshare \
  --symbols 000001,600000,300750 \
  --start 20150101 --end 20260826 \
  --output data/cn.csv
```

A 股免费备用源（BaoStock，无需 token）：

```bash
python3 fetch_data.py baostock \
  --symbols 000001,600000,300750 \
  --start 2015-01-01 --end 2026-08-26 \
  --output data/cn.csv
```

富途 OpenD（日线、前复权）：

```bash
python3 -m pip install -r requirements-futu.txt
python3 fetch_data.py futu \
  --symbols US.AAPL,US.NVDA,SH.600000,SZ.000001 \
  --start 2015-01-01 --end 2026-09-05 \
  --output data/futu.csv
```

运行前需要启动并登录 FutuOpenD，默认监听 `127.0.0.1:11111`。富途按最近 7 天请求过的不同标的数计算历史 K 线额度，因此应先下载到本地，再重复回测。

## Codex MCP 接入

`futu_backtest_mcp.py` 只提供三个本地工具：检查 OpenD、下载历史日线、运行回测。它不读取账户，也没有下单功能。

```bash
python3 -m venv .venv-futu
.venv-futu/bin/python -m pip install -r requirements-futu.txt
codex mcp add futu-backtest \
  --env FUTU_HOST=127.0.0.1 \
  --env FUTU_PORT=11111 \
  -- /absolute/path/to/fries-backtest/strategies/quant/.venv-futu/bin/python \
  /absolute/path/to/fries-backtest/strategies/quant/futu_backtest_mcp.py
```

注册后刷新 Codex 本地客户端。之后可以要求它把一组富途代码下载到 CSV，再直接运行 `breakout`、`ep` 或 `both` 回测。

两个文件可以合并后一起回测，但不要直接用“今天仍在上市的股票列表”回测十年前的全市场，否则会产生幸存者偏差。

## 当前量化定义

Breakout 默认要求：

- 整理前窗口存在至少 30% 的上涨；
- 最近 20 个交易日整理区间不宽于 25%；
- 后半段最低价不明显低于前半段；
- 收盘突破此前整理区间高点；
- `MA10 > MA20 > MA50` 且 MA20 上升；
- 当日成交量至少为此前 20 日均量的 1.5 倍。

EP 默认要求：

- 开盘相对前收跳空至少 10%；
- 当日成交量至少为此前 20 日均量的 3 倍；
- 前 63 日振幅不超过 50%，用于排除已经大幅上涨的股票；
- 收盘高于开盘。

两者均在下一交易日开盘买入，初始止损取“信号日低点”和“一倍 20 日 ADR 止损”中更紧者。第 3 个持有日收盘卖出一半并把止损抬到成本，余仓在收盘跌破 MA10（可改 MA20）后于下一开盘退出。

## 已知限制

- 日线无法还原 opening range，也无法知道同一根 K 线内高低价出现的先后顺序。
- A 股只近似了 T+1，尚未模拟涨跌停无法成交、停牌、印花税和除权事件。
- EP 的重大消息需要单独的 point-in-time 事件数据；没有 `catalyst` 时只是“跳空放量代理”。
- 免费接口通常缺少退市股和历史股票池。对当前股票列表做长期回测会产生幸存者偏差。
- 成交量过滤不代表一定能按开盘价成交；大仓位还需要加入参与率和冲击成本。

## 验证

无行情缓存的新克隆会跳过 2 项股票池数据集成检查，其余 37 项不需要联网。下载完整 59 只股票历史数据后可运行全部 39 项。

```bash
python3 -m unittest discover -s tests -v
```
