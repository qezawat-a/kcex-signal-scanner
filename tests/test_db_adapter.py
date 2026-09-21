"""Verify the db adapter: SQL validity (Postgres dialect) + fallback behaviour.

Postgres cannot start inside the PRoot sandbox (shmget -> ENOSYS), so the SQL is
parsed with sqlglot's postgres dialect and the adapter logic is exercised
against a fake connection that records statements and can be made to fail.
"""
import json
import os
import sys

sys.path.insert(0, "/var/minis/workspace/kcex-signal-scanner")

import sqlglot  # noqa: E402
import db  # noqa: E402

fails = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  {detail}" if detail else ""))
    if not cond:
        fails.append(name)


# --- 1. SQL parses as valid Postgres -----------------------------------------
for stmt in db._DDL:
    try:
        sqlglot.parse_one(stmt, dialect="postgres")
        check(f"DDL parses: {stmt.splitlines()[0][:40]}...", True)
    except Exception as e:
        check("DDL parses", False, f"{type(e).__name__}: {e}")

for stmt in (
    "INSERT INTO kv (store, key, value, updated_at) "
    "VALUES (%s, %s, %s::jsonb, now()) "
    "ON CONFLICT (store, key) DO UPDATE "
    "SET value = EXCLUDED.value, updated_at = now()",
    "SELECT key, value FROM kv WHERE store = %s",
):
    try:
        sqlglot.parse_one(stmt, dialect="postgres")
        check(f"DML parses: {stmt[:45]}...", True)
    except Exception as e:
        check("DML parses", False, f"{type(e).__name__}: {e}")


# --- 2. Disabled when no DSN ------------------------------------------------
for v in ("DATABASE_URL", "POSTGRES_URL", "NEON_DATABASE_URL"):
    os.environ.pop(v, None)
check("no DSN -> enabled() False", db.enabled() is False)
check("no DSN -> backend 'files'", db.backend() == "files")
check("no DSN -> load returns default", db.load("config", default={"a": 1}) == {"a": 1})
check("no DSN -> put returns False (caller falls back to file)", db.put("config", {"a": 1}) is False)


# --- 3. Fake connection: exercise the real code paths -----------------------
class FakeCur:
    def __init__(self, conn):
        self.conn = conn
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.conn.executed.append((sql, params))
        if self.conn.raise_on and self.conn.raise_on in sql:
            raise RuntimeError("simulated connection loss")
        if sql.strip().startswith("SELECT"):
            self._rows = list(self.conn.stored.items())

    def executemany(self, sql, seq):
        self.conn.executed.append((sql, list(seq)))
        if self.conn.raise_on and self.conn.raise_on in sql:
            raise RuntimeError("simulated connection loss")
        for store, key, value in seq:
            self.conn.stored[(key,)] = json.loads(value)

    def fetchall(self):
        return self._rows


class FakeConn:
    def __init__(self, raise_on=None):
        self.executed = []
        self.stored = {}
        self.raise_on = raise_on
        self.closed = False
        self.autocommit = False

    def cursor(self):
        return FakeCur(self)


def with_fake(conn):
    db._CONN = conn
    db._WARNED = False


os.environ["DATABASE_URL"] = "postgresql://u:p@localhost:5432/neondb"
check("DSN set -> enabled() True", db.enabled() is True)
check("DSN set -> backend 'postgres'", db.backend() == "postgres")

# put() issues one upsert per key, JSON-encoded
conn = FakeConn()
with_fake(conn)
ok = db.put("config", {"symbols": ["SAGA_USDT"], "min_confidence": 70})
check("put() reports success", ok is True)
check("put() issues one INSERT with ON CONFLICT", "ON CONFLICT" in conn.executed[0][0])
check("put() sends one row per key", len(conn.executed[0][1]) == 2)
vals = {k: json.loads(v) for _, k, v in conn.executed[0][1]}
check("put() round-trips list value", vals["symbols"] == ["SAGA_USDT"], str(vals))
check("put() round-trips numeric value", vals["min_confidence"] == 70)

