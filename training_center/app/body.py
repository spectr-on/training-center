"""Вес тела, замеры лентой и ощущения после бега. Данные вводятся вручную на странице аддона."""
from datetime import date, datetime, timedelta

from . import timeutil, settings as stg

SOURCE = "manual"
MEASURE_INTERVAL_DAYS = 28          # как часто делать полные замеры лентой
RUN_TYPES = ("running", "treadmill_running", "trail_running")

# поле формы -> (подпись, минимум, максимум, обязательное)
WEIGHT_FIELDS = {
    "weight_kg": ("Вес, кг", 30, 300, True),
    "fat_pct": ("Жир, %", 2, 70, False),
    "water_pct": ("Вода, %", 20, 80, False),
    "bone_kg": ("Кости, кг", 0.5, 10, False),
    "visceral": ("Висцеральный жир, ед.", 1, 30, False),
}

# ключ -> (подпись, как мерить)
MEASURES = {
    "neck": ("Шея", "под кадыком"),
    "shoulders": ("Плечи", "вокруг тела по самым выступающим точкам дельт, руки опущены"),
    "chest": ("Грудь", "на уровне сосков, руки опущены, на выдохе"),
    "waist": ("Талия", "на уровне пупка, на выдохе, живот не втягивать"),
    "hips": ("Бёдра", "по самой выступающей части ягодиц, ноги вместе"),
    "arm": ("Бицепс (правый)", "расслабленная рука, середина между плечом и локтем"),
    "thigh": ("Бедро (правое)", "стоя, вес на обеих ногах, середина между паховой складкой и коленом"),
    "calf": ("Голень (правая)", "по самой широкой части"),
}
MEASURE_MIN, MEASURE_MAX = 5, 250

FEEL = {1: "очень тяжело", 2: "тяжело", 3: "нормально", 4: "легко", 5: "очень легко"}


def interval_days():
    try:
        v = int(stg.get("measure_interval_days", MEASURE_INTERVAL_DAYS))
        return v if v > 0 else MEASURE_INTERVAL_DAYS
    except (TypeError, ValueError):
        return MEASURE_INTERVAL_DAYS


def num(text):
    """'86,85' -> 86.85; пусто -> None; ошибка -> ValueError."""
    s = (text or "").strip().replace(",", ".")
    if not s:
        return None
    v = float(s)
    if v != v or v in (float("inf"), float("-inf")):
        raise ValueError(text)
    return v


def _parse_at(text, today):
    s = (text or "").strip().replace("T", " ")
    if not s:
        return timeutil.now()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s, fmt)
            break
        except ValueError:
            continue
    else:
        raise ValueError("дата и время должны быть в виде ГГГГ-ММ-ДД ЧЧ:ММ")
    if dt.date() > today:
        raise ValueError("дата в будущем")
    return dt.strftime(timeutil.FMT)


# ---------- вес ----------
def add_weight(con, at_text, form, today=None):
    """-> (ok, сообщение). form — словарь строк из формы."""
    today = today or timeutil.today()
    try:
        at = _parse_at(at_text, today)
    except ValueError as e:
        return False, f"Не сохранено: {e}."
    vals = {}
    for key, (label, lo, hi, required) in WEIGHT_FIELDS.items():
        try:
            v = num(form.get(key))
        except ValueError:
            return False, f"Не сохранено: «{label}» — нужно число."
        if v is None:
            if required:
                return False, f"Не сохранено: укажи «{label}»."
            continue
        if not lo <= v <= hi:
            return False, f"Не сохранено: «{label}» должно быть от {lo:g} до {hi:g}."
        vals[key] = v
    note = (form.get("note") or "").strip()[:200] or None
    con.execute("INSERT INTO body_weight(at,weight_kg,fat_pct,water_pct,bone_kg,visceral,source,note) VALUES(?,?,?,?,?,?,?,?)",
                (at, vals["weight_kg"], vals.get("fat_pct"), vals.get("water_pct"), vals.get("bone_kg"),
                 vals.get("visceral"), SOURCE, note))
    con.commit()
    return True, f"Вес записан: {vals['weight_kg']:g} кг, {at[:16]}."


def delete_weight(con, row_id):
    con.execute("DELETE FROM body_weight WHERE id=?", (row_id,))
    con.commit()


def derive(row):
    """Жировая и безжировая масса из веса и % жира (если % жира есть)."""
    d = dict(row)
    if d.get("fat_pct") is not None and d.get("weight_kg"):
        d["fat_kg"] = round(d["weight_kg"] * d["fat_pct"] / 100, 1)
        d["lean_kg"] = round(d["weight_kg"] - d["fat_kg"], 1)
    else:
        d["fat_kg"] = d["lean_kg"] = None
    return d


