"""Вкладки, тема, строка последней синхронизации (v0.10.0)."""
import sys, pathlib, tempfile, threading, time, urllib.request, urllib.parse, http.client
sys.path[:0] = [str(pathlib.Path(__file__).resolve().parent.parent / "training_center")]
from datetime import datetime, timedelta
from app import db, web, timeutil, settings as stg


def fresh():
    stg.FILE = pathlib.Path(tempfile.mktemp())
    return db.connect(tempfile.mktemp())


def run(con, started, finished, ok, trigger="авто"):
    con.execute("INSERT INTO sync_run(started_at,finished_at,trigger,ok) VALUES(?,?,?,?)", (started, finished, trigger, ok))
    con.commit()


def at(now, **kw):
    return (now - timedelta(**kw)).strftime(timeutil.FMT)


def test_sync_line():
    con = fresh()
    now = timeutil.now_dt().replace(hour=12, minute=0, second=0, microsecond=0)
    assert web.sync_line(con, now) == ("grey", "Синхронизаций ещё не было")
    run(con, at(now, minutes=27), at(now, minutes=25), 1)
    cls, t = web.sync_line(con, now)
    assert cls == "green" and "сегодня 11:35" in t and "25 мин назад" in t and t.endswith("ок"), t
    run(con, at(now, hours=8), at(now, hours=8), 1)
    con.execute("DELETE FROM sync_run WHERE id=1"); con.commit()
    cls, t = web.sync_line(con, now)
    assert cls == "yellow" and "8 ч назад" in t and "устаревшими" in t, t
    run(con, at(now, minutes=10), at(now, minutes=9), 0)
    cls, t = web.sync_line(con, now)
    assert cls == "red" and "не удалась" in t and "Последняя успешная" in t, t
    con.execute("INSERT INTO sync_run(started_at,trigger) VALUES(?,?)", (at(now, minutes=1), "кнопка")); con.commit()
    assert web.sync_line(con, now)[1].startswith("идёт сейчас")


def test_page_has_tabs_and_theme():
    con = fresh()
    p = web.page(con, {"weight_divisor": 1000.0})
    for k in ("today", "week", "prog", "body", "hist"):
        assert f"id={k}>" in p and f"data-t='{k}'" in p, k
    assert "data-theme=auto" in p and "Тема: " in p and "Синхронизаций ещё не было" in p
    assert "<details class='card " in p and "Журнал синхронизаций" in p and "Отчёт для ИИ" in p
    stg.set_value("theme", "dark")
    assert "data-theme=dark" in web.page(con, {"weight_divisor": 1000.0})
    assert "body data-tab=prog" in web.page(con, {"weight_divisor": 1000.0}, notice="x")
    assert "body data-tab=hist" in web.page(con, {"weight_divisor": 1000.0}, report_text="t")


def test_post_theme_and_redirect_tab():
    con = fresh()
    threading.Thread(target=web.serve, args=(con, {"weight_divisor": 1000.0}, lambda x: None, 18111), daemon=True).start()
    time.sleep(.5)

    def post(path, hdr=None, **kw):
        c = http.client.HTTPConnection("127.0.0.1", 18111)
        c.request("POST", "/" + path, urllib.parse.urlencode(kw), {"Content-Type": "application/x-www-form-urlencoded", **(hdr or {})})
        r = c.getresponse(); r.read()
        return r.status, r.getheader("Location")
    assert post("theme", v="light") == (303, "./#today") and stg.get("theme") == "light"
    assert post("theme", {"X-Fetch": "1"}, v="dark")[0] == 204 and stg.get("theme") == "dark"
    post("theme", v="bogus"); assert stg.get("theme") == "dark"
    assert post("weight_del", id="1") == (303, "./#body")
    assert post("measure_del", day="2026-01-01") == (303, "./#body")
    assert post("settings") == (303, "./#week")
    assert post("push_toggle", on="1") == (303, "./#prog")
    assert post("report_auto", on="1") == (303, "./#hist")


