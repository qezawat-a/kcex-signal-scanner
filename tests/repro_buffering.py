#!/usr/bin/env python3
"""Reproduce the Railway log-buffering issue locally.

Runs supervisor.py with stdout/stderr redirected to a file (exactly how a
container without a TTY behaves) and checks whether the scanner's print()
output reaches the log promptly or sits in the stdout buffer.
"""
import os
import subprocess
import time

LOG = "/tmp/buf_test.log"
if os.path.exists(LOG):
    os.remove(LOG)

# stdout -> file (block-buffered, like `docker logs`), stderr -> same file
with open(LOG, "wb") as fh:
    proc = subprocess.Popen(
        ["python3", "supervisor.py"],
        stdout=fh, stderr=subprocess.STDOUT,
        cwd="/var/minis/workspace/kcex-signal-scanner",
        env={**os.environ, "TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": ""},
    )

time.sleep(12)
proc.terminate()
proc.wait(timeout=10)
time.sleep(1)

raw = open(LOG, "rb").read()
text = raw.decode("utf-8", "replace")
print(f"--- bytes captured: {len(raw)} ---")
print(text[:1500] if text else "(EMPTY — nothing flushed)")
print("--- supervisor INFO lines (stderr) ---")
print("count:", text.count("supervisor:"))
print("--- scanner print() lines (stdout) ---")
print("KCEX SIGNAL SCAN present:", "KCEX SIGNAL SCAN" in text)
