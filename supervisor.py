#!/usr/bin/env python3
"""
Railway entrypoint: runs the scanner loop + Telegram control bot side by side.

- scanner  -> periodic KCEX scans + Telegram reports (respects pause/resume)
- bot      -> Telegram / command menu + /ask agent

Both run as asyncio tasks in one process (one Railway service = enough).
Restart one side if it crashes; exit(1) only if both die.
"""
import asyncio
import logging
import sys
import traceback
from pathlib import Path

# Railway/`docker logs` capture stdout through a pipe, which Python treats as
# block-buffered: the scanner's print() output can sit in a 8 KB buffer for
# minutes and never reach the log viewer, while logging (stderr) shows up
# immediately. Force line buffering so scan reports appear as they happen.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(line_buffering=True)
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scanner import load_config, paused, set_paused  # noqa: E402
from telegram_bot import poll  # noqa: E402


async def run_scanner():
    # import locally so bot keeps running even if scanner module import hiccups
    from scanner import run_scan_loop
    while True:
        try:
            cfg = load_config()
            await run_scan_loop(cfg)
        except Exception:
            logging.exception("scanner crashed, restarting in 10s")
            await asyncio.sleep(10)


async def run_bot():
    while True:
        try:
            cfg = load_config()
            tg = cfg.get("telegram", {}) or {}
            token = (tg.get("token") or "").strip()
            chat = str(tg.get("chat_id") or "").strip()
            if not token or not chat:
                logging.error("supervisor: missing TELEGRAM_BOT_TOKEN/CHAT_ID — bot idle, retry in 30s")
                await asyncio.sleep(30)
                continue
            logging.info("supervisor: starting telegram bot (owner %s)", chat)
            await poll(token, chat)
        except Exception:
            logging.exception("telegram bot crashed, restarting in 10s")
            await asyncio.sleep(10)


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_config()
    tg = cfg.get("telegram", {}) or {}
    if not tg.get("token") or not tg.get("chat_id"):
        logging.warning("supervisor: Telegram creds missing at boot; scanner runs, bot waits for env.")
    if not paused():
        pass  # default: running
    s = asyncio.create_task(run_scanner())
    b = asyncio.create_task(run_bot())
    done, _ = await asyncio.wait({s, b}, return_when=asyncio.FIRST_COMPLETED)
    for t in done:
        try:
            t.result()
        except Exception:
            traceback.print_exc()
    logging.error("supervisor: one side exited, shutting down")
    sys.exit(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
