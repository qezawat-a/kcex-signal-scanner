#!/usr/bin/env python3
"""
KCEX Futures Signal Scanner
===========================
Public, non-trading signal scanner for KCEX USDT-M perpetual futures.

Controls (state directory: ./state):
  touch state/pause   -> pause scanning
  touch state/resume  -> resume scanning
  python3 scanner.py --status -> show current status
  python3 scanner.py --pause  -> pause
  python3 scanner.py --resume -> resume

Configuration is read from config.json.
"""
import argparse
import asyncio
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
BASE_URL = "https://www.kcex.com"
CONFIG_FILE = BASE_DIR / "config.json"
STATE_FILE = BASE_DIR / "state.json"
DOTENV_FILE = BASE_DIR / ".env"


def load_dotenv():
    """Load BASE_DIR/.env (KEY=VALUE per line) into os.environ if not already set."""
    try:
        if not DOTENV_FILE.exists():
            return
        for raw in DOTENV_FILE.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v
    except Exception:
        pass
PAUSE_FLAG = BASE_DIR / "state" / "pause"
RESUME_FLAG = BASE_DIR / "state" / "resume"

STRATEGIES = {
    "ema": 0.20,
    "rsi": 0.20,
    "macd": 0.20,
    "volume": 0.20,
    "momentum": 0.20,
    "adx": 0.20,
}

HEADERS = {
    "User-Agent": "KCEX-Scanner/1.0 (contact: scanner)",
    "Accept": "application/json, text/plain, */*",
    "Referer": BASE_URL + "/",
    "Origin": BASE_URL,
}


def load_config():
    load_dotenv()
    with open(CONFIG_FILE, encoding="utf-8") as f:
        cfg = json.load(f)
    cfg.setdefault("symbols", "ALL")
    cfg.setdefault("timeframes", ["1m", "3m", "5m", "15m"])
    cfg.setdefault("scan_interval_sec", 10)
    cfg.setdefault("report_interval_sec", 30)
    cfg.setdefault("min_confidence", 70)
    cfg.setdefault("tf_min_confidence", 60)
    cfg.setdefault("min_agreeing_strategies", 3)
    cfg.setdefault("signal_scans_confirm", 2)
    cfg.setdefault("reversal_alarm", True)
    cfg.setdefault("max_symbols", 30)
    cfg.setdefault("concurrency", 6)
    cfg.setdefault("sl_atr_multiple", 1.5)
    cfg.setdefault("tp_atr_multiple", 2.0)
    cfg.setdefault("telegram", {"enabled": False, "token": "", "chat_id": ""})
    tg = cfg.get("telegram") or {}
    env_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    env_chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if env_token:
        tg["token"] = env_token
    if env_chat:
        tg["chat_id"] = str(env_chat)
    # auto-enable when both credentials are present (env or file)
    if tg.get("token") and tg.get("chat_id"):
        if not tg.get("enabled"):
            # only auto-enable if credentials came from env (explicit file opt-in otherwise)
            if env_token and env_chat:
                tg["enabled"] = True
    cfg["telegram"] = tg
    return cfg


def save_config(cfg):
    # don't persist transient keys
    data = {k: v for k, v in cfg.items()}
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return fresh_state()


def fresh_state():
    return {
        "last_confirmed_direction": None,
        "confirm_count": 0,
        "current_direction": None,
        "current_confidence": 0,
        "last_scan_time": None,
        "last_report_time": None,
        "scan_count": 0,
        "signal_count": 0,
        "symbols_scanned": 0,
        "paused": False,
    }


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def paused():
    return PAUSE_FLAG.exists()


def set_paused(value=True):
    if value:
        PAUSE_FLAG.parent.mkdir(parents=True, exist_ok=True)
        PAUSE_FLAG.touch(exist_ok=True)
        if RESUME_FLAG.exists():
            RESUME_FLAG.unlink()
    else:
        if PAUSE_FLAG.exists():
            PAUSE_FLAG.unlink()


def fmt_duration(seconds):
    if seconds is None:
        return "-"
    d = int(seconds)
    h, m = divmod(d, 3600)
    m, s = divmod(m, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def ema(series, period):
    return series.ewm(span=period, adjust=False).mean().values


def rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return (100 - (100 / (1 + rs))).values


def macd(series, fast=12, slow=26, signal=9):
    ema_fast = ema(series, fast)
    ema_slow = ema(series, slow)
    macd_line = ema_fast - ema_slow
    sig = pd.Series(macd_line).ewm(span=signal, adjust=False).mean().values
    return macd_line - sig, macd_line, sig


def atr(high, low, close, period=14):
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period).mean().values


