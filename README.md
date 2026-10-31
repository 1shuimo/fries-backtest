# fries-backtest

个人量化研究仓库：两套互相独立的回测引擎，共用一份日线数据缓存，可选 Yahoo（云端自动下载）或富途 OpenD（本地下载）作为数据源。**只做回测和只读行情，没有任何下单代码。**

## 目录

```
.
├── data/                     # 共用日线缓存（Yahoo，GitHub Actions 自动下载并提交），每只股票一个 CSV
├── data_futu/                # 富途日线缓存（本地生成，不提交）
├── tools/
│   ├── fetch_yahoo.py        # Yahoo → data/（云端用，Actions 自动跑）
│   └── fetch_futu.py         # 富途 OpenD → data_futu/（本地用，只读行情）
├── strategies/
│   ├── friestrader/          # FriesTrader 机械规则回测（Claude 写）
│   │   ├── backtest.py  rules.json  pool.txt
│   │   └── results/          # Actions 跑完自动提交
│   └── quant/                # B1/KDJ、322、Qullamaggie 等研究引擎（Codex 写，见其 README）
│       ├── run_ytd_yahoo.py  # 用共用缓存重跑 2026 年 B1 系列策略
│       ├── ytd_experiments.py# 7 月 / 10 月回撤的改进实验
│       └── monitor/          # 每日盯盘：B1+MA60+第3天检查的买入/止损/卖出提醒（见其 README）
├── tests/                    # FriesTrader 引擎测试；quant 的测试在 strategies/quant/tests
├── docs/RESULTS.md           # 已完成回测的结论汇总
└── .github/workflows/        # backtest.yml（回测）、daily-monitor.yml（每个交易日收盘后盯盘并推送）
```

两套引擎的成交口径、仓位规则都不同，**结果不能直接互相比较或相加**。

## 每日盯盘提醒

`strategies/quant/monitor/`：每个美股交易日收盘后，Actions 自动扫描 `pool.txt`，按 B1+MA60+第3天检查 的规则
列出「明早可买 + 建议股数 + 止损价（前低≤10%，否则买入日低点）」和「持仓明早该卖多少」，写入 `monitor/reports/` 并推送到微信 / iPhone / 邮件（配置见 `monitor/README.md`）。
买卖后在 `monitor/positions.csv` 更新持仓、在 `monitor/config.json` 填账户净值。**只提醒，不下单。**

## 常用命令

```bash
# 测试（无需联网）
python3 -m unittest discover -s tests
(cd strategies/quant && python3 -m unittest discover -s tests)

# FriesTrader：改 pool.txt / rules.json 后 push，Actions 自动下载数据、回测、提交结果
# 或本地运行（需已有 data/）：
python3 strategies/friestrader/backtest.py --start 2026-01-01

# B1 系列 2026 年至今（默认用 data/，可换成富途数据）
cd strategies/quant
python3 run_ytd_yahoo.py --cutoff 2026-10-27
python3 run_ytd_yahoo.py --cutoff 2026-10-27 --data ../../data_futu
python3 ytd_experiments.py
```

## 接入本地富途牛牛（只读行情）

需要在**运行富途 OpenD 的那台电脑上**执行：

1. 下载安装 [FutuOpenD](https://www.futunn.com/download/OpenAPI)，用你的富途账号登录，保持运行（默认监听 `127.0.0.1:11111`）。
2. `python3 -m pip install futu-api`
3. `python3 tools/fetch_futu.py --check` 确认连通。
4. `python3 tools/fetch_futu.py` 下载整个股票池日线到 `data_futu/`（前复权）。
5. 用 `--data ../../data_futu` 跑任意策略，或和 Yahoo 版本对比。

富途按最近 30 天请求过的不同标的数计算历史 K 线额度，下载一次后反复使用本地缓存即可。
`strategies/quant/futu_backtest_mcp.py` 另提供同样只读的 MCP 服务（检查 OpenD、下载日线、跑回测），可接入 Codex 或 Claude Code。

## 局限

- 股票池是今天选出来的，回测有严重的事后选择和幸存者偏差。
- 日线无法还原盘中成交顺序；未模拟停牌、盘口深度、极端滑点。
- 回测收益不代表未来，也不是投资建议。