# load() reads the store back
conn2 = FakeConn()
with_fake(conn2)
conn2.stored = {"symbols": ["SAGA_USDT"], "min_confidence": 70}
got = db.load("config")
check("load() returns rows as dict", got == {"symbols": ["SAGA_USDT"], "min_confidence": 70}, str(got))
check("load() targets the right store", "%s" in conn2.executed[0][0] and conn2.executed[0][1] == ("config",))

# empty store -> falls back to caller default
conn3 = FakeConn()
with_fake(conn3)
check("empty store -> default", db.load("state", default={"fresh": True}) == {"fresh": True})

# DB down -> graceful fallback, no exception, one warning
conn4 = FakeConn(raise_on="INSERT")
with_fake(conn4)
check("DB error -> put() returns False (no raise)", db.put("config", {"a": 1}) is False)
check("DB error -> connection reset for reconnect", db._CONN is None)
conn5 = FakeConn(raise_on="SELECT")
with_fake(conn5)
check("DB error -> load() returns default (no raise)", db.load("config", default={"x": 2}) == {"x": 2})

# --- 4. Scanner integration: DB wins over file, cfg not persisted ----------
os.environ.pop("DATABASE_URL", None)
import scanner  # noqa: E402

FIXTURE = {"symbol": "BTC_USDT", "symbols": ["SAGA_USDT"]}
for name, val in FIXTURE.items():
    pass

# simulate "what is in the repo file" vs "what is in the DB"
scanner.db.load = lambda store, default=None: (
    {"symbols": ["SOL_USDT"]} if store == "config" else ({"scan_count": 99} if store == "state" else default)
)
scanner.db.put = lambda store, data: True
scanner.db.enabled = lambda: True

cfg = scanner.load_config()
check("DB config overrides repo file", cfg["symbols"] == ["SOL_USDT"], str(cfg["symbols"]))
check("derived symbol follows DB symbols", cfg["symbol"] == "SOL_USDT", cfg["symbol"])

st = scanner.load_state()
check("state read from DB", st.get("scan_count") == 99, str(st.get("scan_count")))

captured = {}


def fake_put(store, data):
    captured[store] = data
    return True


scanner.db.put = fake_put
scanner.save_state({"scan_count": 100, "cfg": {"symbols": ["SOL_USDT"]}})
check("save_state drops transient 'cfg' key", "cfg" not in captured.get("state", {}), str(captured.get("state")))
check("save_state keeps real counters", captured["state"].get("scan_count") == 100)

scanner.save_config({"symbols": ["SOL_USDT"], "min_confidence": 70})
check("save_config writes to DB, not file", captured["config"]["symbols"] == ["SOL_USDT"])
check("save_config does not write config.json",
      json.load(open("/var/minis/workspace/kcex-signal-scanner/config.json"))["symbols"] == ["SAGA_USDT"])

# --- 5. Agent memory backed by DB ------------------------------------------
import agent.memory as amem  # noqa: E402

amem.db = db
db.enabled = lambda: True
db.load = lambda store, default=None: ({"note1": "hello"} if store == "memory" else default)
saved = {}
db.put = lambda store, data: (saved.update({store: dict(data)}) or True)

m = amem.Memory()
check("Memory() loads from DB when enabled", m.data == {"note1": "hello"}, str(m.data))
check("Memory.use_db True", m.use_db is True)
m.set("note2", "world")
m.save()
check("Memory.save() writes to DB", saved.get("memory", {}).get("note2") == "world", str(saved))
check("Memory.save() did not create data/memory.json",
      not os.path.exists("/var/minis/workspace/kcex-signal-scanner/data/memory.json"))

# explicit path must keep using the file (tests/local tooling)
m2 = amem.Memory(path="/tmp/mem_test.json")
check("explicit path -> file backend", m2.use_db is False)

print()
print("FAILURES:", fails if fails else "none")
sys.exit(1 if fails else 0)