def adx(high, low, close, period=14):
    prev_close = close.shift(1)
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)
    tr = atr(high, low, close, period)
    plus_di = 100 * pd.Series(plus_dm).ewm(alpha=1 / period, min_periods=period).mean() / pd.Series(tr).replace(0, np.nan)
    minus_di = 100 * pd.Series(minus_dm).ewm(alpha=1 / period, min_periods=period).mean() / pd.Series(tr).replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return pd.Series(dx).ewm(alpha=1 / period, min_periods=period).mean().values, plus_di.values, minus_di.values


def prepare_df(kline):
    data = kline.get("data") or kline
    if isinstance(data, list) and data:
        df = pd.DataFrame(data, columns=[
            "time", "open", "high", "low", "close", "vol", "amount",
            "realOpen", "realClose", "realHigh", "realLow"
        ])
    elif isinstance(data, dict) and data.get("time"):
        df = pd.DataFrame(data)
    else:
        return None
    for col in ["time", "open", "high", "low", "close", "vol", "amount", "realOpen", "realClose", "realHigh", "realLow"]:
        if col not in df.columns:
            df[col] = None
    df["time"] = pd.to_numeric(df["time"], errors="coerce")
    df["open"] = pd.to_numeric(df["open"], errors="coerce")
    df["high"] = pd.to_numeric(df["high"], errors="coerce")
    df["low"] = pd.to_numeric(df["low"], errors="coerce")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df["vol"] = pd.to_numeric(df["vol"], errors="coerce")
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
    df = df.dropna(subset=["close"])
    if len(df) < 2:
        return None
    return df.sort_values("time").reset_index(drop=True)


def analyze_tf(df, period):
    """Return (direction, confidence, details) for one timeframe."""
    if len(df) < 26:
        return 0, 0.0, {"reason": "not_enough_data"}

    close = pd.Series(df["close"].values)
    high = pd.Series(df["high"].values)
    low = pd.Series(df["low"].values)
    volume = pd.Series(df["vol"].values)
    open_ = pd.Series(df["open"].values)

    scores = {}
    signs = {}

    # EMA trend
    ema9 = ema(close, 9)
    ema21 = ema(close, 21)
    if ema9[-1] > ema21[-1] and close.iloc[-1] > ema21[-1]:
        s, d = 0.2, 1
    elif ema9[-1] < ema21[-1] and close.iloc[-1] < ema21[-1]:
        s, d = 0.2, -1
    else:
        s, d = 0.2, 0
    scores["ema"] = s
    signs["ema"] = d

    # RSI
    r = rsi(close).astype(float)
    r_last = float(r[-1])
    if r_last > 55:
        s, d = 0.2, 1
    elif r_last < 45:
        s, d = 0.2, -1
    else:
        s, d = 0.2, 0
    scores["rsi"] = s
    signs["rsi"] = d

    # MACD
    sig, macd_line, sig_line = macd(close)
    if macd_line[-1] > sig_line[-1] and macd_line[-2] <= sig_line[-2]:
        s, d = 0.2, 1
    elif macd_line[-1] < sig_line[-1] and macd_line[-2] >= sig_line[-2]:
        s, d = 0.2, -1
    else:
        s, d = 0.2, 0
    scores["macd"] = s
    signs["macd"] = d

    # Volume confirmation
    avg_vol = volume.rolling(20, min_periods=5).mean().values[-1]
    cur_vol = float(volume.iloc[-1]) if volume.iloc[-1] == volume.iloc[-1] else 0.0
    if avg_vol and avg_vol > 0 and cur_vol > avg_vol * 1.2:
        direction_vol = 1 if float(close.iloc[-1] - open_.iloc[-1]) > 0 else -1
        s, d = 0.2, direction_vol
    else:
        s, d = 0.2, 0
    scores["volume"] = s
    signs["volume"] = d

    # Momentum
    mom_period = min(5, len(close) - 1)
    momentum = float(close.iloc[-1] - close.iloc[-1 - mom_period])
    if momentum > 0:
        s, d = 0.2, 1
    elif momentum < 0:
        s, d = 0.2, -1
    else:
        s, d = 0.2, 0
    scores["momentum"] = s
    signs["momentum"] = d

    # ADX
    adx_val, plus_di, minus_di = adx(high, low, close)
    adx_last = float(adx_val[-1])
    if adx_last >= 25 and plus_di[-1] > minus_di[-1]:
        s, d = 0.2, 1
    elif adx_last >= 25 and minus_di[-1] > plus_di[-1]:
        s, d = 0.2, -1
    else:
        s, d = 0.2, 0
    scores["adx"] = s
    signs["adx"] = d

    total_weight = sum(STRATEGIES.values())
    net = sum(scores[k] * signs[k] for k in STRATEGIES)
    raw_confidence = abs(net) / total_weight * 100 if total_weight else 0.0
    raw_confidence = min(max(raw_confidence, 0.0), 100.0)
    direction = 1 if net > 0.2 * total_weight else (-1 if net < -0.2 * total_weight else 0)

    active = [k for k in STRATEGIES if abs(signs.get(k, 0)) > 0]
    details = {
        "scores": {k: round(scores[k] * signs.get(k, 0), 3) for k in STRATEGIES},
        "active_strategies": active,
        "rsi": round(float(r_last), 2),
        "adx": round(adx_last, 2),
        "plus_di": round(float(plus_di[-1]), 2),
        "minus_di": round(float(minus_di[-1]), 2),
        "momentum": round(momentum, 4),
        "volume_ratio": round(cur_vol / avg_vol, 3) if avg_vol else None,
    }
    return direction, round(raw_confidence, 2), details


