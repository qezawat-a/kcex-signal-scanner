#!/usr/bin/env python3
"""
KCEX Scanner Telegram bot — mini-app control panel.

Commands (send these to your bot):
  /status                 show scanner status
  /scan                   run one scan cycle now (report is sent automatically)
  /pause                  pause the continuous scanner
  /resume                 resume the continuous scanner
  /symbol <ALL|BTC_USDT,..>   set symbol(s)
  /tfs <1m,3m,5m,15m>     set timeframes (any of 1m,3m,5m,15m,1h,4h,1d,1w)
  /report <seconds>       set report interval
  /scanint <seconds>      set scan interval
  /config                 show current config
  /help                   show this help

Runs with polling (no webhook needed).
Env / .env: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID (chat id = owner allowlist).
"""
import asyncio
import json
import logging
import os
import sys
import time

import aiohttp

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from scanner import load_config, save_config, set_paused, paused, one_scan  # noqa: E402

VALID_TFS = {"1m", "3m", "5m", "15m", "1h", "4h", "1d", "1w"}

HELP = (
    "KCEX Scanner commands:\n"
    "/status — scanner status\n"
    "/scan — run one scan now\n"
    "/pause — pause scanner\n"
    "/resume — resume scanner\n"
    "/symbol ALL | BTC_USDT,ETH_USDT — set symbols\n"
    "/tfs 1m,3m,5m,15m — set timeframes\n"
    "/report 30 — report interval sec\n"
    "/scanint 10 — scan interval sec\n"
    "/config — show config\n"
    "/help — this help"
)

last_sent_report = {"text": None, "ts": 0.0}


def cfg_summary(cfg):
    tg = cfg.get("telegram", {}) or {}
    masked = ("..." + tg.get("token", "")[-4:]) if tg.get("token") else "(empty)"
    return (
        f"symbols={cfg.get('symbols')} tfs={','.join(cfg.get('timeframes', []))} "
        f"scan={cfg.get('scan_interval_sec')}s report={cfg.get('report_interval_sec')}s "
        f"min_conf={cfg.get('min_confidence')} tf_min={cfg.get('tf_min_confidence')} "
        f"agree={cfg.get('min_agreeing_strategies')} confirm={cfg.get('signal_scans_confirm')} "
        f"paused={paused()} tg={tg.get('enabled')}:{masked}"
    )


async def tg_call(token, method, params):
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as s:
        async with s.post(f"https://api.telegram.org/bot{token}/{method}", data=params) as r:
            body = await r.text()
            try:
                return json.loads(body)
            except Exception:
                return {"ok": False, "description": body[:300]}


async def tg_send(token, chat_id, text):
    return await tg_call(token, "sendMessage", {"chat_id": str(chat_id), "text": text[:4096]})


async def cmd_status(cfg):
    return "STATUS\n" + cfg_summary(load_config())


async def cmd_config(cfg):
    c = load_config()
    c2 = dict(c)
    if isinstance(c2.get("telegram"), dict):
        t = dict(c2["telegram"])
        if t.get("token"):
            t["token"] = "..." + t["token"][-4:]
        c2["telegram"] = t
    return "CONFIG\n" + json.dumps(c2, indent=1, ensure_ascii=False)[:3500]


async def cmd_scan(cfg, reply):
    # capture what one_scan prints + sends to telegram itself
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        await one_scan(load_config())
    out = buf.getvalue().strip() or "(empty scan output)"
    last_sent_report["text"] = out
    last_sent_report["ts"] = time.time()
    # one_scan already sent the report to Telegram; reply a short ack + the tail
    tail = "\n".join(out.splitlines()[-14:])
    await reply("SCAN DONE (full report was also pushed automatically):\n" + tail)


