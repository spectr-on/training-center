import sys, pathlib, tempfile, json
from datetime import date, timedelta
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "training_center"))
from app import db, traffic, plan, settings, web


def put(con, d, kind, data):
    con.execute("INSERT OR REPLACE INTO daily_raw VALUES(?,?,?,?)", (d, kind, json.dumps(data), "x"))


def mk(con, d, rd, bb, sleep_h, hrv, rhr):
    put(con, d, "readiness", [{"inputContext": "AFTER_WAKEUP_RESET", "score": rd}])
    put(con, d, "stats", {"bodyBatteryAtWakeTime": bb, "restingHeartRate": rhr})
    put(con, d, "sleep", {"dailySleepDTO": {"sleepTimeSeconds": int(sleep_h * 3600), "sleepScores": {"overall": {"value": 70}}}})
    put(con, d, "hrv", {"hrvSummary": {"lastNightAvg": hrv}})


def color(con, d):
    return traffic.decide(traffic.metrics(con, d))[0]


def test_traffic_rules():
    con = db.connect(tempfile.mktemp())
    mk(con, "green", 75, 85, 7.5, 50, 55); assert color(con, "green") == "green"
    mk(con, "oct7", 68, 47, 7.2, 33, 62); assert color(con, "oct7") == "yellow"      # пример из разбора
    mk(con, "oct2", 15, 40, 5.4, 45, 58); assert color(con, "oct2") == "red"         # Readiness < 40
    mk(con, "two", 60, 35, 5.5, 50, 55); assert color(con, "two") == "red"           # 2 флага: BB<40 и сон<6
    mk(con, "one", 60, 35, 7.5, 50, 55); assert color(con, "one") == "yellow"        # 1 флаг
    mk(con, "nogreen", 72, 85, 6.9, 50, 55); assert color(con, "nogreen") == "yellow"  # сон < 7
    assert color(con, "empty") == "yellow"                                            # нет данных -> не зелёный


def act(con, i, day, tkey, name):
    con.execute("INSERT INTO activity VALUES(?,?,?,?,?)", (i, f"{day} 10:00:00", tkey, name, "{}"))


def test_week_plan():
    con = db.connect(tempfile.mktemp())
    cfg = json.loads(json.dumps(settings.DEFAULT))
    mon = date(2026, 10, 5)  # понедельник
    act(con, 1, "2026-10-05", "strength_training", "Full Body A")     # A в срок
    act(con, 2, "2026-10-08", "strength_training", "Full Body B")     # B перенесён с пятницы на четверг
    act(con, 3, "2026-10-07", "running", "Бег")                       # бег в срок
    cells, ok = plan.week_status(con, cfg, mon, date(2026, 10, 12))
    assert [c["status"] for c in cells] == ["done", "moved", "done"] and ok
    # текущая неделя: среда прошла, бега нет -> просрочено; пятница впереди
    mon2 = date(2026, 10, 12)
    act(con, 4, "2026-10-12", "strength_training", "Full Body A")
    cells, ok = plan.week_status(con, cfg, mon2, date(2026, 10, 14))
    assert [c["status"] for c in cells] == ["done", "planned", "today"] and not ok
    cells, ok = plan.week_status(con, cfg, mon2, date(2026, 10, 15))
    assert cells[2]["status"] == "late"
    cells, ok = plan.week_status(con, cfg, mon2, date(2026, 10, 20))   # неделя закончилась
    assert [c["status"] for c in cells] == ["done", "missed", "missed"]


def test_page_renders():
    con = db.connect(tempfile.mktemp())
    mk(con, date.today().isoformat(), 75, 85, 7.5, 50, 55)
    p = web.page(con, {"weight_divisor": 1000.0})
    assert "Светофор" in p and "Зелёный" in p and "Недельный план" in p