def atr_value(df, period=14):
    arr = atr(pd.Series(df["high"].values), pd.Series(df["low"].values), pd.Series(df["close"].values), period)
    return float(arr[-1]) if len(arr) and arr[-1] == arr[-1] else None


def price_percent(price, pct):
    return price * pct / 100


async def fetch_json(session, url, params=None):
    for attempt in range(3):
        try:
            async with session.get(url, params=params, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=8)) as r:
                if r.status == 429:
                    await asyncio.sleep(1 + attempt)
                    continue
                text = await r.text()
                data = json.loads(text)
                if isinstance(data, dict) and not data.get("success"):
                    return None
                return data
        except Exception:
            await asyncio.sleep(0.5 * (attempt + 1))
    return None


async def fetch_tickers(session, cfg):
    data = await fetch_json(session, f"{BASE_URL}/fapi/v1/contract/ticker")
    if not data or not data.get("success"):
        return []
    items = data.get("data") or []
    max_symbols = cfg.get("max_symbols", 30)
    if cfg.get("symbols") == "ALL":
        items = sorted(items, key=lambda x: float(x.get("volume24", 0) or 0), reverse=True)[:max_symbols]
        return [i.get("symbol") for i in items if i.get("symbol")]
    return cfg.get("symbols", [])


async def fetch_kline(session, symbol, period, limit=7):
    data = await fetch_json(session, f"{BASE_URL}/fapi/v1/contract/kline/{symbol}", params={
        "period": period,
        "limit": max(limit, 60),
    })
    if not data or not data.get("success"):
        return None
    return {"symbol": symbol, "period": period, "data": data.get("data") or []}


async def scan_one(session, cfg, state, symbol, period, sem):
    async with sem:
        kline = await fetch_kline(session, symbol, period)
        if not kline or not kline.get("data"):
            return symbol, period, None, None
        df = prepare_df(kline)
        if df is None or len(df) < 26:
            return symbol, period, None, None
        direction, confidence, details = analyze_tf(df, period)
        atr_val = atr_value(df)
        return symbol, period, {"direction": direction, "confidence": confidence, "details": details, "atr": atr_val}, df


async def scan_market(session, cfg, state):
    sem = asyncio.Semaphore(cfg.get("concurrency", 6))
    symbols = await fetch_tickers(session, cfg)
    state["symbols_scanned"] = len(symbols)
    tasks = []
    for symbol in symbols:
        for tf in cfg["timeframes"]:
            tasks.append(scan_one(session, cfg, state, symbol, tf, sem))
    results = await asyncio.gather(*tasks, return_exceptions=True)
    ok = sum(1 for r in results if not isinstance(r, Exception) and r and r[2])
    state["tf_ok_count"] = ok
    state["tf_fail_count"] = len(results) - ok
    return symbols, results


