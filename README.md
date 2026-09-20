# KCEX Signal Scanner

Multi-timeframe futures signal scanner for KCEX USDT-M perpetuals.

## Features
- Scans KCEX futures (`/fapi/v1/contract/ticker`, `/fapi/v1/contract/kline/{symbol}?period=...`)
- User-selectable timeframes: `1m, 3m, 5m, 15m, 1h, 4h, 1d, 1w`
- 6 strategies: EMA trend, RSI momentum, MACD cross, volume confirmation, price momentum, ADX strength
- Multi-TF confirmation: `min_confidence`, `tf_min_confidence`, `min_agreeing_strategies`, `signal_scans_confirm`
- Reversal alarm, periodic status report, pause/resume
- Telegram alerts (optional)

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
