"""Прибавка веса: волна с последнего подхода по верхней границе диапазона."""
import copy
import re
from datetime import datetime

from . import program, timeutil


def next_bump(weights):
    """Индекс подхода, которому будет прибавка по правилу волны: последний среди имеющих минимальный вес.
    None, если весов нет или упражнение с собственным весом."""
    if not weights or not any(weights):
        return None
    lo = min(weights)
    return max(i for i, w in enumerate(weights) if w == lo)


def next_weights(weights, top_reps, actual, step):
    """weights — план по подходам; actual — [(повторы, кг)] фактических подходов упражнения по порядку.
    Если все плановые подходы сделаны на верх диапазона и на плановый вес (не меньше) — поднимаем на step
    последний подход среди имеющих минимальный вес. Иначе None (ничего не меняем)."""
    n = len(weights)
    if n == 0 or not any(weights) or len(actual) < n:
        return None
    last = actual[-n:]
    for (reps, kg), w in zip(last, weights):
        if reps < top_reps or kg + 1e-9 < w:
            return None
    idx = next_bump(weights)
    new = list(weights)
    new[idx] = round(weights[idx] + step, 2)
    return [int(v) if float(v).is_integer() else v for v in new]


_CHANGE = re.compile(r"([\d.]+(?:/[\d.]+)*) → ([\d.]+(?:/[\d.]+)*)\s*$")


def parse_change(text):
    """Из записи progress_log («Full Body A: КОД 90/95/95 → 95/95/95») -> ([старые веса], [новые веса]) или None."""
    m = _CHANGE.search(text or "")
    if not m:
        return None
    try:
        old = [float(x) for x in m.group(1).split("/")]
        new = [float(x) for x in m.group(2).split("/")]
    except ValueError:
        return None
    return (old, new) if len(old) == len(new) else None


def fmt(ws):
    return "/".join(f"{w:g}" for w in ws)


def apply_all(con, divisor=1000.0):
    """Просматривает новые силовые тренировки (с даты evaluate_from) и применяет прибавки.
    Если вес менялся — сохраняет новую авто-версию программы. Возвращает список событий."""
    row, data = program.active(con)
    if not data:
        return []
    data = copy.deepcopy(data)
    start = str(data.get("evaluate_from"))
    step = data["progression"]["step_kg"]
    events, changed = [], False
    acts = con.execute("SELECT * FROM activity WHERE type_key='strength_training' AND substr(start_local,1,10)>=? "
                       "ORDER BY start_local", (start,)).fetchall()
    for a in acts:
        if con.execute("SELECT 1 FROM progress_log WHERE activity_id=? AND code='*'", (a["id"],)).fetchone():
            continue
        name = (a["name"] or "").lower()
        sess = next((s for s in data["sessions"] if s["kind"] == "strength"
                     and str(s.get("match") or s["name"]).lower() in name), None)
        if sess is not None:
            sets = [(r["exercise"], r["reps"] or 0, (r["weight_raw"] or 0) / divisor) for r in con.execute(
                "SELECT * FROM workout_set WHERE activity_id=? AND set_type='ACTIVE' ORDER BY idx", (a["id"],))]
            for b in sess["blocks"]:
                for ex in b["exercises"]:
                    eq = ex.get("equipment") or program.equipment_of(ex["code"], ex["weights"])
                    if eq not in step:
                        continue
                    actual = [(r, k) for c, r, k in sets if c == ex["code"]]
                    new = next_weights(ex["weights"], ex["reps"][1], actual, step[eq])
                    if new:
                        text = f"{sess['name']}: {ex['code']} {fmt(ex['weights'])} → {fmt(new)}"
                        ex["weights"] = new
                        changed = True
                        events.append(text)
                        con.execute("INSERT OR REPLACE INTO progress_log VALUES(?,?,?,?)",
                                    (a["id"], ex["code"], timeutil.now(), text))
        con.execute("INSERT OR REPLACE INTO progress_log VALUES(?,?,?,?)",
                    (a["id"], "*", timeutil.now(),
                     "обработана" if sess is not None else "не относится к программе"))
    if changed:
        program.save_version(con, program.dump(data), "авто", "; ".join(events)[:300], True)
    con.commit()
    return events
