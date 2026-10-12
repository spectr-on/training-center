"""Сборка силовой тренировки для Garmin Connect и отправка. Формат снят с реальных тренировок Garmin."""
import json
import statistics
from datetime import datetime

from .exercise_names import ru
from .garmin_weights import weight_fields

DEFAULT_REST = 90
CAT_KEYWORDS = [("TRICEPS_EXTENSION", "TRICEPS_EXTENSION"), ("LATERAL_RAISE", "LATERAL_RAISE"), ("PULL_UP", "PULL_UP"),
                ("BENCH_PRESS", "BENCH_PRESS"), ("SHOULDER_PRESS", "SHOULDER_PRESS"), ("DEADLIFT", "DEADLIFT"),
                ("SQUAT", "SQUAT"), ("CURL", "CURL"), ("ROW", "ROW")]


class WorkoutError(Exception):
    pass


# ---------- категории и отдых из данных Garmin ----------
def category_map(con):
    """код упражнения -> категория Garmin (из подходов, уже скачанных из Garmin)."""
    m = {}
    for r in con.execute("SELECT exercise, json FROM workout_set WHERE json IS NOT NULL"):
        try:
            ex = (json.loads(r["json"]).get("exercises") or [{}])[0]
        except Exception:
            continue
        if r["exercise"] and ex.get("category"):
            m[r["exercise"]] = ex["category"]
    return m


def guess_category(code):
    for kw, cat in CAT_KEYWORDS:
        if kw in code:
            return cat
    return None


def rest_by_code(con, activity_id):
    """код -> типичный отдых после подхода, округлённый до 15 с (из REST-подходов Garmin)."""
    rows = [dict(r) for r in con.execute("SELECT * FROM workout_set WHERE activity_id=? ORDER BY idx", (activity_id,))]
    acc = {}
    for i, r in enumerate(rows[:-1]):
        nxt = rows[i + 1]
        if r["set_type"] == "ACTIVE" and nxt["set_type"] == "REST" and nxt["duration"]:
            acc.setdefault(r["exercise"], []).append(nxt["duration"])
    out = {}
    for c, v in acc.items():
        sec = statistics.median(v)
        out[c] = int(min(300, max(30, round(sec / 15) * 15)))
    return out


def enrich(con, data):
    """Дополняет программу отсутствующими category и rest_sec по данным Garmin. Возвращает (кол-во изменений)."""
    cats = category_map(con)
    n = 0
    for s in data["sessions"]:
        if s["kind"] != "strength":
            continue
        a = con.execute("SELECT id FROM activity WHERE type_key='strength_training' AND lower(name) LIKE ? "
                        "ORDER BY start_local DESC LIMIT 1", (f"%{str(s.get('match') or s['name']).lower()}%",)).fetchone()
        rests = rest_by_code(con, a["id"]) if a else {}
        for b in s["blocks"]:
            for x in b["exercises"]:
                if not x.get("category"):
                    c = cats.get(x["code"]) or guess_category(x["code"])
                    if c:
                        x["category"] = c
                        n += 1
                if not x.get("rest_sec"):
                    x["rest_sec"] = rests.get(x["code"], DEFAULT_REST)
                    n += 1
    return n


# ---------- сборка ----------
SPORT = {"sportTypeId": 5, "sportTypeKey": "strength_training", "displayOrder": 3}


def _step(order, child, kind, **kw):
    types = {"interval": (3, 3), "rest": (5, 5)}
    tid, disp = types[kind]
    base = {"type": "ExecutableStepDTO", "stepOrder": order,
            "stepType": {"stepTypeId": tid, "stepTypeKey": kind, "displayOrder": disp},
            "childStepId": child, "description": None,
            "preferredEndConditionUnit": None, "endConditionCompare": None,
            "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target", "displayOrder": 1},
            "targetValueOne": None, "targetValueTwo": None, "targetValueUnit": None, "zoneNumber": None,
            "secondaryTargetType": None, "secondaryTargetValueOne": None, "secondaryTargetValueTwo": None,
            "secondaryTargetValueUnit": None, "secondaryZoneNumber": None, "endConditionZone": None,
            "strokeType": {"strokeTypeId": 0, "strokeTypeKey": None, "displayOrder": 0},
            "equipmentType": {"equipmentTypeId": 0, "equipmentTypeKey": None, "displayOrder": 0},
            "workoutProvider": None, "providerExerciseSourceId": None}
    base.update(kw)
    return base