def compute_signal(cfg, state, symbols, results):
    # Aggregate by symbol
    symbol_data = {s: {} for s in symbols}
    for r in results:
        if isinstance(r, Exception):
            continue
        symbol, period, analysis, df = r
        if analysis is None:
            continue
        symbol_data[symbol][period] = analysis

    signals = []
    strategy_net = {k: 0.0 for k in STRATEGIES}
    strategy_count = {k: 0 for k in STRATEGIES}
    for symbol, tf_data in symbol_data.items():
        if not tf_data:
            continue
        tf_scores = []
        for tf in cfg["timeframes"]:
            d = tf_data.get(tf)
            if not d:
                continue
            tf_scores.append(d)
            for k in STRATEGIES:
                contrib = d["details"]["scores"].get(k, 0.0)
                if abs(contrib) > 0:
                    strategy_net[k] += contrib
                    strategy_count[k] += 1
        if not tf_scores:
            continue
        avg_conf = float(np.mean([d["confidence"] for d in tf_scores]))
        net_dir = sum(np.sign([d["direction"] for d in tf_scores]) * d["confidence"] for d in tf_scores)
        global_conf = float(np.mean([d["confidence"] for d in tf_scores]))
        active_tfs = [d for d in tf_scores if d["direction"] != 0]
        if not active_tfs:
            direction = 0
        else:
            direction = 1 if sum(d["direction"] for d in active_tfs) > 0 else -1
        details = {
            "timeframes": {tf: tf_data.get(tf) for tf in cfg["timeframes"]},
            "avg_confidence": round(global_conf, 2),
            "active_timeframes": len(active_tfs),
        }
        signals.append({
            "symbol": symbol,
            "direction": direction,
            "confidence": round(global_conf, 2),
            "details": details,
        })

    # Global direction: sign of total net strategy score; require enough agreeing strategies
    agreeing = sum(1 for k in STRATEGIES if strategy_count[k] >= cfg.get("min_agreeing_strategies", 3) and abs(strategy_net[k]) > 0.2)
    net_total = sum(strategy_net.values())
    global_dir = 1 if net_total > 0.2 * sum(STRATEGIES.values()) else (-1 if net_total < -0.2 * sum(STRATEGIES.values()) else 0)
    global_conf = float(np.mean([s["confidence"] for s in signals])) if signals else 0.0
    global_conf = min(max(global_conf, 0.0), 100.0)
    return signals, {"direction": global_dir, "confidence": round(global_conf, 2), "agreeing_strategies": agreeing, "strategy_net": strategy_net, "strategy_count": strategy_count}


def update_state(state, global_dir, global_conf):
    now = time.time()
    state["last_scan_time"] = datetime.fromtimestamp(now, tz=timezone.utc).isoformat()
    state["scan_count"] = state.get("scan_count", 0) + 1
    prev = state.get("last_confirmed_direction")
    if global_dir != 0:
        if global_dir == prev:
            state["confirm_count"] = state.get("confirm_count", 0) + 1
        else:
            state["confirm_count"] = 1
            state["last_confirmed_direction"] = global_dir
    else:
        state["confirm_count"] = 0
        # keep last_confirmed_direction unchanged when neutral
    if state.get("confirm_count", 0) >= state.get("cfg", {}).get("signal_scans_confirm", 2) and prev != global_dir and global_dir != 0:
        # reversal or fresh confirmed signal
        state["signal_count"] = state.get("signal_count", 0) + 1
        reversal = (prev is not None and prev != global_dir)
        state["last_confirmed_direction"] = global_dir
    state["current_direction"] = global_dir
    state["current_confidence"] = global_conf
    state["last_report_time"] = datetime.fromtimestamp(now, tz=timezone.utc).isoformat()
    return prev


