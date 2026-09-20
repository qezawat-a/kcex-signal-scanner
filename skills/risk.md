---
name: risk
description: Risk rules the agent always applies — position framing, no-trade zones, loss limits.
---

# Risk rules (always on)

- No signal under min_confidence is tradable — report it as "watch", never as "entry".
- Reversal alarm ≠ entry: after a direction flip, wait `signal_scans_confirm` scans before calling it confirmed.
- Never recommend leverage explicitly; if asked, explain liquidation risk and suggest paper-trading first.
- News/spike filter: if a 1m candle range > 4×ATR(14), mark the symbol "spike — wait 3 candles" instead of signaling.
- Daily loss framing: if the user reports losses, switch to diagnose mode (recall notes, review last signals) before any new idea.
