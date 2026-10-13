import sys, pathlib, tempfile, json, datetime
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "training_center"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from app import db, body, web, settings as stg

stg.FILE = pathlib.Path(tempfile.mkdtemp()) / "settings.json"
TODAY = datetime.date(2026, 10, 13)


def fresh():
    con = db.connect(tempfile.mktemp())
    con.execute("INSERT INTO activity VALUES(9,'2026-10-11 08:00:00','running','Бег','{}')")
    con.commit()
    return con


def test_weight_validation_and_derive():
    con = fresh()
    ok, m = body.add_weight(con, "2026-10-13T07:30", {"weight_kg": "86,85", "fat_pct": "21,2", "water_pct": "57.7"}, TODAY)
    assert ok, m
    r = body.weights_between(con, TODAY, TODAY)[0]
    assert r["weight_kg"] == 86.85 and r["source"] == "manual" and r["at"] == "2026-10-13 07:30:00"
    assert r["fat_kg"] == 18.4 and r["lean_kg"] == 68.4          # 86,85 × 21,2% ≈ 18,4
    for form, part in (({}, "Вес"), ({"weight_kg": "abc"}, "число"), ({"weight_kg": "500"}, "от 30"),
                       ({"weight_kg": "80", "fat_pct": "99"}, "Жир"), ({"weight_kg": "80", "water_pct": "x"}, "Вода")):
        ok, m = body.add_weight(con, "", form, TODAY)
        assert not ok and part in m, (form, m)
    assert not body.add_weight(con, "2026-10-20 07:00", {"weight_kg": "80"}, TODAY)[0]    # будущее
    assert not body.add_weight(con, "вчера", {"weight_kg": "80"}, TODAY)[0]
    ok, _ = body.add_weight(con, "2026-10-13T08:00", {"weight_kg": "80"}, TODAY)
    assert ok and body.weights_between(con, TODAY, TODAY)[1]["fat_kg"] is None           # без % жира масса не считается
    assert con.execute("SELECT COUNT(*) FROM body_weight").fetchone()[0] == 2            # неудачные не записались
    body.delete_weight(con, 1)
    assert con.execute("SELECT COUNT(*) FROM body_weight").fetchone()[0] == 1


def test_window_avg_is_per_day():
    con = fresh()
    for at, w in (("2026-10-13 07:00", "86"), ("2026-10-13 21:00", "88"), ("2026-10-10 07:00", "85"), ("2026-09-20 07:00", "83")):
        assert body.add_weight(con, at, {"weight_kg": w}, TODAY)[0]
    a7, a28 = body.window_avg(con, TODAY, 7), body.window_avg(con, TODAY, 28)
    assert a7["days"] == 2 and abs(a7["weight"] - 86.0) < 1e-9        # (87 + 85) / 2: день считается один раз
    assert a28["days"] == 3 and abs(a28["weight"] - (87 + 85 + 83) / 3) < 1e-9
    assert body.window_avg(con, datetime.date(2026, 1, 1), 7) is None


def test_measures_merge_and_due():
    con = fresh()
    assert body.measure_status(con, TODAY) == (True, None, None, 28)
    assert not body.add_measures(con, "2026-10-13", {}, TODAY)[0]
    assert not body.add_measures(con, "2026-10-13", {"waist": "1"}, TODAY)[0]
    assert body.add_measures(con, "2026-09-15", {"waist": "91,5", "chest": "104"}, TODAY)[0]
    assert body.measure_status(con, TODAY) == (True, "2026-09-15", 28, 28)       # ровно интервал — пора
    assert body.add_measures(con, "2026-10-01", {"waist": "90"}, TODAY)[0]
    assert body.measure_status(con, TODAY)[0] is False
    assert body.add_measures(con, "2026-10-01", {"hips": "101"}, TODAY)[0]       # дополняет, не затирает
    last = body.last_measures(con, 3)
    assert [m["date"] for m in last] == ["2026-09-15", "2026-10-01"]
    assert last[1]["waist"] == 90 and last[1]["hips"] == 101
    stg.set_value("measure_interval_days", 10)
    assert body.measure_status(con, TODAY)[0] is True
    stg.set_value("measure_interval_days", "мусор")
    assert body.interval_days() == 28
    body.delete_measures(con, "2026-10-01")
    assert len(body.last_measures(con, 5)) == 1


def test_weigh_due_after_3_days():
    con = fresh()
    assert body.weight_status(con, TODAY) == (True, None, None, 3)
    body.add_weight(con, "2026-10-10 07:00", {"weight_kg": "86"}, TODAY)
    assert body.weight_status(con, datetime.date(2026, 10, 12))[:3] == (False, "2026-10-10", 2)
    assert body.weight_status(con, TODAY)[:3] == (True, "2026-10-10", 3)           # три дня без записи — пора
    body.add_weight(con, "2026-10-13 21:00", {"weight_kg": "86"}, TODAY)           # время суток не важно, считаем по дате
    assert body.weight_status(con, TODAY)[0] is False
    stg.set_value("weigh_interval_days", 1)
    assert body.weight_status(con, datetime.date(2026, 10, 14))[0] is True
    stg.set_value("weigh_interval_days", 0)
    assert body.weigh_interval_days() == 3
    stg.set_value("weigh_interval_days", 3)


def test_run_feel():
    con = fresh()
    assert [r["id"] for r in body.pending_feel(con, TODAY)] == [9]
    assert not body.set_feel(con, 9, 7)[0] and not body.set_feel(con, 9, "x")[0] and not body.set_feel(con, 404, 3)[0]
    assert body.set_feel(con, 9, "4", "ноги лёгкие")[0]
    assert body.pending_feel(con, TODAY) == [] and body.feel_of(con, 9) == (4, "ноги лёгкие")
    assert body.set_feel(con, 9, 2)[0] and body.feel_of(con, 9) == (2, None)      # ответ можно исправить
    con.execute("INSERT INTO activity VALUES(5,'2026-09-01 08:00:00','running','Старый','{}')")
    assert body.pending_feel(con, TODAY) == []                                     # давние пробежки не спрашиваем


def test_page_renders_and_sections():
    con = fresh()
    cfg = {"weight_divisor": 1000.0}
    t = web.page(con, cfg)
    for s in ("Вес и замеры", "Пора взвеситься", "Пора делать замеры лентой", "Как ощущался бег?", "action=weight", "action=measure", "action=run_feel"):
        assert s in t, s
    body.add_weight(con, "", {"weight_kg": "86.5"})
    body.add_measures(con, "", {"waist": "90"})
    body.set_feel(con, 9, 3)
    t = web.page(con, cfg, body_msg=(False, "Не сохранено: <тест>"))
    assert "Как ощущался бег?" not in t and "Пора делать замеры лентой</b>" not in t and "Пора взвеситься" not in t and "&lt;тест&gt;" in t
    assert "86.5" in t or "86,5" in t


if __name__ == "__main__":
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v(); print("ok", k)