def _chunks(exs):
    """Одна группа на блок: число кругов = число подходов, вес группы = наибольший вес упражнения
    (в Garmin у группы один вес; по подходам план хранится только в HA)."""
    n = len(exs[0]["weights"])
    return [[tuple(max(x["weights"]) for x in exs), n]]


def build_workout(session, name, cats):
    """session — словарь тренировки из программы; cats — карта категорий. Возвращает dict для upload_workout."""
    steps, order, group = [], 0, 0
    for b in session["blocks"]:
        exs = b["exercises"]
        for x in exs:
            if not (x.get("category") or cats.get(x["code"]) or guess_category(x["code"])):
                raise WorkoutError(f"Не знаю категорию Garmin для «{x['code']}». Добавь в программу строку category: у этого упражнения "
                                   "или нажми «Дополнить программу из Garmin».")
        for vec, count in _chunks(exs):
            group += 1
            order += 1
            g_order = order
            children = []
            for x, w in zip(exs, vec):
                order += 1
                wf = weight_fields(w if w else None)
                if wf["weightValue"] is None:
                    wf = {"weightValue": None, "weightUnit": None}
                children.append(_step(order, group, "interval",
                                      endCondition={"conditionTypeId": 10, "conditionTypeKey": "reps", "displayOrder": 10, "displayable": True},
                                      endConditionValue=float(x["reps"][1]),
                                      category=x.get("category") or cats.get(x["code"]) or guess_category(x["code"]),
                                      exerciseName=x["code"], **wf))
                order += 1
                children.append(_step(order, group, "rest",
                                      endCondition={"conditionTypeId": 2, "conditionTypeKey": "time", "displayOrder": 2, "displayable": True},
                                      endConditionValue=float(x.get("rest_sec") or DEFAULT_REST),
                                      category=None, exerciseName=None, weightValue=None, weightUnit=None))
            steps.append({"type": "RepeatGroupDTO", "stepOrder": g_order,
                          "stepType": {"stepTypeId": 6, "stepTypeKey": "repeat", "displayOrder": 6},
                          "childStepId": group, "numberOfIterations": count, "workoutSteps": children,
                          "endConditionValue": float(count), "preferredEndConditionUnit": None, "endConditionCompare": None,
                          "endCondition": {"conditionTypeId": 7, "conditionTypeKey": "iterations", "displayOrder": 7, "displayable": False},
                          "skipLastRestStep": False, "smartRepeat": False})
    return {"workoutName": name, "sportType": dict(SPORT), "subSportType": "GENERIC",
            "estimatedDurationInSecs": 0, "estimatedDistanceInMeters": 0.0,
            "workoutSegments": [{"segmentOrder": 1, "sportType": dict(SPORT), "workoutSteps": steps}]}


def summary(session):
    lines = []
    for b in session["blocks"]:
        parts = []
        for x in b["exercises"]:
            top = max(x["weights"])
            txt = f"{len(x['weights'])}×{x['reps'][1]} " + (f"по {top:g} кг" if top else "свой вес")
            if len(set(x["weights"])) > 1:
                txt += f" (в Garmin — наибольший вес; план по подходам: {'/'.join(f'{w:g}' for w in x['weights'])})"
            parts.append(f"{ru(x['code'])}: {txt}; отдых {x.get('rest_sec') or DEFAULT_REST} с")
        lines.append(("Суперсет — " if b["type"] == "superset" else "") + " | ".join(parts))
    return lines


def garmin_name(session):
    return session.get("garmin_name") or f"{session['name']} · ТЦ"


def send(api, con, session, cats):
    """Создаёт или обновляет НАШУ тренировку в Garmin. Чужие тренировки не трогает. -> (workout_id, режим)"""
    payload = build_workout(session, garmin_name(session), cats)
    row = con.execute("SELECT workout_id FROM garmin_workout WHERE session_key=?", (session["key"],)).fetchone()
    mode = "создана"
    wid = None
    if row:
        try:
            payload["workoutId"] = row["workout_id"]
            api.update_workout(row["workout_id"], payload)
            wid, mode = row["workout_id"], "обновлена"
        except Exception:
            payload.pop("workoutId", None)
            mode = "создана заново (обновить прежнюю не удалось)"
    if wid is None:
        res = api.upload_workout(payload)
        wid = res.get("workoutId") if isinstance(res, dict) else None
        if not wid:
            raise WorkoutError(f"Garmin не вернул номер тренировки: {str(res)[:200]}")
    con.execute("INSERT OR REPLACE INTO garmin_workout VALUES(?,?,?,?)",
                (session["key"], wid, payload["workoutName"], datetime.now().isoformat(timespec="seconds")))
    con.commit()
    return wid, mode