def build_signal_entry(symbol, analysis, price, cfg):
    tf_data = analysis["details"]["timeframes"]
    atrs = [tf_data[tf]["atr"] for tf in cfg["timeframes"] if tf_data.get(tf) and tf_data[tf].get("atr")]
    atr_val = float(np.mean(atrs)) if atrs else None
    if atr_val is None:
        atr_val = price * 0.005
    if analysis["direction"] == 1:
        sl = price - cfg.get("sl_atr_multiple", 1.5) * atr_val
        tp = price + cfg.get("tp_atr_multiple", 2.0) * atr_val
    else:
        sl = price + cfg.get("sl_atr_multiple", 1.5) * atr_val
        tp = price - cfg.get("tp_atr_multiple", 2.0) * atr_val
    return {
        "symbol": symbol,
        "direction": "LONG" if analysis["direction"] == 1 else "SHORT",
        "price": round(price, 4),
        "entry": round(price, 4),
        "sl": round(sl, 4),
        "tp": round(tp, 4),
        "sl_pct": round(abs(sl - price) / price * 100, 3),
        "tp_pct": round(abs(tp - price) / price * 100, 3),
        "confidence": analysis["confidence"],
        "timeframes": {tf: tf_data[tf]["confidence"] for tf in cfg["timeframes"] if tf_data.get(tf)},
        "strategies": analysis["details"]["active_strategies"],
    }


def format_report(cfg, state, symbols, signals, global_info, price_map):
    lines = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    lines.append("=" * 90)
    lines.append(f"KCEX SIGNAL SCAN  |  {now} UTC  |  scans={state.get('scan_count',0)}  signals={state.get('signal_count',0)}")
    lines.append(f"Symbols: {state.get('symbols_scanned',0)} | Timeframes: {','.join(cfg['timeframes'])} | Scan: {cfg['scan_interval_sec']}s | Report: {cfg['report_interval_sec']}s")
    lines.append(f"Global direction: {'LONG' if global_info['direction']==1 else 'SHORT' if global_info['direction']==-1 else 'NEUTRAL'} | Confidence: {global_info['confidence']:.1f} | Agreeing strategies: {global_info['agreeing_strategies']}/{len(STRATEGIES)}")
    n_ok = state.get("tf_ok_count", 0)
    n_fail = state.get("tf_fail_count", 0)
    if n_ok or n_fail:
        lines.append(f"TF results: ok={n_ok} fail={n_fail}")
    lines.append("-" * 90)
    if not signals:
        lines.append("No analyzable TF data (all kline fetches empty — check API limits/connectivity).")
        lines.append("=" * 90)
        return "\n".join(lines)
    count = 0
    for sig in signals[:40]:
        sym = sig["symbol"]
        price = price_map.get(sym, 0)
        if sig["confidence"] >= cfg.get("min_confidence", 70) and sig["direction"] != 0:
            entry = build_signal_entry(sym, sig, price, cfg)
            count += 1
            lines.append(
                f"{sym:12s} | {entry['direction']:4s} | conf {entry['confidence']:5.1f} | "
                f"entry {entry['entry']:>10} | SL {entry['sl']:>10} ({entry['sl_pct']:+.3f}%) | "
                f"TP {entry['tp']:>10} ({entry['tp_pct']:+.3f}%) | "
                f"TFs {entry['timeframes']} | {','.join(entry['strategies'][:4])}"
            )
    lines.append("-" * 90)
    lines.append(f"Showing {count} high-confidence signals (threshold {cfg.get('min_confidence',70)}). Full details in state.json.")
    lines.append("=" * 90)
    return "\n".join(lines)


async def send_telegram(cfg, text, parse_mode=None):
    tg = cfg.get("telegram", {}) or {}
    if not tg.get("enabled"):
        return False
    token = (tg.get("token") or "").strip()
    chat = str(tg.get("chat_id") or "").strip()
    if not token or not chat:
        print("Telegram skipped: missing token or chat_id")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat, "text": text[:4096]}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.post(url, data=payload) as r:
                body = await r.text()
                if r.status != 200:
                    print(f"Telegram send failed HTTP {r.status}: {body[:300]}")
                    return False
                return True
    except Exception as e:
        print(f"Telegram send error: {type(e).__name__}: {e}")
        return False


