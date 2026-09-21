"""Persistent JSON memory (CRAG memory.js port). Stored in Postgres when
DATABASE_URL is set, otherwise in data/memory.json. Injected into the system
prompt on every agent turn."""
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
MEM_FILE = BASE_DIR / "data" / "memory.json"

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
try:
    import db
except Exception:  # keep the agent importable even without the db module
    db = None


class Memory:
    def __init__(self, path=None):
        self.file = Path(path) if path else MEM_FILE
        self.use_db = db is not None and db.enabled() and path is None
        self.data = {}
        self.load()

    def load(self):
        if self.use_db:
            self.data = db.load("memory") or {}
            return self
        try:
            self.data = json.loads(self.file.read_text(encoding="utf-8"))
        except Exception:
            self.data = {}
        return self

    def save(self):
        if self.use_db and db.put("memory", self.data):
            return
        self.file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.file)

    def set(self, key, value):
        self.data[str(key).strip()] = str(value or "")

    def get(self, key):
        return self.data.get(str(key).strip())

    def remove(self, key):
        self.data.pop(str(key).strip(), None)

    def all(self):
        return dict(self.data)


def memory_tools(memory):
    return [
        {"name": "remember",
         "description": "Save a long-term note (user prefs, lessons, symbol watchlist). Survives restarts. Short clear key.",
         "parameters": {"type": "object",
                        "properties": {"key": {"type": "string"}, "value": {"type": "string"}},
                        "required": ["key", "value"], "additionalProperties": False},
         "run": lambda key, value: _remember(memory, key, value)},
        {"name": "recall",
         "description": "Read long-term notes. No key = all notes, with key = that one.",
         "parameters": {"type": "object",
                        "properties": {"key": {"type": "string", "description": "(optional) which note"}},
                        "additionalProperties": False},
         "run": lambda key="": _recall(memory, key)},
    ]


def _remember(memory, key, value):
    if not (key or "").strip():
        return "Error: empty key"
    memory.set(key, value)
    memory.save()
    return f"Saved: {key.strip()} ({'postgres' if memory.use_db else 'data/memory.json'})"


def _recall(memory, key=""):
    if (key or "").strip():
        v = memory.get(key)
        return f"{key.strip()}: {v}" if v is not None else f'(no note "{key.strip()}")'
    if not memory.all():
        return "(no notes yet — use remember)"
    return "\n".join(f"{k}: {v}" for k, v in memory.all().items())