def test_traffic_morning_and_now_rows():
    import json
    con = fresh()
    d = timeutil.today().isoformat()
    put = lambda kind, data: con.execute("INSERT OR REPLACE INTO daily_raw VALUES(?,?,?,?)", (d, kind, json.dumps(data), "x"))
    put("readiness", [{"inputContext": "AFTER_WAKEUP_RESET", "timestampLocal": f"{d}T07:21:00", "score": 78}])
    put("stats", {"bodyBatteryAtWakeTime": 86, "restingHeartRate": 58, "bodyBatteryMostRecentValue": 86})
    put("sleep", {"dailySleepDTO": {"sleepTimeSeconds": int(7.4 * 3600), "sleepScores": {"overall": {"value": 70}}}})
    put("hrv", {"hrvSummary": {"lastNightAvg": 41}})
    p = web.page(con, {"weight_divisor": 1000.0})
    assert "Начало дня · 07:21" in p and "rownow" not in p and "Все условия зелёного выполнены" in p
    assert "Body Battery 86" in p and "Readiness 70+" not in p
    # днём значения изменились -> появляется строка «Сейчас» только с Readiness и Body Battery
    put("readiness", [{"inputContext": "AFTER_WAKEUP_RESET", "timestampLocal": f"{d}T07:21:00", "score": 78},
                      {"inputContext": "AFTER_POST_EXERCISE_RESET", "timestampLocal": f"{d}T10:15:00", "score": 64}])
    put("stats", {"bodyBatteryAtWakeTime": 86, "restingHeartRate": 58, "bodyBatteryMostRecentValue": 52})
    p = web.page(con, {"weight_divisor": 1000.0})
    row = p[p.index("rownow"):]
    row = row[:row.index("</div>")]
    assert "Readiness 64" in row and "Body Battery 52" in row and "HRV" not in row and "Сон" not in row and "Пульс" not in row


def test_next_bump_matches_progress_rule():
    from app import progress
    assert progress.next_bump([90, 95, 95]) == 0           # меньший вес растёт первым
    assert progress.next_bump([95, 95, 95]) == 2           # поровну — последний подход
    assert progress.next_bump([60, 60, 65]) == 1
    assert progress.next_bump([0, 0]) is None and progress.next_bump([]) is None
    # правило прибавки и подсветка не расходятся
    assert progress.next_weights([90, 95, 95], 8, [(8, 90), (8, 95), (8, 95)], 5) == [95, 95, 95]
    assert progress.next_weights([95, 95, 95], 8, [(8, 95)] * 3, 5) == [95, 95, 100]


def test_run_fields_validation():
    from app import program
    d = {"evaluate_from": "2026-10-14", "progression": {"step_kg": {"barbell": 5, "dumbbell": 2.5}},
         "sessions": [{"key": "RUN", "name": "Бег", "kind": "run"}]}
    assert program.validate(d) == []
    d["sessions"][0].update(level="Лёгкий", zone=2, duration_min=40, distance_km=4.5)
    assert program.validate(d) == []
    for k, bad in (("level", "быстрый"), ("zone", 6), ("zone", 0), ("zone", True), ("duration_min", 0), ("distance_km", -1), ("distance_km", "5")):
        x = {"key": "RUN", "name": "Бег", "kind": "run", k: bad}
        errs = program.validate({**d, "sessions": [x]})
        assert len(errs) == 1 and k in errs[0], (k, bad, errs)
    assert program.run_level("легкий")[0] == "easy" and program.run_level("ИНТЕНСИВНЫЙ")[1] == "Интенсивный" and program.run_level(None) is None


def test_program_tab_tables():
    import yaml
    from app import program
    con = fresh()
    data = {"program": "T", "evaluate_from": "2026-10-14", "progression": {"step_kg": {"barbell": 5, "dumbbell": 2.5}},
            "sessions": [
                {"key": "A", "name": "Full Body A", "kind": "strength", "day": 0, "blocks": [
                    {"type": "single", "exercises": [{"code": "BARBELL_BACK_SQUAT", "reps": [6, 8], "weights": [90, 95, 95]}]},
                    {"type": "single", "exercises": [{"code": "NEUTRAL_GRIP_PULL_UP", "reps": [5, 7], "weights": [0, 0]}]},
                    {"type": "superset", "exercises": [
                        {"code": "DUMBBELL_BICEPS_CURL", "reps": [8, 10], "weights": [10, 10]},
                        {"code": "DUMBBELL_LYING_TRICEPS_EXTENSION", "reps": [10, 12], "weights": [10, 10]}]}]},
                {"key": "RUN", "name": "Бег", "kind": "run", "level": "обычный", "zone": 2, "duration_min": 40},
                {"key": "RUN2", "name": "Бег 2", "kind": "run"}]}
    program.save_version(con, program.dump(data), "ручная", "т", True)
    p = web.page(con, {"weight_divisor": 1000.0})
    # недельная программа и блок управления
    assert "Недельная программа" in p and "Управление тренировок" not in p and "Управление тренировками" in p
    assert "4 упражнения, 1 суперсет" in p and "Обычный · Зона 2 · 40 мин" in p and "без параметров" in p
    # силовая: веса отдельными метками; без записей о прибавках ничего не подсвечено
    assert ">90</span>" in p and ">свой вес</span>" in p and "6–8" in p and "wchip up" not in p
    # бег: нагрузка, шкала зоны, время, прочерки
    assert "<span class='dot norm'></span>Обычный" in p and "Зона 2" in p and "40 мин" in p
    assert p.count("<span class=dash>—</span>") >= 4
    # служебные разделы внутри «Управления»
    m = p.index("data-k='prog-manage'")
    for k in ("prog-push", "prog-yaml", "prog-versions", "prog-draft"):
        assert p.index(f"data-k='{k}'") > m, k
    assert p.index("data-k='prog-week'") < p.index("data-k='prog-s-A'") < m


