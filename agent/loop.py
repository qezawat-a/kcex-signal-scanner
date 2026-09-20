"""Tool-calling agent loop (CRAG loop.js port).

run_agent(user_text, ...) -> {"reply": str, "tool_calls": [...], "provider": ..., "model": ...}
- builds history with system prompt (SOUL + skills + memory)
- each round: chat() -> execute tool calls -> append results -> repeat until
  a text answer or max_rounds. Reversal of CRAG default: tools are the point,
  so the loop keeps going while the model keeps calling tools.
"""
try:
    from .prompt import build_prompt
    from .providers import chat
except ImportError:  # direct script / flat execution
    from agent.prompt import build_prompt
    from agent.providers import chat


def _schema_for(model_tools):
    return [{"name": t["name"], "description": t.get("description", ""),
             "parameters": t.get("parameters", {"type": "object", "properties": {}})}
            for t in model_tools]


def run_agent(user_text, tools, memory=None, history=None, max_rounds=6,
              extra_skills=(), system_extra=""):
    model_tools = list(tools or [])
    by_name = {t["name"]: t for t in model_tools}
    system = build_prompt(memory=memory, extra_skills=extra_skills)
    if system_extra:
        system += "\n\n" + system_extra
    history = list(history or [])
    convo = history + [{"role": "user", "content": user_text}]
    calls_log = []
    last = None
    for _ in range(max_rounds):
        last = chat(system=system, messages=convo, tools=_schema_for(model_tools))
        calls = last.get("tool_calls") or []
        if not calls:
            convo.append({"role": "assistant", "content": last.get("content") or ""})
            break
        convo.append({"role": "assistant", "content": last.get("content") or "",
                      "tool_calls": [{"id": c["id"], "type": "function",
                                      "function": {"name": c["name"],
                                                   "arguments": __import__("json").dumps(c.get("args", {}))}}
                                     for c in calls]})
        for c in calls:
            tool = by_name.get(c["name"])
            if not tool:
                result = f"Error: unknown tool '{c['name']}'"
            else:
                try:
                    result = tool["run"](**(c.get("args") or {}))
                except TypeError as e:
                    result = f"Error: bad arguments for {c['name']}: {e}"
                except Exception as e:
                    result = f"Error in {c['name']}: {type(e).__name__}: {e}"
            result = str(result if result is not None else "")
            calls_log.append({"tool": c["name"], "args": c.get("args", {}), "ok": not result.startswith("Error")})
            convo.append({"role": "tool", "tool_call_id": c["id"], "content": result[:6000]})
    else:
        # ran out of rounds while still calling tools: force a wrap-up
        try:
            last = chat(system=system + "\n\nSummarize the tool results above in one short reply.",
                        messages=convo, tools=[])
        except Exception:
            pass
    reply = (last or {}).get("content") or ""
    if not reply and calls_log:
        reply = "Done. " + "; ".join(f"{c['tool']} ok" if c["ok"] else f"{c['tool']} failed" for c in calls_log) + "."
    return {"reply": reply, "tool_calls": calls_log,
            "provider": (last or {}).get("provider"), "model": (last or {}).get("model")}