async def run_scan_loop(cfg):
    state = load_state()
    state["cfg"] = cfg
    last_report = 0
    print(f"KCEX Signal Scanner started. TFs={cfg['timeframes']} interval={cfg['scan_interval_sec']}s")
    print("Pause/resume: touch state/pause  |  status: python3 scanner.py --status")
    while True:
        if paused():
            state["paused"] = True
            save_state(state)
            await asyncio.sleep(2)
            if RESUME_FLAG.exists():
                RESUME_FLAG.unlink()
                if PAUSE_FLAG.exists():
                    PAUSE_FLAG.unlink()
                state["paused"] = False
                print("Resumed at", datetime.now(timezone.utc).isoformat())
            continue
        state["paused"] = False
        try:
            async with aiohttp.ClientSession() as session:
                symbols, results = await scan_market(session, cfg, state)
                price_data = {}
                ticker = await fetch_json(session, f"{BASE_URL}/fapi/v1/contract/ticker")
                if ticker and ticker.get("success"):
                    for item in ticker.get("data") or []:
                        try:
                            price_data[item.get("symbol")] = float(item.get("lastPrice", 0) or 0)
                        except (TypeError, ValueError):
                            continue
            signals, global_info = compute_signal(cfg, state, symbols, results)
            prev = update_state(state, global_info["direction"], global_info["confidence"])
            if global_info["direction"] != 0 and prev is not None and global_info["direction"] != prev and cfg.get("reversal_alarm", True):
                alarm = f"REVERSAL ALARM: {prev} -> {global_info['direction']} (conf {global_info['confidence']:.1f})"
                print(alarm)
                await send_telegram(cfg, alarm)
            now = time.time()
            if now - last_report >= cfg.get("report_interval_sec", 30):
                last_report = now
                report = format_report(cfg, state, symbols, signals, global_info, price_data)
                print(report)
                await send_telegram(cfg, report)
            save_state(state)
        except Exception as e:
            print(f"Scan error: {type(e).__name__}: {e}")
            await asyncio.sleep(5)
        await asyncio.sleep(cfg.get("scan_interval_sec", 10))


def print_status(cfg):
    state = load_state()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print("KCEX Signal Scanner STATUS")
    print(f"Updated: {now} UTC")
    print(f"Config file : {CONFIG_FILE}")
    print(f"State file  : {STATE_FILE}")
    print(f"Paused      : {state.get('paused') or paused()}")
    print(f"Scan interval : {cfg.get('scan_interval_sec')}s")
    print(f"Report interval: {cfg.get('report_interval_sec')}s")
    print(f"Timeframes    : {','.join(cfg.get('timeframes',[]))}")
    print(f"Symbols       : {cfg.get('symbols')}")
    print(f"Min confidence: {cfg.get('min_confidence')}")
    print(f"TF min conf   : {cfg.get('tf_min_confidence')}")
    print(f"Confirm scans : {cfg.get('signal_scans_confirm')}")
    tg = cfg.get("telegram", {}) or {}
    masked = ("..." + tg.get("token", "")[-4:]) if tg.get("token") else "(empty)"
    print(f"Telegram      : enabled={tg.get('enabled')} token={masked} chat_id={tg.get('chat_id') or '(empty)'}")
    print(f"Last scan     : {state.get('last_scan_time')}")
    print(f"Last report   : {state.get('last_report_time')}")
    print(f"Total scans   : {state.get('scan_count',0)}")
    print(f"Total signals : {state.get('signal_count',0)}")
    print(f"Current dir   : {'LONG' if state.get('current_direction')==1 else 'SHORT' if state.get('current_direction')==-1 else 'NEUTRAL'}")
    print(f"Current conf  : {state.get('current_confidence',0):.1f}")
    print(f"Symbols scanned: {state.get('symbols_scanned',0)}")


async def one_scan(cfg):
    state = load_state()
    state["cfg"] = cfg
    async with aiohttp.ClientSession() as session:
        symbols, results = await scan_market(session, cfg, state)
        price_data = {}
        ticker = await fetch_json(session, f"{BASE_URL}/fapi/v1/contract/ticker")
        if ticker and ticker.get("success"):
            for item in ticker.get("data") or []:
                try:
                    price_data[item.get("symbol")] = float(item.get("lastPrice", 0) or 0)
                except (TypeError, ValueError):
                    continue
    signals, global_info = compute_signal(cfg, state, symbols, results)
    update_state(state, global_info["direction"], global_info["confidence"])
    save_state(state)
    print(format_report(cfg, state, symbols, signals, global_info, price_data))
    print(f"Scanned {len(symbols)} symbols with {len(results)} TF results.")


