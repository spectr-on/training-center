"""Повышение веса: все подходы на верху диапазона → вес растёт во всех подходах сразу на step_kg упражнения."""
import copy
import re
from datetime import datetime

from . import program, timeutil


def next_weights(weights, top_reps, actual, step):
    """weights — план по подходам; actual — [(повторы, кг)] фактических подходов упражнения по порядку.
    Условие: все плановые подходы сделаны на верх диапазона и на плановый вес (не меньше).
    Тогда вес поднимается сразу во всех подходах на step (подходы со «своим весом», 0, не трогаем);
    повторы при этом возвращаются к нижней границе диапазона — диапазон в программе не меняется,
    цель следующей тренировки — его низ. Иначе None (ничего не меняем)."""
    n = len(weights)
    if n == 0 or not any(weights) or not step or len(actual) < n:
        return None
    last = actual[-n:]
    for (reps, kg), w in zip(last, weights):
        if reps < top_reps or kg + 1e-9 < w:
            return None
    new = [round(w + step, 2) if w else w for w in weights]
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
                    step = program.step_of(ex)
                    if not step:
                        continue
                    actual = [(r, k) for c, r, k in sets if c == ex["code"]]
                    new = next_weights(ex["weights"], ex["reps"][1], actual, step)
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
