# Stock Watchtower

A股中线交易监控系统。盘前用 LLM 分析隔夜讯息 + 全球市场，盘中用规则引擎盯价格走势，有调仓信号时发邮件通知。

[![CI](https://github.com/ghostLLC/stock-watchtower/actions/workflows/ci.yml/badge.svg)](https://github.com/ghostLLC/stock-watchtower/actions/workflows/ci.yml)

## 运行模式

| 时段 | 频率 | 分析方式 |
|---|---|---|
| 盘前 09:15–09:25 | 每交易日一次 | 采集隔夜全球市场 + 财经新闻 + 个股公告 → LLM 综合分析 → 邮件 |
| 盘中 09:30–15:00 | 每 15 分钟 | 实时行情 → 规则引擎 + LLM 增强（每小时一次）→ 邮件 |
| 其他时间 | — | 直接退出 |

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置
cp .env.example .env          # 填入 LLM API 和 QQ 邮箱配置
# 编辑 config/portfolio.json   # 填入真实持仓
# 编辑 config/watchlist.json   # 填入观察池标的

# 3. 运行
python src/main.py              # 单次巡检（自动判断盘前/盘中模式）
python src/dashboard_server.py  # Web 控制面板 → http://127.0.0.1:8765
```

## 配置说明

三个 JSON 配置文件在 `config/` 目录下：

- **`portfolio.json`** — 持仓列表（code、name、board、sector、current_weight、target_weight、entry_date、thesis）
- **`watchlist.json`** — 观察池（code、name、board、sector、reason）
- **`strategy.json`** — 策略参数：
  - `check_interval_minutes` — 巡检间隔
  - `trading_session` — 盘前和盘中时段配置
  - `llm_analysis` — LLM 分析开关和冷却时间
  - `risk_rules` — 止损止盈阈值
  - `opportunity_rules` — 建仓/加仓条件
  - `portfolio_rules` — 仓位和集中度上限

## 规则引擎

盘中规则引擎按四阶段运行：

1. **组合风险检查** — 总仓位、科技集中度、板块集中度、单票上限超标时发出 rebalance/trim 信号
2. **持仓管理** — 止损（跌破 -8% 或 -3.5% 且低于 MA20）、止盈（月涨幅 ≥18% 硬止盈 / ≥12% 且回撤时分批止盈）、加仓（risk_on + 站上 MA20 + 趋势延续）
3. **建仓筛选** — 观察池中当日涨幅 ≥2.5% 且站上 MA20 且月涨幅为正的标的，按动量排序
4. **去重排序** — 同 (action, symbol) 仅保留一条，按紧急度排序

## 邮件信号去重

同一动作 + 同一标的在 180 分钟内不重复发信（可通过 `strategy.json` 中 `max_same_signal_cooldown_minutes` 调整）。

## 计划任务

推荐通过 Windows 任务计划程序配置三个触发器：

- `stock-watchtower-pre` — 工作日 09:15，执行 `run.bat`
- `stock-watchtower-am` — 工作日 09:30–11:31，每 15 分钟，执行 `run.bat`
- `stock-watchtower-pm` — 工作日 13:00–15:01，每 15 分钟，执行 `run.bat`

程序内部会再次判断时段，即使计划任务误触发也不会在非交易时段发信号。

## 技术架构

```
main.py
  ├─ scheduler.py            → 判断盘前/盘中/闭市
  ├─ fetchers/
  │   ├─ market_data.py      → 行情采集（akshare → 东方财富降级 → 日线缓存）
  │   ├─ news_fetcher.py     → 新闻采集 + 全球指数 + 龙虎榜匹配
  │   └─ capital_flow.py     → 北向资金流向
  ├─ analyzer.py             → 规则引擎 + LLM 分析
  ├─ notifier/
  │   ├─ signal_registry.py  → 信号去重 + 冷却期管理
  │   └─ emailer.py          → QQ 邮箱 SMTP（高紧急即时发送，中低紧急收盘汇总）
  ├─ dashboard_server.py     → Web 控制面板（127.0.0.1:8765）
  └─ tests/
      ├─ test_scheduler.py       → 时段判断测试
      ├─ test_analyzer.py        → 规则引擎测试
      ├─ test_config_loader.py   → 配置读写测试
      └─ test_signal_registry.py → 去重逻辑测试
```

## 开发

```bash
# 运行测试
python -m pytest tests/ -v

# 类型检查
mypy src/

# CI 在 push/PR 时自动执行以上两项
```

## 依赖

- `akshare` — A股行情和新闻数据
- `openai` — LLM 分析（OpenAI 兼容 API）
- `python-dotenv` — 环境变量加载
- `requests` — HTTP 请求
- `pydantic` — 数据校验

开发依赖：`pytest`、`mypy`

## 环境变量（.env）

| 变量 | 说明 |
|---|---|
| `MODEL_BASE_URL` | LLM API 地址 |
| `MODEL_API_KEY` | LLM API 密钥 |
| `MODEL_NAME` | 模型名称 |
| `SMTP_HOST` | SMTP 服务器 |
| `SMTP_PORT` | SMTP 端口 |
| `SMTP_USER` | 发件邮箱 |
| `SMTP_PASS` | 邮箱授权码 |
| `MAIL_TO` | 收件邮箱 |
