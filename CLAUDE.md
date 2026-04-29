# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

Stock Watchtower is an A-share (Chinese stock market) 5-minute monitoring system. It runs during trading hours (09:30–11:30, 13:00–15:00 on weekdays), fetches real-time and daily data for portfolio positions and watchlist stocks, evaluates them against configurable risk/opportunity rules, and emails trading signals. A web dashboard provides control over configs, scheduled tasks, and ad-hoc runs.

## Commands

```bash
# Activate virtual environment (created at .venv/)
source .venv/Scripts/activate

# Install dependencies
pip install -r requirements.txt

# Dev setup (all at once)
pip install -r requirements.txt mypy pytest pre-commit && pre-commit install

# Run a single inspection cycle (the core pipeline)
python src/main.py

# Start the web dashboard (serves on 127.0.0.1:8765)
python src/dashboard_server.py

# Run all tests
python -m pytest tests/ -v

# Run a single test file
python -m pytest tests/test_analyzer.py -v

# Type check
mypy src/

# Coverage report
python -m pytest tests/ --cov=src --cov-report=term-missing
```

## Architecture

**Pipeline** (each run of `src/main.py`):

1. `scheduler.py` checks whether the current time falls within an A-share trading session. Reads `trading_session` config from `strategy.json` (weekdays, sessions, pre_market_sessions); falls back to hardcoded A-share defaults when the config is absent. Returns `pre_market`, `in_session`, or `closed`. If closed, exits early — no data fetched, no email sent.

2. `fetchers/market_data.py` gathers a market snapshot:
   - Three major indexes (SH, SZ, ChiNext) via akshare, falling back to eastmoney push API.
   - Realtime quotes for every symbol in the portfolio + watchlist: per-symbol eastmoney API with `ThreadPoolExecutor` (max 10 workers) is the **primary** path; bulk `ak.stock_zh_a_spot_em()` is the **fallback**.
   - Recent daily data (30 bars) per symbol via `ak.stock_zh_a_hist` (qfq-adjusted), cached to `data/cache/daily_{symbol}.csv`. On fetch failure, cached CSV is reused; if no cache exists, the symbol proceeds with realtime-only data.
   - Market state derived from index changes: `risk_off` (SH ≤ -1.5% or ChiNext ≤ -2.0%), `risk_on` (SH ≥ 0.8% or ChiNext ≥ 1.2%), else `neutral`.
   - Snapshot dicts include `market`, `board`, `sector` fields propagated from config, enabling `boards_allowed`/`markets_allowed` filtering downstream.

3. `analyzer.py` is a **pure rule engine** (no LLM call). Runs four phases in order:
   - **Portfolio risk checks** — total weight, tech concentration, per-sector concentration, per-position weight against limits from `strategy.json → portfolio_rules`.
   - **Position management** — stop-loss (position breakdown at -8%, or -3.5% when below MA20), hard take-profit (≥18% monthly gain with MA20 advantage narrowing), trailing take-profit (≥12% monthly gain + intraday pullback), add-on signals (risk_on + strong MA20 position + good momentum).
   - **Opening candidates** — scans watchlist for items with `change_pct ≥ 2.5%`, above MA20, positive monthly change. Sorted by composite momentum.
   - **Deduplication** — per `(action, symbol)` pair, keeping only one decision. Results sorted by urgency.
   - Price zones are action-aware: buy/add uses 0.97–1.01, sell/reduce/take_profit uses 0.985–1.03.

4. `notifier/signal_registry.py` enforces per-`(action, symbol)` cooldown (default 180 min) via a JSON file at `data/signals/registry.json`. Uses a PID+timestamp file lock (`_FileLock`) with stale lock detection via platform process-liveness check. Old records (>30 days) pruned on every check.

5. `notifier/emailer.py` sends via QQ SMTP (SSL on port 465) using credentials from `.env`.

**Dashboard** (`src/dashboard_server.py`):
- `ThreadingHTTPServer` on `127.0.0.1:8765`.
- `GET /` → serves `web/dashboard.html` (single-page vanilla JS app).
- `GET /api/state` → returns all three configs, scheduled-task status (via `schtasks /Query`), recent reports, and whether `.env` exists.
- `POST /api/configs` → overwrites `config/portfolio.json`, `config/watchlist.json`, `config/strategy.json` atomically.
- `POST /api/run-once` → spawns `python src/main.py` as a subprocess (240s timeout).
- `POST /api/task-action` → enables, disables, or runs Windows scheduled tasks (`stock-watchtower-pre`, `stock-watchtower-am`, `stock-watchtower-pm`) via `schtasks /Change` or `schtasks /Run`.
- Token auth via `?token=` query parameter when `DASHBOARD_TOKEN` is set in `.env`.

## Key conventions

- **Configs** are plain JSON files under `config/`. `config_loader.py` resolves `BASE_DIR` as the parent of `src/` and provides `load_json()` / `save_json()` helpers. Validation functions (`validate_portfolio`, `validate_watchlist`, `validate_strategy`) return lists of issue strings. `validate_portfolio` validates both `current_weight` (canonical) and `weight` (legacy fallback), warning when they differ.
- **`.env`** at the repo root is loaded by `python-dotenv` in `bootstrap_environment()`. Holds the OpenAI-compatible API endpoint, QQ SMTP credentials, recipient address, and optional `DASHBOARD_TOKEN`.
- **Stock codes** use the `CODE.EXCHANGE` format (e.g. `688981.SH`, `000001.SZ`). Fetchers translate between this and akshare/eastmoney conventions.
- **Weights** are always floats in `[0, 1]`, formatted as percentages only at display time (`_fmt_weight`).
- **Windows dependency**: the dashboard's task management calls `schtasks.exe`. Task names: `stock-watchtower-pre` (09:15), `stock-watchtower-am` (09:30–11:31), `stock-watchtower-pm` (13:00–15:01). These are Windows-only.
- **`src/` is on `sys.path` implicitly** when running `python src/main.py` from the repo root. Python adds the script's directory to `sys.path`. Tests use `conftest.py` to add `src/` to `sys.path` explicitly.

## Strategy config fields

All fields in `config/strategy.json` are now consumed by the code:

| Field | Used by |
|---|---|
| `trading_session` | `scheduler.py` — weekdays, sessions, pre_market_sessions |
| `monitoring.north_flow_enabled` | `main.py` — gates north flow fetch (default true) |
| `monitoring.dragon_tiger_enabled` | `main.py` — gates dragon tiger fetch (default true) |
| `boards_allowed` / `markets_allowed` | `analyzer.py` — filters positions/watchlist in `evaluate_market_snapshot()` |
| `send_only_on_action` | `main.py` — when true, only high urgency sends immediately; when false, all allowed signals send immediately |
| `llm_analysis.*` | `analyzer.py` / `main.py` — LLM enable/disable, cooldown, temperature |
| `risk_rules.*` / `opportunity_rules.*` / `portfolio_rules.*` | `analyzer.py` — rule engine thresholds |
| `alert_rules.*` | `analyzer.py` — intraday crash and volume spike alerts |
| `max_same_signal_cooldown_minutes` | `main.py` — signal dedup cooldown |

## Test suite

134 tests across 7 files: `test_analyzer.py`, `test_config_loader.py`, `test_scheduler.py`, `test_signal_registry.py`, `test_fetchers_market_data.py`, `test_fetchers_capital_flow.py`, `test_fetchers_news_fetcher.py`.

CI runs `mypy src/` and `pytest tests/ -v` on push/PR to `main` (Python 3.11, ubuntu-latest).