def _gain_setup():
    import json
    from app import program
    con = fresh()
    data = {"program": "T", "evaluate_from": "2026-10-01", "progression": {"step_kg": {"barbell": 5, "dumbbell": 2.5}},
            "sessions": [{"key": "A", "name": "Full Body A", "kind": "strength", "day": 0, "blocks": [
                {"type": "single", "exercises": [{"code": "BARBELL_BACK_SQUAT", "reps": [6, 8], "weights": [95, 95, 95]}]},
                {"type": "single", "exercises": [{"code": "DUMBBELL_SEATED_PRESS", "reps": [10, 12], "weights": [15, 15, 17.5]}]},
                {"type": "single", "exercises": [{"code": "BARBELL_BENCH_PRESS", "reps": [6, 8], "weights": [90, 95]}]},
                {"type": "single", "exercises": [{"code": "BARBELL_ROW", "reps": [8, 10], "weights": [70, 70]}]}]}]}
    program.save_version(con, program.dump(data), "авто", "т", True)
    con.execute("INSERT INTO activity VALUES(7,'2026-10-12 10:00:00','strength_training','Full Body A','{}')")
    for code, txt in (("BARBELL_BACK_SQUAT", "Full Body A: BARBELL_BACK_SQUAT 90/95/95 → 95/95/95"),
                      ("DUMBBELL_SEATED_PRESS", "Full Body A: DUMBBELL_SEATED_PRESS 15/15/15 → 15/15/17.5"),
                      ("BARBELL_BENCH_PRESS", "Full Body A: BARBELL_BENCH_PRESS 90/90 → 90/95")):
        con.execute("INSERT INTO progress_log VALUES(7,?,?,?)", (code, "2026-10-12 12:00:00", txt))
    con.commit()
    return con


def _up_chips(p):
    """Подписи подсвеченных меток по порядку на странице."""
    import re
    return re.findall(r"<span class='wchip up' title='[^']*'>([^<]*)</span>", p)


def test_highlight_follows_suggested_weight_changes():
    from app import progress
    assert progress.parse_change("Full Body A: X 15/15/15 → 15/15/17.5") == ([15, 15, 15], [15, 15, 17.5])
    assert progress.parse_change("нет изменений") is None
    con = _gain_setup()
    p = web.page(con, {"weight_divisor": 1000.0})
    # подсвечены ровно подходы с повышенным весом: присед — первый (95), жим гантелей — последний (17,5), жим штанги — второй (95)
    assert _up_chips(p) == ["95", "17,5", "95"], _up_chips(p)
    assert "Прибавка: 90 → 95 кг (по итогам тренировки 12.10)" in p and "Прибавка: 15 → 17,5 кг" in p
    assert "его предстоит поднять" in p
    # упражнение без записи (тяга) не подсвечивается, у приседа подсвечен один подход из трёх
    i = p.index("Приседания"); row = p[i:p.index("</tr>", i)]
    assert row.count("wchip up") == 1 and row.index("wchip up") < row.index(">95</span><span class=wchip>95")
    # после следующей тренировки этой сессии прибавка считается поднятой — подсветка гаснет
    con.execute("INSERT INTO activity VALUES(8,'2026-10-14 10:00:00','strength_training','Full Body A','{}')"); con.commit()
    assert "wchip up" not in web.page(con, {"weight_divisor": 1000.0})
    # вес в программе поменяли руками (не равен записанному) — подсветки нет
    con2 = _gain_setup()
    con2.execute("UPDATE progress_log SET text='Full Body A: BARBELL_BACK_SQUAT 90/95/95 → 100/95/95' WHERE code='BARBELL_BACK_SQUAT'"); con2.commit()
    p2 = web.page(con2, {"weight_divisor": 1000.0})
    i = p2.index("Приседания"); assert "wchip up" not in p2[i:p2.index("</tr>", i)]
    assert _up_chips(p2) == ["17,5", "95"]


def test_week_table_status_pills():
    from datetime import timedelta
    con = fresh()
    mon = timeutil.today() - timedelta(days=timeutil.today().weekday())
    con.execute("INSERT INTO activity VALUES(1,?,?,?,?)", (f"{mon.isoformat()} 10:00:00", "strength_training", "Full Body A", "{}"))
    con.commit()
    p = web.page(con, {"weight_divisor": 1000.0})
    assert "<table class=w1>" in p and "tr class=cur" in p and "res go" in p and "st done" in p
    assert "идёт · 1/3" in p


if __name__ == "__main__":
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v()
    print("OK")
