"""Scanner tools exposed to the LLM agent (CRAG tools.js port).

Each tool: {name, description, parameters, run}. The loop calls run(**args)
synchronously; run_scan executes the asyncio scanner via asyncio.run().
"""
import asyncio

from .skills import list_skills


def scanner_tools(load_config, save_config, scan_market, compute_signal,
                  update_state, save_state, load_state, format_report,
                  fetch_tickers=None, sync_symbols=None):
    if sync_symbols is None:
        def sync_symbols(c):  # noqa: F811
            return c
    def _cfg():
        return load_config()

    def _status():
        cfg = _cfg()
        st = load_state()
        return (f"symbols={cfg.get('symbols')} tfs={','.join(cfg.get('timeframes', []))} "
                f"scan={cfg.get('scan_interval_sec')}s report={cfg.get('report_interval_sec')}s "
                f"min_conf={cfg.get('min_confidence')} tf_min={cfg.get('tf_min_confidence')} "
                f"min_agree={cfg.get('min_agreeing_strategies')} confirm={cfg.get('signal_scans_confirm')} "
                f"paused={st.get('paused')} scans={st.get('scan_count', 0)} "
                f"signals={st.get('signal_count', 0)} last_scan={st.get('last_scan_time')} "
                f"dir={st.get('current_direction')} conf={st.get('current_confidence')}")

    def _scan(timeframes_override=""):
        cfg = _cfg()
        if (timeframes_override or "").strip():
            cfg["timeframes"] = [t.strip() for t in timeframes_override.split(",") if t.strip()]
        st = load_state()

        async def _go():
            import aiohttp
            async with aiohttp.ClientSession() as session:
                symbols, results = await scan_market(session, cfg, st)
                price_map = {}
                if fetch_tickers is not None:
                    try:
                        ticks = await fetch_tickers(session, cfg)
                        if isinstance(ticks, dict):
                            for k, v in ticks.items():
                                try:
                                    price_map[k] = float(v)
                                except Exception:
                                    pass
                    except Exception:
                        pass
            return symbols, results, price_map

        symbols, results, price_map = asyncio.run(_go())
        signals, info = compute_signal(cfg, st, symbols, results)
        direction = {1: "LONG", -1: "SHORT"}.get(info.get("direction", 0), "NEUTRAL")
        update_state(st, info.get("direction", 0), info.get("confidence", 0))
        save_state(st)
        try:
            rep = format_report(cfg, st, symbols, signals, info, price_map)
        except TypeError:
            rep = format_report(cfg, st, symbols, signals, info)
        top = [f"{s.get('symbol')} {s.get('direction')} conf={s.get('confidence')}" for s in (signals or [])[:8]]
        return (f"direction={direction} confidence={info.get('confidence', 0):.1f} "
                f"agreeing={info.get('agreeing_strategies', 0)} "
                f"symbols={len(symbols)} signals={len(signals or [])} " +
                ("top=[%s] " % "; ".join(top) if top else "") +
                f"\n{rep[:2500]}")

    def _set_symbol(symbols):
        cfg = _cfg()
        v = (symbols or "").strip()
        cfg["symbols"] = "ALL" if v.upper() == "ALL" else [s.strip() for s in v.split(",") if s.strip()]
        cfg["symbol"] = cfg["symbols"][0] if isinstance(cfg["symbols"], list) and len(cfg["symbols"]) == 1 else ("ALL" if cfg["symbols"] == "ALL" else cfg["symbols"])
        sync_symbols(cfg)
        save_config(cfg)
        return f"symbols={cfg['symbols']} symbol={cfg.get('symbol')}"

    def _set_timeframes(timeframes):
        cfg = _cfg()
        cfg["timeframes"] = [t.strip() for t in (timeframes or "").split(",") if t.strip()]
        save_config(cfg)
        return f"timeframes={','.join(cfg['timeframes'])}"

    def _set_scan_interval(seconds):
        cfg = _cfg()
        cfg["scan_interval_sec"] = int(seconds)
        save_config(cfg)
        return f"scan_interval_sec={cfg['scan_interval_sec']}"

    def _set_report_interval(seconds):
        cfg = _cfg()
        cfg["report_interval_sec"] = int(seconds)
        save_config(cfg)
        return f"report_interval_sec={cfg['report_interval_sec']}"

    def _set_thresholds(min_confidence=None, tf_min_confidence=None,
                        min_agreeing=None, confirm=None):
        cfg = _cfg()
        if min_confidence is not None:
            cfg["min_confidence"] = float(min_confidence)
        if tf_min_confidence is not None:
            cfg["tf_min_confidence"] = float(tf_min_confidence)
        if min_agreeing is not None:
            cfg["min_agreeing_strategies"] = int(min_agreeing)
        if confirm is not None:
            cfg["signal_scans_confirm"] = int(confirm)
        save_config(cfg)
        return (f"min_conf={cfg.get('min_confidence')} tf_min={cfg.get('tf_min_confidence')} "
                f"min_agree={cfg.get('min_agreeing_strategies')} confirm={cfg.get('signal_scans_confirm')}")

    def _load_skill(skill_id):
        for s in list_skills():
            if s["id"] == skill_id or s["name"] == skill_id:
                return f"### {s['name']}\n{s['body']}"[:6000]
        avail = ", ".join(s["id"] for s in list_skills()) or "(none)"
        return f"Unknown skill '{skill_id}'. Available: {avail}"

    num = {"type": "number"}
    req = lambda *a: {"type": "object", "properties": {}, "additionalProperties": False}  # placeholder
    return [
        {"name": "status", "description": "Current scanner config + runtime state (no network).",
         "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
         "run": lambda: _status()},
        {"name": "run_scan", "description": "Run one live multi-timeframe market scan and return the report.",
         "parameters": {"type": "object", "properties": {
             "timeframes_override": {"type": "string", "description": "(optional) e.g. 1m,5m,15m"}}, "additionalProperties": False},
         "run": lambda timeframes_override="": _scan(timeframes_override)},
        {"name": "set_symbol", "description": "Persist scan universe: ALL or comma list e.g. BTC_USDT,ETH_USDT.",
         "parameters": {"type": "object", "properties": {"symbols": {"type": "string"}},
                        "required": ["symbols"], "additionalProperties": False},
         "run": lambda symbols: _set_symbol(symbols)},
        {"name": "set_timeframes", "description": "Persist scan timeframes, e.g. 1m,3m,5m,15m.",
         "parameters": {"type": "object", "properties": {"timeframes": {"type": "string"}},
                        "required": ["timeframes"], "additionalProperties": False},
         "run": lambda timeframes: _set_timeframes(timeframes)},
        {"name": "set_scan_interval", "description": "Persist scan interval seconds.",
         "parameters": {"type": "object", "properties": {"seconds": num},
                        "required": ["seconds"], "additionalProperties": False},
         "run": lambda seconds: _set_scan_interval(seconds)},
        {"name": "set_report_interval", "description": "Persist report interval seconds.",
         "parameters": {"type": "object", "properties": {"seconds": num},
                        "required": ["seconds"], "additionalProperties": False},
         "run": lambda seconds: _set_report_interval(seconds)},
        {"name": "set_thresholds", "description": "Persist signal thresholds (any subset).",
         "parameters": {"type": "object", "properties": {
             "min_confidence": num, "tf_min_confidence": num,
             "min_agreeing": num, "confirm": num}, "additionalProperties": False},
         "run": lambda min_confidence=None, tf_min_confidence=None, min_agreeing=None, confirm=None:
             _set_thresholds(min_confidence, tf_min_confidence, min_agreeing, confirm)},
        {"name": "load_skill", "description": "Read a skill playbook from skills/ fully.",
         "parameters": {"type": "object", "properties": {"skill_id": {"type": "string"}},
                        "required": ["skill_id"], "additionalProperties": False},
         "run": lambda skill_id: _load_skill(skill_id)},
    ]
