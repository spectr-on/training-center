"""Светофор по правилам из разбора данных (утро, сочетание показателей)."""
import json

PLANS = {
    "green": "Тяжёлая силовая по плану, можно прибавлять вес/повторы. Бег можно с темповым куском.",
    "yellow": "Силовая по плану без прибавки веса и без отказа (−15% объёма). Бег только лёгкий (зона 2).",
    "red": "Не пропуск, а замена: «Минимальная тренировка» (20–25 мин) или прогулка/лёгкий кросс 30–40 мин и мобильность. Тяжёлое переносим.",
}


def find_key(obj, key):
    """Первое не-None значение ключа в произвольно вложенном JSON."""
    if isinstance(obj, dict):
        if obj.get(key) is not None:
            return obj[key]
        for v in obj.values():
            r = find_key(v, key)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = find_key(v, key)
            if r is not None:
                return r
    return None


def _raw(con, d, kind):
    r = con.execute("SELECT json FROM daily_raw WHERE date=? AND kind=?", (d, kind)).fetchone()
    try:
        return json.loads(r["json"]) if r else None
    except Exception:
        return None


def metrics(con, d):
    stats, sleep, hrv, rd = (_raw(con, d, k) for k in ("stats", "sleep", "hrv", "readiness"))
    m = {}
    entries = [x for x in rd if isinstance(x, dict)] if isinstance(rd, list) else ([rd] if isinstance(rd, dict) else [])

    def ts(x):
        return str(x.get("timestampLocal") or x.get("timestamp") or "")
    entries.sort(key=ts)
    morning = next((x for x in entries if x.get("inputContext") == "AFTER_WAKEUP_RESET"), entries[0] if entries else None)
    latest = entries[-1] if entries else None
    m["readiness"] = find_key(morning, "score") if morning else None
    m["readiness_now"] = find_key(latest, "score") if latest else None
    m["readiness_at"] = ts(morning)[11:16] if morning else ""
    m["readiness_now_at"] = ts(latest)[11:16] if latest else ""
    sec = find_key(sleep, "sleepTimeSeconds") if sleep else None
    m["sleep_h"] = None if sec is None else round(sec / 3600, 1)
    sc = find_key(sleep, "sleepScores") if sleep else None
    m["sleep_score"] = (sc or {}).get("overall", {}).get("value") if isinstance(sc, dict) else None
    m["sleep_stress"] = find_key(sleep, "avgSleepStress") if sleep else None
    m["hrv"] = find_key(hrv, "lastNightAvg") if hrv else None
    rhr = find_key(stats, "restingHeartRate") if stats else None
    m["rhr"] = rhr if rhr is not None else (find_key(sleep, "restingHeartRate") if sleep else None)
    bb = find_key(stats, "bodyBatteryAtWakeTime") if stats else None
    m["bb_note"] = ""
    if bb is None and stats:
        bb = find_key(stats, "bodyBatteryHighestValue")
        m["bb_note"] = " (макс. за день)" if bb is not None else ""
    m["bb"] = bb
    m["bb_now"] = find_key(stats, "bodyBatteryMostRecentValue") if stats else None
    return m


def missing_morning(m):
    """Чего не хватает, чтобы вообще принимать решение: сон и утренний Readiness.
    Пока Garmin не обработал ночь (часы ещё не синхронизировались с телефоном), цвет показывать нельзя."""
    out = []
    if m.get("sleep_h") is None:
        out.append("сон")
    if m.get("readiness") is None:
        out.append("утренний Training Readiness")
    return out


def decide(m):
    """-> (цвет, [причины], [заметки о нехватке данных])"""
    flags, notes = [], []
    if m["bb"] is not None and m["bb"] < 40:
        flags.append(f"Body Battery {m['bb']} < 40")
    if m["sleep_h"] is not None and m["sleep_h"] < 6:
        flags.append(f"сон {m['sleep_h']} ч < 6")
    if m["hrv"] is not None and m["hrv"] < 36:
        flags.append(f"HRV {m['hrv']} < 36")
    if m["rhr"] is not None and m["rhr"] >= 63:
        flags.append(f"пульс покоя {m['rhr']} ≥ 63")
    for k, n in (("readiness", "Training Readiness"), ("bb", "Body Battery"), ("sleep_h", "сон"),
                 ("hrv", "HRV"), ("rhr", "пульс покоя")):
        if m[k] is None:
            notes.append(f"нет данных: {n}")
    rd = m["readiness"]
    if rd is not None and rd < 40:
        return "red", [f"Readiness {rd} < 40"] + flags, notes
    if len(flags) >= 2:
        return "red", flags, notes
    if (rd is not None and rd >= 70 and m["bb"] is not None and m["bb"] >= 80
            and m["sleep_h"] is not None and m["sleep_h"] >= 7 and not flags):
        return "green", ["Readiness 70+, Body Battery 80+, сон 7+ ч, красных флагов нет"], notes
    why = flags or ["не выполнены все условия зелёного"]
    return "yellow", why, notes
