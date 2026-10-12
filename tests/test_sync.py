import sys, pathlib, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "training_center"))
from app import db, sync, web


class Fake:
    def __getattr__(self, n):
        if n.startswith("get_activities_by_date"):
            return lambda a, b: [{"activityId": 1, "startTimeLocal": "2026-10-08 18:00:00",
                                  "activityName": "Силовая", "activityType": {"typeKey": "strength_training"}}]
        if n == "get_activity_exercise_sets":
            return lambda i: {"exerciseSets": [
                {"setType": "ACTIVE", "repetitionCount": 5, "weight": 60000.0, "exercises": [{"name": "BARBELL_BENCH_PRESS"}]},
                {"setType": "REST", "duration": 90}]}
        if n == "get_hrv_data":
            raise RuntimeError("нет HRV")
        return lambda d: {"x": d}


def test_sync_and_page():
    sync.PAUSE = 0
    con = db.connect(tempfile.mktemp())
    rid = sync.run_sync(con, Fake, 2, "тест")
    r = con.execute("SELECT * FROM sync_run WHERE id=?", (rid,)).fetchone()
    assert r["ok"] == 1 and r["days"] == 2 and r["activities"] == 1 and r["sets"] == 2
    assert "hrv" in r["errors"]
    p = web.page(con, {"weight_divisor": 1000.0})
    assert "60 кг × 5" in p and "Жим штанги лёжа" in p and "Силовая" in p
    # повторный запуск не дублирует подходы
    sync.run_sync(con, Fake, 2, "тест")
    assert con.execute("SELECT COUNT(*) FROM workout_set").fetchone()[0] == 2


def test_blocks_and_names():
    from app.blocks import group_blocks
    from app.exercise_names import ru
    seq = list("SSS") + list("CTCT") + list("LLL")
    b = group_blocks([{"exercise": x} for x in seq])
    assert [x["kind"] for x in b] == ["single", "superset", "single"]
    assert len(b[1]["items"]) == 4
    assert [x["kind"] for x in group_blocks([{"exercise": x} for x in "AAABBB"])] == ["single", "single"]
    assert ru("BARBELL_BENCH_PRESS") == "Жим штанги лёжа" and ru("WEIRD_THING").endswith("⁇")


def test_new_names_and_overrides(tmp_path=None):
    import tempfile, json, pathlib
    from app import exercise_names as en
    assert en.ru("WIDE_GRIP_PULL_UP") == "Подтягивания широким хватом"
    assert en.ru("Lying dumbbell pullover to extension") == "Французский жим гантелей (лёжа)"
    f = pathlib.Path(tempfile.mktemp()); f.write_text(json.dumps({"WEIRD_THING": "Моё упражнение"}), encoding="utf-8")
    old = en.OVERRIDES_FILE; en.OVERRIDES_FILE = f
    try:
        assert en.ru("WEIRD_THING") == "Моё упражнение"
    finally:
        en.OVERRIDES_FILE = old
