# Stock Watchtower

A股中线交易监控系统。盘前用 LLM 分析隔夜讯息 + 全球市场，盘中用规则引擎盯价格走势，有调仓信号时发邮件通知。

[![CI](https://github.com/ghostLLC/StockWatchtower/actions/workflows/ci.yml/badge.svg)](https://github.com/ghostLLC/StockWatchtower/actions/workflows/ci.yml)

## 运行模式

| 时段 | 频率 | 分析方式 |
|---|---|---|
| 盘前 09:15–09:25 | 每交易日一次 | 采集隔夜全球市场 + 财经新闻 + 个股公告 + 龙虎榜 → LLM 综合分析 → 邮件 |
| 盘中 09:30–15:00 | 每 15 分钟 | 实时行情 → 规则引擎 + LLM 增强（每小时一次）→ 邮件 |
| 其他时间 | — | 直接退出 |

## 快速开始

```bash
# 1. 克隆仓库
git clone https://github.com/ghostLLC/StockWatchtower.git
cd StockWatchtower

# 2. 创建虚拟环境并安装依赖
python -m venv .venv
source .venv/Scripts/activate  # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
pip install mypy pytest         # 开发依赖

# 3. 验证开发环境
mypy src/                        # 类型检查
python -m pytest tests/ -v       # 运行测试（134 个）

# 4. 配置
cp .env.example .env             # 填入 LLM API 和 QQ 邮箱配置
# 编辑 config/portfolio.json     # 填入真实持仓
# 编辑 config/watchlist.json     # 填入观察池标的

# 5. 运行
python src/main.py               # 单次巡检（自动判断盘前/盘中模式）
python src/dashboard_server.py   # Web 控制面板 → http://127.0.0.1:8765
```

> 换机器开发只需前三步（clone + venv + pip install + 验证），配置文件不提交在仓库里，从 example 文件手写或从原机器拷贝即可。

## 配置说明

三个 JSON 配置文件在 `config/` 目录下：

- **`portfolio.json`** — 持仓列表（code、name、market、board、sector、current_weight、target_weight、entry_date、thesis）
- **`watchlist.json`** — 观察池（code、name、board、sector、reason）
- **`strategy.json`** — 策略参数，所有字段均被代码消费：

| 配置段 | 字段 | 作用 |
|---|---|---|
| `trading_session` | `weekdays`, `sessions`, `pre_market_sessions` | 调度器读取的交易时段，缺失则使用 A 股默认时段 |
| `llm_analysis` | `enabled`, `in_session_enabled`, `cooldown`, `temperature` | LLM 分析开关与参数 |
| `monitoring` | `north_flow_enabled`, `dragon_tiger_enabled` | 控制北向资金和龙虎榜数据采集 |
| `alert_rules` | `intraday_crash_pct`, `volume_spike_ratio` | 日内急跌和异常放量告警阈值 |
| `risk_rules` | `position_breakdown_pct`, `stop_loss_pct`, `take_profit_pct` | 止损止盈阈值 |
| `opportunity_rules` | `watchlist_breakout_pct`, `max_new_buy_weight`, `max_add_weight` | 建仓/加仓条件 |
| `portfolio_rules` | `max_total_weight`, `max_single_position_weight`, `max_position_count`, `max_tech_weight`, `max_sector_weight` | 仓位与集中度上限 |
| — | `boards_allowed`, `markets_allowed` | 过滤持仓/观察池的板块与市场 |
| — | `send_only_on_action` | 为 true 时仅高紧急信号即时发送，false 时所有信号即时发送 |
| — | `max_same_signal_cooldown_minutes` | 同信号冷却时间（默认 180 分钟） |

## 规则引擎

盘中规则引擎按四阶段运行：

1. **标的过滤** — 按 `boards_allowed` / `markets_allowed` 过滤持仓和观察池
2. **组合风险检查** — 总仓位、科技集中度、板块集中度、单票上限超标时发出 rebalance/trim 信号
3. **持仓管理** — 止损（跌破 -8% 或 -3.5% 且低于 MA20）、止盈（月涨幅 ≥18% 硬止盈 / ≥12% 且回撤时分批止盈）、加仓（risk_on + 站上 MA20 + 趋势延续）。价区根据操作类型调整：买入/加仓取 0.97–1.01，卖出/止盈取 0.985–1.03
4. **建仓筛选** — 观察池中当日涨幅 ≥2.5% 且站上 MA20 且月涨幅为正的标的，按动量排序
5. **去重排序** — 同 (action, symbol) 仅保留一条，按紧急度排序

## 邮件信号去重与分级

同一动作 + 同一标的在冷却期内不重复发信（可通过 `strategy.json` 调整）。去重注册表使用 PID+时间戳文件锁，stale lock 会检测进程存活状态后再打破，防止并发写入损坏数据。

高紧急信号即时发送；中低紧急信号汇集到每日摘要，在收盘窗口（11:25–11:30、14:55–15:00）合并为单封邮件发送。

## 监控告警

除规则引擎外，还包含专项监控：

- **日内急跌告警** — 持仓盘中跌幅超过阈值（默认 -5%）立即触发高优先级邮件
- **异常放量检测** — 基于 20 日均成交额计算量比，≥3 倍放量发出方向性告警
- **北向资金** — 通过沪深股通净买卖数据研判当日外资流向（大幅流入/流出/均衡）。可由 `monitoring.north_flow_enabled` 控制开关
- **龙虎榜匹配** — 每日龙虎榜与持仓+自选交叉比对，识别机构异动。可由 `monitoring.dragon_tiger_enabled` 控制开关

## 计划任务

推荐通过 Windows 任务计划程序配置三个触发器：

- `stock-watchtower-pre` — 工作日 09:15，执行 `run.bat`
- `stock-watchtower-am` — 工作日 09:30–11:31，每 15 分钟，执行 `run.bat`
- `stock-watchtower-pm` — 工作日 13:00–15:01，每 15 分钟，执行 `run.bat`

程序内部会再次判断时段，即使计划任务误触发也不会在非交易时段发信号。三个任务均可通过 Web 控制面板管理（启用/禁用/立即运行）。

## 技术架构

```
src/
  ├─ main.py                    → 入口 + 流程编排
  ├─ logging_setup.py           → 日志配置（文件轮转 + 控制台）
  ├─ scheduler.py               → 判断盘前/盘中/闭市（读取 strategy.json 交易时段配置）
  ├─ config_loader.py           → 配置读写 + 校验（持仓/观察池/策略）
  ├─ fetchers/
  │   ├─ market_data.py         → 行情采集（并发 eastmoney API 为主，akshare 批量兜底）
  │   ├─ news_fetcher.py        → 新闻 + 全球指数 + 龙虎榜（统一重试机制）
  │   └─ capital_flow.py        → 北向资金流向（统一重试机制）
  ├─ analyzer.py                → 规则引擎 + LLM 分析（操作感知价区、标的过滤）
  ├─ notifier/
  │   ├─ signal_registry.py     → 信号去重 + PID 文件锁
  │   └─ emailer.py             → QQ 邮箱 SMTP（即时 + 收盘汇总）
  └─ dashboard_server.py        → Web 控制面板（含 Token 鉴权、三任务管理）
```

## 开发

```bash
# 首次设置
pip install pytest mypy pre-commit pytest-cov
pre-commit install

# 运行全部测试
python -m pytest tests/ -v

# 运行单个测试文件
python -m pytest tests/test_analyzer.py -v

# 覆盖率报告
python -m pytest tests/ --cov=src --cov-report=term-missing

# 类型检查
mypy src/

# CI 在 push/PR 时自动执行 mypy + pytest
```

当前测试覆盖：134 个测试，7 个测试文件，覆盖 analyzer、config_loader、scheduler、signal_registry、market_data、capital_flow、news_fetcher。

## 依赖

- `akshare` — A股行情和新闻数据
- `openai` — LLM 分析（OpenAI 兼容 API）
- `python-dotenv` — 环境变量加载
- `requests` — HTTP 请求
- `pydantic` — 数据校验

开发依赖：`pytest`、`mypy`、`pre-commit`、`pytest-cov`

## Docker

```bash
# 构建镜像
docker compose build

# 运行单次巡检
docker compose run --rm watchtower

# 启动控制面板
docker compose up -d dashboard
# 访问 http://127.0.0.1:8765?token=<DASHBOARD_TOKEN>

# 停止
docker compose down
```

> Windows 计划任务（schtasks）在容器中不可用。Docker 方式推荐在 Linux 服务器或 macOS 上使用，通过宿主机 cron 定时触发。

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
| `DASHBOARD_TOKEN` | 控制面板访问令牌（可选，留空则跳过鉴权） |