def parse_args():
    p = argparse.ArgumentParser(description="KCEX Futures Signal Scanner")
    p.add_argument("--symbol", help="Symbol or comma list (overrides config)")
    p.add_argument("--timeframes", help="Timeframes comma-separated (overrides config)")
    p.add_argument("--scan-interval", type=int, help="Scan interval seconds")
    p.add_argument("--report-interval", type=int, help="Report interval seconds")
    p.add_argument("--min-confidence", type=int, help="Minimum confidence")
    p.add_argument("--tf-min-confidence", type=int, help="Per-TF minimum confidence")
    p.add_argument("--min-agreeing", type=int, help="Minimum agreeing strategies")
    p.add_argument("--confirm", type=int, help="Confirmation scans")
    p.add_argument("--start", action="store_true", help="Start/resume scanner")
    p.add_argument("--once", action="store_true", help="Single scan cycle then exit")
    p.add_argument("--pause", action="store_true", help="Pause scanner")
    p.add_argument("--resume", action="store_true", help="Resume scanner")
    p.add_argument("--status", action="store_true", help="Print status")
    p.add_argument("--telegram-on", action="store_true", help="Enable Telegram alerts (needs TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID env or config)")
    p.add_argument("--telegram-off", action="store_true", help="Disable Telegram alerts")
    p.add_argument("--telegram-test", action="store_true", help="Send a Telegram test message then exit")
    p.add_argument("--set-symbol", help="Persist symbol(s) to config: ALL or comma list e.g. BTC_USDT,ETH_USDT")
    p.add_argument("--set-timeframes", help="Persist timeframes to config e.g. 1m,3m,5m,15m")
    p.add_argument("--set-scan-interval", type=int, help="Persist scan interval seconds")
    p.add_argument("--set-report-interval", type=int, help="Persist report interval seconds")
    return p.parse_args()


def main():
    args = parse_args()
    cfg = load_config()
    if args.symbol:
        cfg["symbols"] = [s.strip() for s in args.symbol.split(",")]
    if args.timeframes:
        cfg["timeframes"] = [t.strip() for t in args.timeframes.split(",")]
    if args.scan_interval:
        cfg["scan_interval_sec"] = args.scan_interval
    if args.report_interval:
        cfg["report_interval_sec"] = args.report_interval
    if args.min_confidence:
        cfg["min_confidence"] = args.min_confidence
    if args.tf_min_confidence:
        cfg["tf_min_confidence"] = args.tf_min_confidence
    if args.min_agreeing:
        cfg["min_agreeing_strategies"] = args.min_agreeing
    if args.confirm:
        cfg["signal_scans_confirm"] = args.confirm
    dirty = False
    if args.set_symbol:
        v = args.set_symbol.strip()
        cfg["symbols"] = "ALL" if v.upper() == "ALL" else [s.strip() for s in v.split(",")]
        dirty = True
    if args.set_timeframes:
        cfg["timeframes"] = [t.strip() for t in args.set_timeframes.split(",")]
        dirty = True
    if args.set_scan_interval:
        cfg["scan_interval_sec"] = args.set_scan_interval
        dirty = True
    if args.set_report_interval:
        cfg["report_interval_sec"] = args.set_report_interval
        dirty = True
    if args.telegram_on:
        cfg.setdefault("telegram", {}).setdefault("token", "")
        cfg["telegram"]["enabled"] = True
        dirty = True
    if args.telegram_off:
        cfg.setdefault("telegram", {}).setdefault("token", "")
        cfg["telegram"]["enabled"] = False
        dirty = True
    if dirty:
        save_config(cfg)
        print("Config saved:", CONFIG_FILE)
    if args.telegram_test:
        ok = asyncio.run(send_telegram(cfg, "KCEX scanner Telegram test ✅ (alerts working)"))
        print("Telegram test:", "SENT ✅" if ok else "FAILED ❌ (check TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID env or config)")
        return
    if args.once:
        asyncio.run(one_scan(cfg))
        return
    if args.pause:
        set_paused(True)
        print("Paused. Use --resume to resume.")
        return
    if args.resume:
        set_paused(False)
        print("Resumed.")
        return
    if args.start:
        set_paused(False)
        print("Resumed. Starting scan loop.")
    if args.status:
        print_status(cfg)
        return
    if args.start or not paused():
        asyncio.run(run_scan_loop(cfg))
    else:
        print("Scanner paused. Use --start or --resume.")


if __name__ == "__main__":
    main()
