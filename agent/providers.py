"""Provider detection + OpenAI-compatible chat.

No model names hardcoded. Agar gateway (9Router / OpenAI-compatible)
ba AI_BASE_URL / NINEROUTER_URL set bashe, /v1/models mishen va model
haye mojaz ro auto entekhab mikonim. Sirf API key lazem ast agar
gateway auth req karde.

Env:
  AI_API_KEY / AI_BASE_URL / AI_MODEL
  ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL / ANTHROPIC_MODEL
  GEMINI_API_KEY / GEMINI_BASE_URL / GEMINI_MODEL
  NINEROUTER_URL / NINEROUTER_KEY
"""
import json
import os
import urllib.request
from pathlib import Path

TIMEOUT_S = 60
PROBE_TIMEOUT_S = 12
MAX_TOKENS = 2048

KEY_VAR = {"openai": "AI_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "google": "GEMINI_API_KEY"}
MODEL_VAR = {"openai": "AI_MODEL", "anthropic": "ANTHROPIC_MODEL", "google": "GEMINI_MODEL"}
BASE_VAR = {"openai": "AI_BASE_URL", "anthropic": "ANTHROPIC_BASE_URL", "google": "GEMINI_BASE_URL"}

NON_CHAT_MARKERS = ["embed", "whisper", "tts", "audio", "dall", "image",
                    "moderation", "rerank", "realtime", "omni", "vl-"]
CHEAP_MARKERS = ["flash", "mini", "lite", "nano", "small", "distil",
                 "-8b", "-7b", "-4b", "-3b", "-2b", "-1b"]


def load_dotenv_here():
    p = Path(__file__).resolve().parent.parent / ".env"
    try:
        if not p.exists():
            return
        for raw in p.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v
    except Exception:
        pass


load_dotenv_here()

# 9Router shorthand
if os.environ.get("NINEROUTER_URL") and not os.environ.get("AI_BASE_URL"):
    os.environ["AI_BASE_URL"] = os.environ["NINEROUTER_URL"].rstrip("/")
if os.environ.get("NINEROUTER_KEY") and not os.environ.get("AI_API_KEY"):
    os.environ["AI_API_KEY"] = os.environ["NINEROUTER_KEY"]


def _get(url, headers, timeout=PROBE_TIMEOUT_S):
    req = urllib.request.Request(url, method="GET", headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "replace")


def _post(url, headers, body, timeout=TIMEOUT_S):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, method="POST", headers=headers, data=data)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "replace")


def _http(method, url, headers, body=None, timeout=TIMEOUT_S):
    req = urllib.request.Request(url, method=method, headers=headers,
                                 data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "replace")


def _post(url, headers, body, timeout=TIMEOUT_S):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, method="POST", headers=headers, data=data)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "replace")


def list_gateway_models(base_url, api_key=None):
    """List chat models from an OpenAI-compatible gateway."""
    url = base_url.rstrip("/") + "/models"
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        _, text = _get(url, headers, timeout=PROBE_TIMEOUT_S)
        data = json.loads(text)
        return [m.get("id") for m in data.get("data", []) if m.get("id")]
    except Exception:
        return []


def rank_models(ids):
    chat = [i for i in ids if not any(m in i.lower() for m in NON_CHAT_MARKERS)]
    free = [i for i in chat if ":free" in i.lower()]
    cheap = [i for i in chat if i not in free and any(m in i.lower() for m in CHEAP_MARKERS)]
    rest = [i for i in chat if i not in free and i not in cheap]
    return free + cheap + rest


def detect_providers():
    """Auto-detect providers from env + gateway model listing."""
    out = []

    # 1. Explicit per-provider configs
    for name in ["openai", "anthropic", "google"]:
        key = os.environ.get(KEY_VAR[name], "").strip()
        if not key:
            continue
        base = os.environ.get(BASE_VAR[name])
        if name == "openai":
            base = (base or "https://api.openai.com/v1").rstrip("/")
        elif name == "anthropic":
            base = (base or "https://api.anthropic.com").rstrip("/")
        else:
            b = base or "https://generativelanguage.googleapis.com/v1"
            if not b.endswith("/openai"):
                b = b.rstrip("/").removesuffix("/v1beta").removesuffix("/v1") + "/v1beta/openai"
            base = b.rstrip("/")
        env_model = os.environ.get(MODEL_VAR[name], "").strip()
        out.append({"name": name, "api_key": key, "base_url": base,
                    "model": env_model if env_model and env_model != "auto" else None,
                    "mode": "explicit"})

    # 2. Generic gateway auto-discovery
    gateway_url = os.environ.get("AI_BASE_URL", "").strip() or os.environ.get("NINEROUTER_URL", "").strip()
    gateway_key = os.environ.get("AI_API_KEY", "").strip() or os.environ.get("NINEROUTER_KEY", "").strip()
    if gateway_url:
        if not any(p.get("base_url") == gateway_url.rstrip("/") for p in out):
            ids = list_gateway_models(gateway_url, gateway_key)
            out.append({"name": "auto", "api_key": gateway_key, "base_url": gateway_url.rstrip("/"),
                        "model": os.environ.get("AI_MODEL", "auto").strip() or "auto",
                        "mode": "gateway", "available_models": rank_models(ids),
                        "list_ok": bool(ids)})
    return out