async def handle_command(cfg, token, owner_chat, chat_id, text, reply):
    parts = text.strip().split(None, 1)
    cmd = parts[0].split("@")[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""
    c = load_config()
    if cmd == "/status":
        await reply(await cmd_status(c))
    elif cmd == "/config":
        await reply(await cmd_config(c))
    elif cmd == "/help" or cmd == "/start":
        await reply(HELP)
    elif cmd == "/scan":
        await reply("Scanning KCEX… (30 symbols x 4 TFs, ~10s)")
        await cmd_scan(c, reply)
    elif cmd == "/pause":
        set_paused(True)
        await reply("Paused ⏸")
    elif cmd == "/resume":
        set_paused(False)
        await reply("Resumed ▶")
    elif cmd == "/symbol":
        if not arg:
            await reply("Usage: /symbol ALL  or  /symbol BTC_USDT,ETH_USDT")
            return
        c["symbols"] = "ALL" if arg.upper() == "ALL" else [s.strip() for s in arg.split(",") if s.strip()]
        save_config(c)
        await reply("Saved. " + cfg_summary(c))
    elif cmd == "/tfs":
        tfs = [t.strip() for t in arg.split(",") if t.strip()]
        bad = [t for t in tfs if t not in VALID_TFS]
        if not tfs or bad:
            await reply(f"Usage: /tfs 1m,3m,5m,15m  (valid: {','.join(sorted(VALID_TFS))})")
            return
        c["timeframes"] = tfs
        save_config(c)
        await reply("Saved. " + cfg_summary(c))
    elif cmd == "/report":
        try:
            v = int(arg)
            assert v >= 10
        except Exception:
            await reply("Usage: /report 30  (>=10 seconds)")
            return
        c["report_interval_sec"] = v
        save_config(c)
        await reply("Saved. " + cfg_summary(c))
    elif cmd == "/scanint":
        try:
            v = int(arg)
            assert v >= 5
        except Exception:
            await reply("Usage: /scanint 10  (>=5 seconds)")
            return
        c["scan_interval_sec"] = v
        save_config(c)
        await reply("Saved. " + cfg_summary(c))
    else:
        await reply("Unknown command.\n" + HELP)


async def poll(token, owner_chat):
    offset = 0
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as s:
        # set bot commands menu (best effort)
        try:
            cmds = [
                {"command": "status", "description": "scanner status"},
                {"command": "scan", "description": "run one scan now"},
                {"command": "pause", "description": "pause scanner"},
                {"command": "resume", "description": "resume scanner"},
                {"command": "symbol", "description": "set symbols"},
                {"command": "tfs", "description": "set timeframes"},
                {"command": "report", "description": "set report interval"},
                {"command": "scanint", "description": "set scan interval"},
                {"command": "config", "description": "show config"},
            ]
            await s.post(f"https://api.telegram.org/bot{token}/setMyCommands",
                         json={"commands": cmds}, timeout=aiohttp.ClientTimeout(total=15))
        except Exception as e:
            print("setMyCommands skipped:", e)
        print(f"Telegram control bot polling. owner_chat={owner_chat}")
        while True:
            try:
                async with s.post(f"https://api.telegram.org/bot{token}/getUpdates",
                                  data={"offset": offset, "timeout": 30}) as r:
                    data = json.loads(await r.text())
            except Exception as e:
                print("getUpdates error:", type(e).__name__, e)
                await asyncio.sleep(5)
                continue
            for upd in data.get("result", []):
                offset = max(offset, upd.get("update_id", 0) + 1)
                msg = upd.get("message") or upd.get("edited_message") or {}
                chat_id = str((msg.get("chat") or {}).get("id", ""))
                text = (msg.get("text") or "").strip()
                if not text:
                    continue
                # owner allowlist (same chat that receives reports)
                if owner_chat and chat_id != str(owner_chat):
                    await tg_send(token, chat_id, "Not authorized for this scanner bot.")
                    continue
                cfg = load_config()

                async def reply(t, _cid=chat_id):
                    await tg_send(token, _cid, t)

                try:
                    await handle_command(cfg, token, owner_chat, chat_id, text, reply)
                except Exception as e:
                    await reply(f"Command error: {type(e).__name__}: {e}")
            await asyncio.sleep(0.5)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    cfg = load_config()
    tg = cfg.get("telegram", {}) or {}
    token = (tg.get("token") or "").strip()
    chat = str(tg.get("chat_id") or "").strip()
    if not token or not chat:
        print("Missing TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID (env, .env or config.json telegram section).")
        sys.exit(2)
    print("Commands will appear in Telegram's '/' menu automatically (setMyCommands).")
    asyncio.run(poll(token, chat))


if __name__ == "__main__":
    main()