def weights_between(con, start, end):
    """Записи веса за период (включительно), по возрастанию времени."""
    rows = con.execute("SELECT * FROM body_weight WHERE substr(at,1,10)>=? AND substr(at,1,10)<=? ORDER BY at, id",
                       (start.isoformat(), end.isoformat())).fetchall()
    return [derive(r) for r in rows]


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def window_avg(con, today, days):
    """Среднее по дням (сначала среднее внутри дня) за последние days дней, включая сегодня.
    -> {'days': число дней с весом, 'weight','fat_pct','lean_kg'} или None."""
    rows = weights_between(con, today - timedelta(days=days - 1), today)
    per = {}
    for r in rows:
        per.setdefault(r["at"][:10], []).append(r)
    if not per:
        return None
    day = lambda k: [_mean([x[k] for x in v]) for v in per.values()]
    return {"days": len(per), "weight": _mean(day("weight_kg")), "fat_pct": _mean(day("fat_pct")),
            "lean_kg": _mean(day("lean_kg"))}


# ---------- замеры лентой ----------
def add_measures(con, day_text, form, today=None):
    """Один набор замеров на дату: повторная отправка за ту же дату дополняет и обновляет значения."""
    today = today or timeutil.today()
    day = (day_text or "").strip() or today.isoformat()
    try:
        d = date.fromisoformat(day)
    except ValueError:
        return False, "Не сохранено: дата должна быть в виде ГГГГ-ММ-ДД."
    if d > today:
        return False, "Не сохранено: дата в будущем."
    vals = {}
    for key, (label, _) in MEASURES.items():
        try:
            v = num(form.get(key))
        except ValueError:
            return False, f"Не сохранено: «{label}» — нужно число (см)."
        if v is None:
            continue
        if not MEASURE_MIN <= v <= MEASURE_MAX:
            return False, f"Не сохранено: «{label}» должно быть от {MEASURE_MIN} до {MEASURE_MAX} см."
        vals[key] = v
    if not vals:
        return False, "Не сохранено: не заполнен ни один замер."
    old = con.execute("SELECT * FROM body_measure WHERE date=?", (d.isoformat(),)).fetchone()
    merged = {k: (old[k] if old else None) for k in MEASURES}
    merged.update(vals)
    note = (form.get("note") or "").strip()[:200] or (old["note"] if old else None)
    cols = ",".join(MEASURES)
    con.execute(f"INSERT OR REPLACE INTO body_measure(date,{cols},note) VALUES(?,{','.join('?' * len(MEASURES))},?)",
                (d.isoformat(), *[merged[k] for k in MEASURES], note))
    con.commit()
    return True, f"Замеры за {d.isoformat()} записаны ({len(vals)} из {len(MEASURES)})."


def delete_measures(con, day):
    con.execute("DELETE FROM body_measure WHERE date=?", (day,))
    con.commit()


def last_measures(con, limit=3):
    """Последние наборы замеров, по возрастанию даты."""
    rows = con.execute("SELECT * FROM body_measure ORDER BY date DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in reversed(rows)]


def measure_status(con, today=None):
    """-> (пора ли, дата последних замеров или None, дней прошло или None, интервал)."""
    today = today or timeutil.today()
    iv = interval_days()
    r = con.execute("SELECT MAX(date) AS d FROM body_measure").fetchone()
    if not r or not r["d"]:
        return True, None, None, iv
    days = (today - date.fromisoformat(r["d"])).days
    return days >= iv, r["d"], days, iv


# ---------- ощущения после бега ----------
def pending_feel(con, today=None, days=10):
    """Пробежки за последние days дней, по которым ещё не отмечено, как ощущались."""
    today = today or timeutil.today()
    since = (today - timedelta(days=days)).isoformat()
    q = ",".join("?" * len(RUN_TYPES))
    return con.execute(f"SELECT id,start_local,name FROM activity WHERE type_key IN ({q}) AND substr(start_local,1,10)>=? "
                       "AND id NOT IN (SELECT activity_id FROM run_feel) ORDER BY start_local DESC",
                       (*RUN_TYPES, since)).fetchall()


def set_feel(con, activity_id, score, note=""):
    try:
        score = int(score)
        activity_id = int(activity_id)
    except (TypeError, ValueError):
        return False, "Не сохранено: выбери оценку."
    if score not in FEEL:
        return False, "Не сохранено: оценка от 1 до 5."
    q = ",".join("?" * len(RUN_TYPES))
    if not con.execute(f"SELECT 1 FROM activity WHERE id=? AND type_key IN ({q})", (activity_id, *RUN_TYPES)).fetchone():
        return False, "Не сохранено: такой пробежки нет."
    con.execute("INSERT OR REPLACE INTO run_feel(activity_id,score,note,at) VALUES(?,?,?,?)",
                (activity_id, score, (note or "").strip()[:200] or None, timeutil.now()))
    con.commit()
    return True, f"Записано: {FEEL[score]}."


def feel_of(con, activity_id):
    r = con.execute("SELECT score,note FROM run_feel WHERE activity_id=?", (activity_id,)).fetchone()
    return (r["score"], r["note"]) if r else None