def resolve_model(provider):
    if provider.get("model") and provider["model"] != "auto":
        return provider["model"], "explicit"
    ids = provider.get("available_models") or list_gateway_models(provider["base_url"], provider.get("api_key"))
    ranked = rank_models(ids) if ids else []
    probe = (ranked or ["gpt-4o-mini", "gpt-4o", "deepseek-chat"])[:10]
    for cand in probe:
        try:
            chat_once({**provider, "model": cand}, system="Reply with: ok",
                      messages=[{"role": "user", "content": "ping"}], tools=[],
                      timeout=PROBE_TIMEOUT_S)
            provider["model"] = cand
            if "available_models" in provider and cand not in provider["available_models"]:
                provider["available_models"].append(cand)
            return cand, "auto"
        except Exception:
            continue
    provider["model"] = probe[0]
    return provider["model"], "fallback"


def _openai_payload(provider, system, messages, tools):
    body = {"model": provider["model"], "messages": []}
    if system:
        body["messages"].append({"role": "system", "content": system})
    body["messages"].extend(messages)
    if tools:
        body["tools"] = [{"type": "function", "function": {
            "name": t["name"], "description": t.get("description", ""),
            "parameters": t.get("parameters", {"type": "object", "properties": {}})}} for t in tools]
    return body


def _parse_openai(data):
    m = (data.get("choices") or [{}])[0].get("message") or {}
    calls = []
    for tc in m.get("tool_calls") or []:
        if tc.get("type") != "function":
            continue
        fn = tc.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except Exception:
            args = {}
        calls.append({"id": tc.get("id"), "name": fn.get("name"), "args": args})
    return {"content": m.get("content"), "tool_calls": calls}


def _to_anthropic_messages(messages):
    out, pending = [], []
    for m in messages:
        if m.get("role") == "tool":
            pending.append({"type": "tool_result", "tool_use_id": m.get("tool_call_id"),
                            "content": m.get("content") or ""})
            continue
        if pending:
            out.append({"role": "user", "content": pending})
            pending = []
        if m.get("role") == "assistant" and m.get("tool_calls"):
            blocks = []
            if m.get("content"):
                blocks.append({"type": "text", "text": m["content"]})
            for tc in m["tool_calls"]:
                blocks.append({"type": "tool_use", "id": tc["id"], "name": tc["name"],
                               "input": json.loads(tc.get("function", {}).get("arguments", "{}")
                                                   if isinstance(tc, dict) and "function" in tc else tc.get("args", {}))})
            out.append({"role": "assistant", "content": blocks})
        else:
            out.append({"role": m.get("role", "user"), "content": m.get("content") or ""})
    if pending:
        out.append({"role": "user", "content": pending})
    return out


def chat_once(provider, system="", messages=None, tools=None, timeout=TIMEOUT_S):
    messages, tools = messages or [], tools or []
    if provider["name"] == "anthropic":
        blocks = []
        for t in tools:
            blocks.append({"name": t["name"], "description": t.get("description", ""),
                           "input_schema": t.get("parameters", {"type": "object"})})
        body = {"model": provider["model"], "max_tokens": MAX_TOKENS, "system": system or "You are helpful.",
                "messages": _to_anthropic_messages(messages)}
        if blocks:
            body["tools"] = blocks
        url = provider["base_url"].rstrip("/").removesuffix("/v1") + "/v1/messages"
        headers = {"Content-Type": "application/json", "x-api-key": provider["api_key"],
                   "anthropic-version": "2023-06-01"}
        _, text = _http("POST", url, headers, body, timeout=timeout)
        data = json.loads(text)
        content, calls = [], []
        for b in data.get("content") or []:
            if b.get("type") == "text":
                content.append(b.get("text", ""))
            elif b.get("type") == "tool_use":
                calls.append({"id": b.get("id"), "name": b.get("name"), "args": b.get("input") or {}})
        return {"content": "".join(content) or None, "tool_calls": calls,
                "provider": "anthropic", "model": provider["model"]}
    body = _openai_payload(provider, system, messages, tools)
    url = provider["base_url"].rstrip("/") + "/chat/completions"
    _, text = _http("POST", url, {"Content-Type": "application/json",
                                 "Authorization": f"Bearer {provider['api_key']}"},
                   body, timeout=timeout)
    parsed = _parse_openai(json.loads(text))
    parsed.update({"provider": provider["name"], "model": provider["model"]})
    return parsed


def chat(system="", messages=None, tools=None):
    """Try each provider in priority order."""
    providers = detect_providers()
    if not providers:
        raise RuntimeError("No LLM provider configured. Set AI_API_KEY (+AI_BASE_URL/AI_MODEL) "
                           "or NINEROUTER_URL/NINEROUTER_KEY in .env")
    errors = []
    for p in providers:
        try:
            model, _ = resolve_model(p)
            p["model"] = model
            return chat_once(p, system=system, messages=messages, tools=tools)
        except Exception as e:
            errors.append(f"{p['name']}: {type(e).__name__}: {str(e)[:200]}")
    raise RuntimeError("AI request failed on all providers:\n  - " + "\n  - ".join(errors))
