"""Недельный план: слот считается выполненным в любой день недели (статус moved, если не в плановый)."""
from datetime import date, timedelta

RUN_TYPES = {"running", "treadmill_running", "trail_running"}
LABEL = {"done": "выполнено", "moved": "выполнено (перенос)", "today": "сегодня", "planned": "впереди",
         "late": "просрочено — можно ещё в эту неделю", "missed": "пропущено"}


def monday(d):
    return d - timedelta(days=d.weekday())


def week_acts(con, mon):
    a, b = mon.isoformat(), (mon + timedelta(days=7)).isoformat()
    return [dict(r) for r in con.execute(
        "SELECT id,start_local,type_key,name FROM activity WHERE substr(start_local,1,10)>=? AND substr(start_local,1,10)<? ORDER BY start_local", (a, b))]


def week_status(con, settings, mon, today):
    acts = week_acts(con, mon)
    slots = settings["slots"]
    found = {}
    used = set()
    for s in slots:  # 1) по названию тренировки
        for a in acts:
            if a["id"] in used or s["kind"] != "strength" or a["type_key"] != "strength_training":
                continue
            if s["pattern"] and s["pattern"] in (a["name"] or "").lower():
                found[s["key"]] = a; used.add(a["id"]); break
    for s in slots:  # 2) остальные: по порядку
        if s["key"] in found:
            continue
        for a in acts:
            if a["id"] in used:
                continue
            ok = (s["kind"] == "strength" and a["type_key"] == "strength_training") or \
                 (s["kind"] == "run" and a["type_key"] in RUN_TYPES)
            if ok:
                found[s["key"]] = a; used.add(a["id"]); break
    out = []
    for s in slots:
        planned = mon + timedelta(days=s["day"])
        a = found.get(s["key"])
        if a:
            done_day = date.fromisoformat(a["start_local"][:10])
            st = "done" if done_day == planned else "moved"
            when = done_day
        else:
            when = planned
            week_over = today > mon + timedelta(days=6)
            if week_over:
                st = "missed"
            elif planned == today:
                st = "today"
            elif planned < today:
                st = "late"
            else:
                st = "planned"
        out.append({"slot": s, "status": st, "date": when, "planned": planned})
    week_ok = all(x["status"] in ("done", "moved") for x in out)
    return out, week_ok
