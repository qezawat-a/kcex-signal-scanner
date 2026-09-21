# KCEX Signal Scanner

Multi-timeframe futures signal scanner for KCEX USDT-M perpetuals.

## Features
- Scans KCEX futures (`/fapi/v1/contract/ticker`, `/fapi/v1/contract/kline/{symbol}?period=...`)
- User-selectable timeframes: `1m, 3m, 5m, 15m, 1h, 4h, 1d, 1w`
- 6 strategies: EMA trend, RSI momentum, MACD cross, volume confirmation, price momentum, ADX strength
- Multi-TF confirmation: `min_confidence`, `tf_min_confidence`, `min_agreeing_strategies`, `signal_scans_confirm`
- Reversal alarm, periodic status report, pause/resume
- Telegram alerts (optional) + Telegram control bot (`telegram_bot.py`) with auto `/` command menu

## Telegram control bot (mini-app style panel)
Run `telegram_bot.py` alongside (or instead of) the Actions schedule. It registers
a `/` command menu in Telegram automatically via `setMyCommands`:
`/status /scan /pause /resume /symbol /tfs /report /scanint /config /help`.
```bash
export TELEGRAM_BOT_TOKEN="123:ABC" TELEGRAM_CHAT_ID="123456789"  # or put them in .env
python3 telegram_bot.py
```
Then open your bot in Telegram, tap `/` and pick a command — results come back as messages.
Only your `TELEGRAM_CHAT_ID` can control it (others get "Not authorized").
`/scan` runs a live scan and the full report is pushed automatically too.
`/ask` talks to the AI agent (needs an LLM key, see below).

## AI Agent (CRAG-style, scanner tools only)
Same pattern as CRAG's `src/agent/` (loop + brain + tools + memory + skills + prompt),
minus everything XT/trading — tools here are scanner ops only:
`status, run_scan, set_symbol, set_timeframes, set_scan_interval, set_report_interval,
set_thresholds, load_skill` + `remember/recall`.
- `agent/providers.py` — multi-provider OpenAI-compatible chat (openai → anthropic → google priority, auto model resolve), env: `AI_API_KEY/AI_BASE_URL/AI_MODEL`, `ANTHROPIC_*`, `GEMINI_*`, or `NINEROUTER_URL/NINEROUTER_KEY`
- `agent/loop.py` — tool-calling loop (max 6 rounds)
- `agent/tools.py`, `agent/memory.py` (`data/memory.json`), `agent/skills.py` (`skills/*.md`), `agent/prompt.py` (SignalOps SOUL)
- `skills/` ships with `scalping`, `swing`, `risk` playbooks — add your own `.md`
```bash
cp .env.example .env   # fill AI_API_KEY etc.
python3 agent_cli.py --models      # check provider detection
python3 agent_cli.py --ask "status ro bego"
python3 agent_cli.py --chat        # REPL
```
Telegram: `/ask <sual>` (masalan `/ask bazar alan chetore?`), `/am <dastur>`, `/models`.
No auto-trade: the agent only analyzes and reports signals.

## Quick start
```bash
pip install -r requirements.txt
cp config.json config.local.json  # optional
python3 scanner.py --status
python3 scanner.py --start
```

## Commands
```bash
python3 scanner.py --start             # run continuous scanner
python3 scanner.py --start --once      # single scan cycle
python3 scanner.py --status            # show status
python3 scanner.py --pause             # pause scanning
python3 scanner.py --resume            # resume scanning
python3 scanner.py --set-symbol BTC_USDT
python3 scanner.py --set-timeframes 5m,15m,1h
```

## Config (`config.json`)
| Key | Default | Description |
|---|---|---|
| symbols | ALL | `ALL` or list like `["BTC_USDT","ETH_USDT"]` |
| timeframes | 1m,3m,5m,15m | any of `1m,3m,5m,15m,1h,4h,1d,1w` |
| scan_interval_sec | 10 | market scan interval |
| report_interval_sec | 30 | status report interval |
| min_confidence | 70 | global min confidence |
| tf_min_confidence | 60 | per-timeframe min confidence |
| min_agreeing_strategies | 3 | min agreeing strategies |
| signal_scans_confirm | 2 | consecutive confirmations |
| reversal_alarm | true | alert on direction flip |
| max_symbols | 30 | top-N by volume when symbols=ALL |
| telegram.enabled | false | set token/chat_id to enable |

## Notes
- No API key needed (public market data only).
- No auto-trading — signals only.
- Sandbox without DNS can't reach KCEX; run on a host with internet (VPS/Railway/GitHub Actions).

## Railway deploy (recommended: scanner + bot + agent, one service)

> ⚠️ **Avoid a split brain.** The scanner must have exactly ONE writer. If the
> Railway service is running the scan loop, turn OFF the GitHub Actions schedule
> (`.github/workflows/scan.yml`), otherwise each `*/5` run is a second, separate
> scanner writing its own counters. Delete the `schedule:` block or the workflow.

1. Push repo → Railway → **New Project → Deploy from GitHub repo** → pick `kcex-signal-scanner`.
   Start command is automatic (`railway.toml` → `python3 supervisor.py`, which runs the
   scanner loop + Telegram bot + `/ask` agent in one process).
2. **Variables** tab → add:
   - `TELEGRAM_BOT_TOKEN` = token az `@BotFather`
   - `TELEGRAM_CHAT_ID` = chat id adadi
   - `DATABASE_URL` = Neon connection string (recommended — see below)
   - `AI_API_KEY` (+ optional `AI_BASE_URL`, `AI_MODEL`) — ya `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` / `NINEROUTER_URL`+`NINEROUTER_KEY`
3. **Redeploy**. Logs bayad neshun bede:
   `supervisor: starting telegram bot (owner ...)` + `KCEX Signal Scanner started...`
4. To Telegram `/status` bezan — age javab dad, hame chi vasle.

### Storage / persistence (Neon Postgres)

Settings (`/set_symbol`, thresholds), scan counters o agent memory dar **Postgres**
zakhire mishan, age `DATABASE_URL` set bashe:

1. Neon → **Create project** → connection string ro copy kon (ba `?sslmode=require`).
2. Railway → Variables → `DATABASE_URL` = oon string → redeploy.

Az oon be ba'd:
- `/set_symbol` o settings bad az **har redeploy/restart** mimunan (digar `scans=1` nemibini).
- Har writer (Railway + Actions, age her do ra dashte bashi) hamoon state ro mibine.

Bedun `DATABASE_URL`, app mesle ghabl file-based kar mikone (`config.json` / `state.json` /
`data/memory.json`) — pas local o GitHub Actions bedun DB ham kar mikonan.

`POSTGRES_URL` o `NEON_DATABASE_URL` ham ghabul mishan. Schema khodesh sakhte mishe
(table `kv`: `store`, `key`, `value JSONB`) — migration niaz nist.
