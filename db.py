"""Postgres-backed store for scanner config / scan state / agent memory.

Enabled automatically when DATABASE_URL is set (Neon, Railway Postgres, any
standard Postgres). When it is NOT set the app keeps using the plain JSON files,
so local runs and `python3 scanner.py --once` in GitHub Actions still work
without any database.

Schema is deliberately a key/value table rather than real columns:

    CREATE TABLE kv (
        store      TEXT NOT NULL,   -- 'config' | 'state' | 'memory'
        key        TEXT NOT NULL,
        value      JSONB NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (store, key)
    );

Config keys come and go (thresholds, intervals, symbols) and agent memory is
free-form user notes, so one JSONB row per key absorbs all of that without
migrations. Writes are per-key upserts, which also means two writers (Railway
supervisor + a GitHub Actions --once run) can touch different keys without
clobbering each other's whole document.
"""
import json
import os
import threading

_LOCK = threading.Lock()
_CONN = None
_WARNED = False

_DDL = [
    """CREATE TABLE IF NOT EXISTS kv (
           store      TEXT NOT NULL,
           key        TEXT NOT NULL,
           value      JSONB NOT NULL,
           updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
           PRIMARY KEY (store, key)
       )""",
    "CREATE INDEX IF NOT EXISTS kv_store_idx ON kv (store)",
]


def dsn():
    """Connection string, tolerating the names different providers inject."""
    for name in ("DATABASE_URL", "POSTGRES_URL", "NEON_DATABASE_URL"):
        v = (os.environ.get(name) or "").strip()
        if v:
            return v
    return ""


def enabled():
    return bool(dsn())


def backend():
    return "postgres" if enabled() else "files"


def target():
    """Safe display label for the DSN — never exposes user/password."""
    if not enabled():
        return ""
    try:
        from urllib.parse import urlsplit
        u = urlsplit(dsn())
        host = u.hostname or "?"
        name = (u.path or "").lstrip("/") or "?"
        if u.port:
            host = f"{host}:{u.port}"
        return f"{host}/{name}"
    except Exception:
        return "configured"


def _warn(exc):
    global _WARNED
    if not _WARNED:
        _WARNED = True
        print(f"[db] Postgres unavailable ({type(exc).__name__}: {exc}); "
              f"falling back to local JSON files")


def _conn():
    """Lazily connect + ensure schema. Returns a live connection."""
    global _CONN
    import psycopg
    if _CONN is not None and not _CONN.closed:
        return _CONN
    _CONN = psycopg.connect(dsn(), autocommit=True, connect_timeout=15)
    with _CONN.cursor() as cur:
        for stmt in _DDL:
            cur.execute(stmt)
    return _CONN


def load(store, default=None):
    """Whole store as a dict. Returns `default` when empty or unavailable."""
    if not enabled():
        return default
    with _LOCK:
        try:
            with _conn().cursor() as cur:
                cur.execute("SELECT key, value FROM kv WHERE store = %s", (store,))
                rows = cur.fetchall()
        except Exception as e:
            _warn(e)
            global _CONN
            _CONN = None
            return default
    return {k: v for k, v in rows} if rows else default


def put(store, data):
    """Upsert every key of `data`. True on success, False if it fell back."""
    if not enabled() or not data:
        return False
    with _LOCK:
        try:
            with _conn().cursor() as cur:
                cur.executemany(
                    "INSERT INTO kv (store, key, value, updated_at) "
                    "VALUES (%s, %s, %s::jsonb, now()) "
                    "ON CONFLICT (store, key) DO UPDATE "
                    "SET value = EXCLUDED.value, updated_at = now()",
                    [(store, str(k), json.dumps(v, ensure_ascii=False))
                     for k, v in data.items()],
                )
            return True
        except Exception as e:
            _warn(e)
            global _CONN
            _CONN = None
            return False
