# CLAUDE.md

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:

- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:

- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:

- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:

- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:

```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

---

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

Stock Watchtower is an A-share (Chinese stock market) 5-minute monitoring system. It runs during trading hours (09:30–11:30, 13:00–15:00 on weekdays), fetches real-time and daily data for portfolio positions and watchlist stocks, evaluates them against configurable risk/opportunity rules, and emails trading signals. A web dashboard provides control over configs, scheduled tasks, and ad-hoc runs.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run a single inspection cycle (the core pipeline)
python src/main.py

# Start the web dashboard (serves on 127.0.0.1:8765)
python src/dashboard_server.py

# Windows scheduled-task wrappers (called by Task Scheduler)
run.bat              # → python src/main.py
run_dashboard.bat    # → python src/dashboard_server.py
```

There is no test suite, no type checker, and no linter configured.

## Architecture

**Pipeline** (each run of `src/main.py`):

1. `scheduler.py` checks whether the current time falls within an A-share trading session. If not, the run exits early with a "not open" report — no data is fetched, no email is sent.
2. `fetchers/market_data.py` gathers a market snapshot:
   - Three major indexes (SH, SZ, ChiNext) via akshare, falling back to eastmoney push API.
   - Realtime quotes for every symbol in the portfolio + watchlist via `ak.stock_zh_a_spot_em`, falling back to per-symbol eastmoney HTTP calls.
   - Recent daily data (30 bars) per symbol via `ak.stock_zh_a_hist` (qfq-adjusted), cached to `data/cache/daily_{symbol}.csv`. On fetch failure, the cached CSV is reused; if no cache exists, the symbol proceeds with realtime-only data rather than failing.
   - Market state is derived from index changes: `risk_off` (SH ≤ -1.5% or ChiNext ≤ -2.0%), `risk_on` (SH ≥ 0.8% or ChiNext ≥ 1.2%), else `neutral`.
3. `analyzer.py` is a **pure rule engine** (no LLM call). It runs four phases in order:
   - **Portfolio risk checks** — total weight, tech concentration, per-sector concentration, per-position weight against limits from `strategy.json → portfolio_rules`.
   - **Position management** — stop-loss (position breakdown at -8%, or -3.5% when below MA20), hard take-profit (≥18% monthly gain with MA20 advantage narrowing), trailing take-profit (≥12% monthly gain + intraday pullback), and add-on signals (risk_on + strong MA20 position + good momentum).
   - **Opening candidates** — scans the watchlist for items with `change_pct ≥ 2.5%`, above MA20, positive monthly change. Sorted by composite momentum. Respects remaining capacity, position-count cap, and sector-concentration cap.
   - **Deduplication** — per `(action, symbol)` pair, keeping only one decision. Results are sorted by urgency (high → medium → low).
4. `notifier/signal_registry.py` enforces a per-`(action, symbol)` cooldown (default 180 min) via a JSON file at `data/signals/registry.json`. Old records (>30 days) are pruned on every check.
5. `notifier/emailer.py` sends via QQ SMTP (SSL on port 465) using credentials from `.env`.

**Dashboard** (`src/dashboard_server.py`):
- `ThreadingHTTPServer` on `127.0.0.1:8765`.
- `GET /` → serves `web/dashboard.html` (a single-page vanilla JS app).
- `GET /api/state` → returns all three configs, scheduled-task status (via `schtasks /Query`), recent reports list, and whether `.env` exists.
- `POST /api/configs` → overwrites `config/portfolio.json`, `config/watchlist.json`, `config/strategy.json` atomically.
- `POST /api/run-once` → spawns `python src/main.py` as a subprocess (240s timeout).
- `POST /api/task-action` → enables, disables, or runs one of the two Windows scheduled tasks (`stock-watchtower-am`, `stock-watchtower-pm`) via `schtasks /Change` or `schtasks /Run`.
- No authentication of any kind.

## Key conventions

- **Configs** are plain JSON files under `config/`. `config_loader.py` resolves `BASE_DIR` as the parent of `src/` and provides `load_json()` / `save_json()` helpers. The three runtime configs (`portfolio.json`, `watchlist.json`, `strategy.json`) are loaded on every run — there is no hot-reload or caching.
- **`.env`** at the repo root is loaded by `python-dotenv` in `bootstrap_environment()`. It holds the OpenAI-compatible API endpoint (not currently called), QQ SMTP credentials, and recipient address.
- **Stock codes** use the `CODE.EXCHANGE` format internally (e.g. `688981.SH`, `000001.SZ`). The fetchers translate between this and akshare/eastmoney conventions as needed.
- **Weights** are always floats in `[0, 1]`, formatted as percentages only at display time (`_fmt_weight`).
- **Windows dependency**: the dashboard's task management calls `schtasks.exe` and assumes two tasks named `stock-watchtower-am` and `stock-watchtower-pm` exist. These are Windows-only and will fail on other platforms.
- **`src/` is on `sys.path` implicitly** when running `python src/main.py` from the repo root. Imports like `from analyzer import ...` work because Python adds the script's directory to `sys.path`. Running from a different working directory will break imports.

## Notable gaps

- **`.env` is tracked in git** and the initial commit contains real API keys and SMTP credentials. These should be rotated and the file removed from tracking (`git rm --cached .env`).
- **`openai` is listed in `requirements.txt` but never imported.** The analyzer is entirely rule-based; the LLM integration was planned but not implemented.
- There are **no tests**, no CI, and no type-checking configuration.
- The dashboard has **no authentication**. It binds to localhost, which is safe for single-user machines but should not be exposed to a network.
