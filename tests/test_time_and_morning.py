import sys, pathlib, tempfile, json, sqlite3
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "training_center"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from app import db, timeutil, traffic, web, sync
import test_traffic_plan as tp


def test_utc_to_local():
    timeutil.setup()
    assert timeutil.utc_iso_to_local("2026-10-13T02:16:48") == "2026-10-13 07:16:48"   # UTC+5
    assert timeutil.utc_iso_to_local("2026-10-12T20:30:00") == "2026-10-13 01:30:00"   # переход через полночь
    assert len(timeutil.now()) == 19 and "T" not in timeutil.now()


def test_old_timestamps_migrated():
    path = tempfile.mktemp()
    con = sqlite3.connect(path)
    # база версии 3 со старыми метками UTC
    for m in db.MIGRATIONS[:3]:
        con.executescript(m)
    con.execute("PRAGMA user_version=3")
    con.execute("INSERT INTO sync_run(started_at,finished_at,trigger,ok) VALUES('2026-10-13T02:16:48','2026-10-13T02:20:00','кнопка',1)")
    con.execute("INSERT INTO program_version(created_at,kind,note,yaml,active) VALUES('2026-10-12T17:00:00','ручная','','',1)")
    con.commit(); con.close()
    c = db.connect(path)
    r = c.execute("SELECT * FROM sync_run").fetchone()
    assert (r["started_at"], r["finished_at"]) == ("2026-10-13 07:16:48", "2026-10-13 07:20:00")
    assert c.execute("SELECT created_at FROM program_version").fetchone()[0] == "2026-10-12 22:00:00"
    c.close()
    c = db.connect(path)    # повторный запуск ничего не сдвигает
    assert c.execute("SELECT started_at FROM sync_run").fetchone()[0] == "2026-10-13 07:16:48"


def test_traffic_grey_until_morning_data():
    con = db.connect(tempfile.mktemp())
    d = timeutil.today().isoformat()
    # 06:50: статистика дня уже есть, сна и утреннего Readiness ещё нет
    tp.put(con, d, "stats", {"bodyBatteryHighestValue": 35, "restingHeartRate": 64})
    m = traffic.metrics(con, d)
    assert traffic.missing_morning(m) == ["сон", "утренний Training Readiness"]
    p = web.page(con, {"weight_divisor": 1000.0})
    assert "Утренних данных ещё нет" in p and "Красный" not in p
    # появились сон и readiness -> светофор показывает цвет
    tp.put(con, d, "readiness", [{"inputContext": "AFTER_WAKEUP_RESET", "timestampLocal": f"{d}T07:21:00", "score": 64}])
    tp.put(con, d, "sleep", {"dailySleepDTO": {"sleepTimeSeconds": 7 * 3600, "sleepScores": {"overall": {"value": 80}}}})
    p = web.page(con, {"weight_divisor": 1000.0})
    assert "Утренних данных ещё нет" not in p and "Светофор на" in p


def test_sync_times_have_no_T():
    class F:
        def __getattr__(self, n):
            if n == "get_activities_by_date":
                return lambda a, b: []
            return lambda d: {}
    sync.PAUSE = 0
    con = db.connect(tempfile.mktemp())
    rid = sync.run_sync(con, F, 1, "тест")
    r = con.execute("SELECT * FROM sync_run WHERE id=?", (rid,)).fetchone()
    assert "T" not in r["started_at"] and "T" not in r["finished_at"] and len(r["started_at"]) == 19
