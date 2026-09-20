"""Agent CLI entry: `python3 agent_cli.py --chat` (REPL) or `--ask "..."`.

Reads LLM credentials from .env / env (AI_API_KEY + AI_BASE_URL + AI_MODEL,
or ANTHROPIC_*, GEMINI_*, NINEROUTER_*). See .env.example.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import scanner as S
from agent.loop import run_agent
from agent.memory import Memory, memory_tools
from agent.providers import detect_providers, resolve_model
from agent.tools import scanner_tools


def build_tools(memory):
    tools = scanner_tools(
        load_config=S.load_config, save_config=S.save_config,
        scan_market=S.scan_market, compute_signal=S.compute_signal,
        update_state=S.update_state, save_state=S.save_state,
        load_state=S.load_state, format_report=S.format_report,
        fetch_tickers=getattr(S, "fetch_tickers", None),
    )
    return tools + memory_tools(memory)


def cmd_models():
    for p in detect_providers():
        try:
            model, how = resolve_model(p)
            print(f"{p['name']}: model={model} ({how}) base={p['base_url']}")
        except Exception as e:
            print(f"{p['name']}: error {e}")


def cmd_ask(text):
    mem = Memory()
    print(run_agent(text, build_tools(mem), memory=mem)["reply"])


def cmd_chat():
    mem = Memory()
    tools = build_tools(mem)
    print("SignalOps agent. Type /quit to exit. (LLM credentials from .env)")
    try:
        while True:
            try:
                line = input("you> ").strip()
            except EOFError:
                break
            if not line:
                continue
            if line in ("/quit", "/exit", "quit", "exit"):
                break
            print(run_agent(line, tools, memory=mem)["reply"])
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--chat", action="store_true")
    ap.add_argument("--ask", default="")
    ap.add_argument("--models", action="store_true")
    ap.add_argument("--list-skills", action="store_true")
    a = ap.parse_args()
    if a.models:
        cmd_models()
    elif a.list_skills:
        from agent.skills import list_skills
        for s in list_skills():
            print(f"- {s['id']}: {s['description'] or s['name']}")
    elif a.ask:
        cmd_ask(a.ask)
    else:
        cmd_chat()
