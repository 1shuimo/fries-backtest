# A 股与美股日线数据源选择

更新时间：2026-08-26。

## 快速试验

| 市场 | 项目 | 成本/认证 | 优点 | 主要问题 |
|---|---|---|---|---|
| A 股 + 美股 | [AKShare](https://github.com/akfamily/akshare) | 免费、免 token | 一个库可取两地日线；A 股支持前/后/不复权 | 聚合网页数据，上游页面变化时接口可能失效；不天然解决历史股票池与退市股 |
| A 股 | [BaoStock](https://github.com/zxygithub/baostock) | 免费、免 token | 日/周/月线、复权、交易日历；`query_all_stock(day=...)` 可按历史日期取证券列表 | 速度和字段有限；仍应审计退市、停牌、公司行动 |
| A 股 | [Tushare](https://github.com/waditu/tushare) | token；部分接口需要积分 | 日线、复权因子、上市/退市日期、交易日历等较完整 | 权限分层；批量下载前要确认积分和限频 |
| 美股 | [yfinance](https://github.com/ranaroussi/yfinance) | 免费、免 token | 原型最快，批量 OHLCV 简单 | Yahoo 非正式 SDK、仅限个人研究用途；退市股和历史股票池不完整，接口可能变化 |
| 美股 | [OpenBB](https://github.com/OpenBB-finance/OpenBB) | 取决于底层 provider | 统一接入 yfinance、Tiingo、FMP、Intrinio 等 | OpenBB 是适配层，不会自动改善底层数据质量 |

本仓库的 `fetch_data.py` 已接入 AKShare 和 yfinance，适合先验证代码和参数。

## 更严肃的全市场回测

Qullamaggie 策略专门寻找当时最强的股票，因此股票池偏差会非常严重。至少需要：

1. 每个交易日当时可交易的证券列表；
2. 已退市股票及其最后交易/退市收益；
3. 永久证券标识，避免 ticker 更名或复用；
4. 原始价格、拆股复权价格、分红复权价格和公司行动；
5. 停牌、涨跌停和成交量；
6. 如果限制在指数成分股，还需要 point-in-time 成分历史。

### 美股优先级

1. **CRSP/WRDS**：学术研究的标准选择，包含永久标识和退市收益，价格昂贵或依赖学校/机构权限。
2. **Sharadar/Nasdaq Data Link**：可获得日线、退市证券、公司行动和元数据；[这个 GitHub 清洗项目](https://github.com/tenicho/data-cleaning)展示了如何构建 survivorship-clean panel，但原始 Sharadar 数据仍需授权。
3. **EODHD、Massive/Polygon、Tiingo、FMP**：适合工程化拉取；购买前必须逐项确认 delisted coverage、corporate actions、历史 ticker 映射和本地存储许可。
4. **Quandl WIKI Prices 旧数据**：1962–2018，含部分退市股，适合历史研究但已经停止更新，不能单独覆盖近年。

### A 股优先级

1. **BaoStock**：免费起步首选，可按历史日期查询证券列表；先抽样核对退市股和复权。
2. **Tushare Pro**：需要更完整元数据、复权因子、上市/退市信息时更合适。
3. **Wind、CSMAR、同花顺 iFinD**：正式研究或资金规模较大时更稳妥，但属于商业数据。
4. **AKShare**：探索和交叉验证非常方便，不建议把单一网页接口直接当作不可审计的最终数据仓库。

## 建议路线

- 第一阶段：AKShare + yfinance，小股票池跑通策略、交易日志和参数敏感性。
- 第二阶段：A 股换 BaoStock/Tushare 历史股票池；美股换 Sharadar/CRSP 或确认包含退市股的商业源。
- 第三阶段：加入分钟 opening-range 数据、真实公司事件时间、涨跌停/停牌和交易成本，再判断策略是否有可交易优势。
