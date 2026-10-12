"""Синхронизация Garmin Connect -> SQLite. Только чтение из Garmin."""
import json, time, threading
from datetime import date, datetime, timedelta
from pathlib import Path

PAUSE = 0.5
DAILY = [("stats", "get_stats"), ("sleep", "get_sleep_data"), ("hrv", "get_hrv_data"),
         ("readiness", "get_training_readiness"), ("lifestyle", "get_lifestyle_logging_data")]
lock = threading.Lock()


def now():
    return datetime.now().isoformat(timespec="seconds")


def login(token_dir):
    from garminconnect import Garmin
    p = Path(token_dir)
    if not (p / "garmin_tokens.json").exists():
        raise FileNotFoundError(f"Нет токена Garmin: {p}/garmin_tokens.json")
    api = Garmin()
    api.login(str(p.resolve()))
    return api


def parse_sets(resp):
    out = []
    for i, s in enumerate((resp or {}).get("exerciseSets") or []):
        ex = (s.get("exercises") or [{}])
        name = ex[0].get("name") or ex[0].get("category") if ex else None
        out.append(dict(idx=i, set_type=s.get("setType"), exercise=name,
                        reps=s.get("repetitionCount"), weight_raw=s.get("weight"),
                        duration=s.get("duration"), start_time=s.get("startTime"),
                        json=json.dumps(s, ensure_ascii=False)))
    return out


def run_sync(con, api_factory, days, trigger="кнопка"):
    """Возвращает id записи sync_run. Ошибки отдельных запросов не прерывают синхронизацию."""
    if not lock.acquire(blocking=False):
        return None
    errors, n_days, n_act, n_sets = [], 0, 0, 0
    run = con.execute("INSERT INTO sync_run(started_at,trigger) VALUES(?,?)", (now(), trigger)).lastrowid
    con.commit()
    try:
        api = api_factory()
        today = date.today()
        for back in range(days):
            d = (today - timedelta(days=back)).isoformat()
            for kind, method in DAILY:
                try:
                    data = getattr(api, method)(d)
                    con.execute("INSERT OR REPLACE INTO daily_raw VALUES(?,?,?,?)",
                                (d, kind, json.dumps(data, ensure_ascii=False), now()))
                except Exception as e:  # noqa
                    errors.append(f"{d} {kind}: {e}")
                time.sleep(PAUSE)
            n_days += 1
        start = (today - timedelta(days=days - 1)).isoformat()
        acts = api.get_activities_by_date(start, today.isoformat()) or []
        for a in acts:
            aid = a["activityId"]
            tkey = (a.get("activityType") or {}).get("typeKey")
            con.execute("INSERT OR REPLACE INTO activity VALUES(?,?,?,?,?)",
                        (aid, a.get("startTimeLocal"), tkey, a.get("activityName"),
                         json.dumps(a, ensure_ascii=False)))
            n_act += 1
            if tkey == "strength_training":
                try:
                    sets = parse_sets(api.get_activity_exercise_sets(aid))
                    con.execute("DELETE FROM workout_set WHERE activity_id=?", (aid,))
                    for s in sets:
                        con.execute("INSERT INTO workout_set VALUES(?,?,?,?,?,?,?,?,?)",
                                    (aid, s["idx"], s["set_type"], s["exercise"], s["reps"],
                                     s["weight_raw"], s["duration"], s["start_time"], s["json"]))
                    n_sets += len(sets)
                except Exception as e:  # noqa
                    errors.append(f"подходы {aid}: {e}")
                time.sleep(PAUSE)
        ok = 1
    except Exception as e:  # noqa
        errors.append(str(e))
        ok = 0
    con.execute("UPDATE sync_run SET finished_at=?,ok=?,days=?,activities=?,sets=?,errors=? WHERE id=?",
                (now(), ok, n_days, n_act, n_sets, "\n".join(errors) or None, run))
    con.commit()
    lock.release()
    return run
