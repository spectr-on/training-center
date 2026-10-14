"""Программа тренировок: формат YAML, проверка, версии, черновик из Garmin."""
import json
from datetime import date, datetime

import yaml

from . import timeutil
from .blocks import group_blocks
from . import garmin_workout as gw

STEP_DEFAULT = {"barbell": 5, "dumbbell": 2.5}

# Бег: необязательные поля level (нагрузка), zone (пульсовая зона Garmin 1–5), duration_min, distance_km.
# Что не задано — в интерфейсе прочерк. Значение level принимается по-русски и по-английски, ё не обязательна.
RUN_LEVELS = {"легкий": ("easy", "Лёгкий"), "лёгкий": ("easy", "Лёгкий"), "easy": ("easy", "Лёгкий"),
              "обычный": ("norm", "Обычный"), "normal": ("norm", "Обычный"),
              "интенсивный": ("hard", "Интенсивный"), "hard": ("hard", "Интенсивный")}


def run_level(v):
    """-> (css-класс, подпись) или None, если значение не распознано."""
    return RUN_LEVELS.get(str(v).strip().lower()) if v is not None else None


def now():
    return timeutil.now()


def equipment_of(code, weights):
    c = (code or "").upper()
    if not any(w for w in weights):
        return "bodyweight"
    if "BARBELL" in c:
        return "barbell"
    if "DUMBBELL" in c:
        return "dumbbell"
    return "other"


def dump(data):
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=None, width=100)


def parse(text):
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        return None, [f"Ошибка YAML: {e}"]
    errs = validate(data)
    return (data if not errs else None), errs


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def validate(d):
    e = []
    if not isinstance(d, dict):
        return ["Программа должна быть словарём (ключи: program, progression, sessions)."]
    prog = d.get("progression") or {}
    step = prog.get("step_kg") or {}
    for k in ("barbell", "dumbbell"):
        if not (_num(step.get(k)) and step[k] > 0):
            e.append(f"progression.step_kg.{k}: нужно положительное число (кг)")
    ef = d.get("evaluate_from")
    try:
        date.fromisoformat(str(ef))
    except Exception:
        e.append("evaluate_from: нужна дата ГГГГ-ММ-ДД (с какого дня учитывать тренировки для прибавки веса)")
    sessions = d.get("sessions")
    if not isinstance(sessions, list) or not sessions:
        return e + ["sessions: нужен непустой список тренировок"]
    keys = set()
    for si, s in enumerate(sessions, 1):
        w = f"Тренировка {si}"
        if not isinstance(s, dict):
            e.append(f"{w}: должна быть словарём"); continue
        k = s.get("key")
        w = f"Тренировка «{k or si}»"
        if not k or k in keys:
            e.append(f"{w}: нужен уникальный key")
        keys.add(k)
        if s.get("kind") not in ("strength", "run"):
            e.append(f"{w}: kind должен быть strength или run")
        if not s.get("name"):
            e.append(f"{w}: нужно name")
        day = s.get("day")
        if day is not None and not (isinstance(day, int) and 0 <= day <= 6):
            e.append(f"{w}: day — число 0 (пн) … 6 (вс) или пусто (любой день)")
        if s.get("kind") == "run":
            if s.get("level") is not None and run_level(s["level"]) is None:
                e.append(f"{w}: level — лёгкий, обычный или интенсивный")
            z = s.get("zone")
            if z is not None and not (isinstance(z, int) and not isinstance(z, bool) and 1 <= z <= 5):
                e.append(f"{w}: zone — номер пульсовой зоны Garmin, число 1–5")
            for k, name, hi in (("duration_min", "время в минутах", 1000), ("distance_km", "расстояние в километрах", 1000)):
                if s.get(k) is not None and not (_num(s[k]) and 0 < s[k] <= hi):
                    e.append(f"{w}: {k} — {name}, положительное число")
        if s.get("kind") != "strength":
            continue
        seen = set()
        blocks = s.get("blocks")
        if not isinstance(blocks, list) or not blocks:
            e.append(f"{w}: нужен непустой список blocks"); continue
        for bi, b in enumerate(blocks, 1):
            exs = (b or {}).get("exercises") if isinstance(b, dict) else None
            if (b or {}).get("type") not in ("single", "superset") or not isinstance(exs, list) or not exs:
                e.append(f"{w}, блок {bi}: type single/superset и список exercises"); continue
            if b["type"] == "single" and len(exs) != 1:
                e.append(f"{w}, блок {bi}: single — ровно одно упражнение")
            if b["type"] == "superset" and len(exs) < 2:
                e.append(f"{w}, блок {bi}: superset — минимум два упражнения")
            for x in exs:
                c = (x or {}).get("code") if isinstance(x, dict) else None
                n = f"{w}, {c or '?'}"
                if not c:
                    e.append(f"{w}, блок {bi}: у упражнения нет code"); continue
                if c in seen:
                    e.append(f"{n}: упражнение дважды в одной тренировке")
                seen.add(c)
                r = x.get("reps")
                if not (isinstance(r, list) and len(r) == 2 and all(isinstance(v, int) and v > 0 for v in r) and r[0] <= r[1]):
                    e.append(f"{n}: reps — диапазон [мин, макс], например [6, 8]")
                ws = x.get("weights")
                if not (isinstance(ws, list) and ws and all(_num(v) and v >= 0 for v in ws)):
                    e.append(f"{n}: weights — список весов по подходам в кг (0 = свой вес)")
                rs = x.get("rest_sec")
                if rs is not None and not (isinstance(rs, int) and 0 < rs <= 900):
                    e.append(f"{n}: rest_sec — отдых после подхода, секунды (1–900)")
            if b["type"] == "superset":
                lens = {len(x.get("weights") or []) for x in exs if isinstance(x, dict)}
                if len(lens) > 1:
                    e.append(f"{w}, блок {bi}: в суперсете у всех упражнений должно быть одинаковое число подходов (weights)")
    return e


