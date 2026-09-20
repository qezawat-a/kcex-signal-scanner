---
name: scalping
description: Fast 1m/3m scalping playbook — when to trust low-timeframe signals and when to stand down.
---

# Scalping playbook (1m / 3m)

- Only trust 1m/3m LONG/SHORT when 15m agrees in the same direction (check `run_scan`, compare per-TF details).
- Stand down when: spread between EMA9/EMA21 < 0.05% of price (chop), or RSI whipsaws across 50 three+ times in the last 10 candles.
- ATR rule: SL = 1.5×ATR(14) on the traded TF, TP = 2×ATR. Never widen SL after entry.
- Volume filter: skip symbols where 24h quote volume < $1M.
- Report template: direction, entry zone, SL, TP, confidence, which TFs agree.
