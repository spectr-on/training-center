import json
from pathlib import Path

FILE = Path("/data/settings.json")
DEFAULT = {"slots": [
    {"key": "A", "label": "День A (Full Body A)", "kind": "strength", "pattern": "full body a", "day": 0},
    {"key": "B", "label": "День B (Full Body B)", "kind": "strength", "pattern": "full body b", "day": 4},
    {"key": "RUN", "label": "Бег", "kind": "run", "pattern": "", "day": 2},
]}


def load():
    try:
        d = json.loads(FILE.read_text(encoding="utf-8"))
        if isinstance(d.get("slots"), list):
            return d
    except Exception:
        pass
    return json.loads(json.dumps(DEFAULT))


def save(d):
    FILE.parent.mkdir(parents=True, exist_ok=True)
    FILE.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


def day_overrides():
    d = load()
    return d.get("days") or {}


def set_days(days):
    d = load()
    d["days"] = days
    save(d)


def get(key, default=None):
    return load().get(key, default)


def set_value(key, value):
    d = load()
    d[key] = value
    save(d)