def slots_of(data, day_overrides=None):
    out = []
    for s in data["sessions"]:
        day = s.get("day")
        if day_overrides and s["key"] in day_overrides:
            day = day_overrides[s["key"]]
        out.append({"key": s["key"], "label": s["name"], "kind": s["kind"],
                    "pattern": str(s.get("match") or s["name"]).lower(), "day": day})
    return out


# ---------- версии ----------
def active(con):
    r = con.execute("SELECT * FROM program_version WHERE active=1 ORDER BY id DESC LIMIT 1").fetchone()
    if not r:
        return None, None
    return r, yaml.safe_load(r["yaml"])


def save_version(con, text, kind, note, make_active):
    if make_active:
        con.execute("UPDATE program_version SET active=0")
    cur = con.execute("INSERT INTO program_version(created_at,kind,note,yaml,active) VALUES(?,?,?,?,?)",
                      (now(), kind, note, text, 1 if make_active else 0))
    con.commit()
    return cur.lastrowid


def rollback(con, version_id):
    r = con.execute("SELECT * FROM program_version WHERE id=?", (version_id,)).fetchone()
    if not r:
        return None
    return save_version(con, r["yaml"], "откат", f"откат к версии {version_id}", True)


# ---------- черновик из Garmin ----------
def build_draft(con, divisor=1000.0):
    data = {"program": "Full Body",
            "evaluate_from": timeutil.today().isoformat(),
            "progression": {"rule": "волна с последнего подхода", "step_kg": dict(STEP_DEFAULT)},
            "sessions": []}
    missing = []
    for key, pat in (("A", "full body a"), ("B", "full body b")):
        a = con.execute("SELECT * FROM activity WHERE type_key='strength_training' AND lower(name) LIKE ? "
                        "ORDER BY start_local DESC LIMIT 1", (f"%{pat}%",)).fetchone()
        if not a:
            missing.append(f"Full Body {key}")
            continue
        sets = [dict(exercise=r["exercise"], reps=r["reps"] or 0, kg=(r["weight_raw"] or 0) / divisor)
                for r in con.execute("SELECT * FROM workout_set WHERE activity_id=? AND set_type='ACTIVE' ORDER BY idx", (a["id"],))]
        rests = gw.rest_by_code(con, a["id"])
        cats = gw.category_map(con)
        blocks = []
        for b in group_blocks(sets):
            codes = []
            for x in b["items"]:
                if x["exercise"] not in codes:
                    codes.append(x["exercise"])
            exs = []
            for c in codes:
                mine = [x for x in b["items"] if x["exercise"] == c]
                top = max(x["reps"] for x in mine)
                ws = [round(x["kg"], 2) if round(x["kg"], 2) % 1 else int(round(x["kg"])) for x in mine]
                ex = {"code": c, "equipment": equipment_of(c, ws), "reps": [max(1, top - 2), top], "weights": ws,
                      "rest_sec": rests.get(c, gw.DEFAULT_REST)}
                cat = cats.get(c) or gw.guess_category(c)
                if cat:
                    ex["category"] = cat
                exs.append(ex)
            blocks.append({"type": b["kind"], "exercises": exs})
        data["sessions"].append({"key": key, "name": f"Full Body {key}", "kind": "strength",
                                 "day": 0 if key == "A" else 4, "blocks": blocks,
                                 "source": f"черновик из тренировки Garmin от {a['start_local'][:10]}"})
    data["sessions"].append({"key": "RUN", "name": "Бег", "kind": "run"})
    if missing:
        return None, [f"Не нашёл в Garmin тренировку: {', '.join(missing)} (название должно содержать «Full Body A/B»)."]
    return data, []
