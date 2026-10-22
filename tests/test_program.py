import sys, pathlib, tempfile, json
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "training_center"))
from app import db, program, progress


def test_all_sets_step():
    nw = progress.next_weights
    full = [(8, 90), (8, 95), (8, 95)]
    assert nw([90, 95, 95], 8, full, 5) == [95, 100, 100]        # вес растёт во всех подходах сразу
    assert nw([95, 95, 95], 8, [(8, 95)] * 3, 2.5) == [97.5, 97.5, 97.5]
    assert nw([10, 10], 12, [(12, 10)] * 2, 1.25) == [11.25, 11.25]
    assert nw([90, 95, 95], 8, [(8, 90), (7, 95), (8, 95)], 5) is None  # один подход не дотянул
    assert nw([90, 95, 95], 8, [(8, 90), (8, 95)], 5) is None           # не все подходы
    assert nw([90, 95, 95], 8, [(8, 85), (8, 95), (8, 95)], 5) is None  # вес ниже планового
    assert nw([5, 5, 5, 5], 20, [(20, 5)] * 4, 2.5) == [7.5] * 4
    assert nw([0, 0, 0], 7, [(7, 0)] * 3, 5) is None                    # свой вес
    assert nw([90, 95, 95], 8, [(8, 90), (8, 95), (8, 95)], None) is None  # шага нет
    assert nw([90, 95, 95], 8, [(5, 40), (8, 90), (8, 95), (8, 95)], 5) == [95, 100, 100]  # разминка в начале не мешает


def test_step_of():
    so = program.step_of
    assert so({"code": "BARBELL_BACK_SQUAT", "weights": [90]}) == 2.5          # штанга по умолчанию
    assert so({"code": "DUMBBELL_CURL", "weights": [10]}) == 1.25               # гантели по умолчанию (на гантель)
    assert so({"code": "BARBELL_BACK_SQUAT", "weights": [90], "step_kg": 5}) == 5
    assert so({"code": "DUMBBELL_CURL", "weights": [10], "step_kg": 2.5}) == 2.5
    assert so({"code": "CABLE_ROW", "weights": [40]}) is None                   # другой снаряд: шага по умолчанию нет
    assert so({"code": "CABLE_ROW", "weights": [40], "step_kg": 2.5}) == 2.5
    assert so({"code": "NEUTRAL_GRIP_PULL_UP", "weights": [0, 0]}) is None      # свой вес
    assert so({"code": "BARBELL_BACK_SQUAT", "weights": [90], "step_kg": 0}) == 2.5  # некорректное значение -> по умолчанию


def mkdb():
    con = db.connect(tempfile.mktemp())
    def act(i, day, name, sets):
        con.execute("INSERT INTO activity VALUES(?,?,?,?,?)", (i, f"{day} 10:00:00", "strength_training", name, "{}"))
        n = 0
        cat = {"BARBELL_BACK_SQUAT": "SQUAT", "NEUTRAL_GRIP_PULL_UP": "PULL_UP", "DUMBBELL_BICEPS_CURL": "CURL",
               "DUMBBELL_LYING_TRICEPS_EXTENSION": "TRICEPS_EXTENSION", "BARBELL_FRONT_SQUAT": "SQUAT"}
        for ex, reps, kg in sets:
            con.execute("INSERT INTO workout_set VALUES(?,?,?,?,?,?,?,?,?)", (i, n, "ACTIVE", ex, reps, kg * 1000.0, None, None,
                        json.dumps({"exercises": [{"category": cat.get(ex), "name": ex}]})))
            con.execute("INSERT INTO workout_set VALUES(?,?,?,?,?,?,?,?,?)", (i, n + 1, "REST", None, None, None, 130.0, None, "{}"))
            n += 2
    A = [("BARBELL_BACK_SQUAT", 8, 90), ("BARBELL_BACK_SQUAT", 8, 95), ("BARBELL_BACK_SQUAT", 8, 95),
         ("NEUTRAL_GRIP_PULL_UP", 7, 0), ("NEUTRAL_GRIP_PULL_UP", 7, 0),
         ("DUMBBELL_BICEPS_CURL", 10, 10), ("DUMBBELL_LYING_TRICEPS_EXTENSION", 10, 10),
         ("DUMBBELL_BICEPS_CURL", 9, 10), ("DUMBBELL_LYING_TRICEPS_EXTENSION", 12, 10)]
    act(1, "2026-10-12", "Full Body A", A)
    act(2, "2026-10-08", "Full Body B", [("BARBELL_FRONT_SQUAT", 8, 50), ("BARBELL_FRONT_SQUAT", 10, 60)])
    return con, act


def test_draft_validate_progress():
    con, act = mkdb()
    data, err = program.build_draft(con)
    assert not err
    a = data["sessions"][0]
    assert a["blocks"][0]["exercises"][0]["weights"] == [90, 95, 95]
    assert a["blocks"][1]["exercises"][0]["equipment"] == "bodyweight"
    assert a["blocks"][2]["type"] == "superset" and len(a["blocks"][2]["exercises"]) == 2
    assert data["sessions"][-1] == {"key": "RUN", "name": "Бег", "kind": "run"}
    text = program.dump(data)
    d2, errs = program.parse(text)
    assert not errs, errs
    program.save_version(con, text, "ручная", "старт", True)
    # прогрессия: сегодняшняя A считается, т.к. evaluate_from = сегодня; потеряем её, поставив 2026-10-12
    d2["evaluate_from"] = "2026-10-12"
    d2["sessions"][0]["blocks"][0]["exercises"][0]["reps"] = [6, 8]
    program.save_version(con, program.dump(d2), "ручная", "правка", True)
    ev = progress.apply_all(con)
    assert any("BARBELL_BACK_SQUAT 90/95/95 → 92.5/97.5/97.5" in x for x in ev), ev
    assert not progress.apply_all(con)                       # повторно ничего не делает
    row, cur = program.active(con)
    assert cur["sessions"][0]["blocks"][0]["exercises"][0]["weights"] == [92.5, 97.5, 97.5] and row["kind"] == "авто"
    # следующая A с новыми весами
    act(3, "2026-10-14", "Full Body A", [("BARBELL_BACK_SQUAT", 8, 92.5), ("BARBELL_BACK_SQUAT", 8, 97.5), ("BARBELL_BACK_SQUAT", 8, 97.5)])
    ev = progress.apply_all(con)
    assert any("92.5/97.5/97.5 → 95/100/100" in x for x in ev), ev


def test_validation_errors():
    bad = {"sessions": [{"key": "A", "kind": "x", "name": "A"}]}
    errs = program.validate(bad)
    assert any("evaluate_from" in x for x in errs) and any("kind" in x for x in errs)
    assert not any("step_kg" in x for x in errs)                   # общий progression.step_kg больше не нужен


def test_step_kg_field():
    def mk(step):
        ex = {"code": "BARBELL_BACK_SQUAT", "reps": [6, 8], "weights": [90, 90]}
        if step is not None:
            ex["step_kg"] = step
        return {"evaluate_from": "2026-10-14", "sessions": [{"key": "A", "name": "A", "kind": "strength",
                "blocks": [{"type": "single", "exercises": [ex]}]}]}
    assert program.validate(mk(None)) == [] and program.validate(mk(5)) == [] and program.validate(mk(1.25)) == []
    for bad in (0, -2.5, "5", True, [5]):
        errs = program.validate(mk(bad))
        assert len(errs) == 1 and "step_kg" in errs[0], (bad, errs)
    # старые программы с progression.step_kg остаются допустимыми (поле игнорируется)
    old = mk(None); old["progression"] = {"rule": "волна с последнего подхода", "step_kg": {"barbell": 5, "dumbbell": 2.5}}
    assert program.validate(old) == []


def test_rollback():
    con, _ = mkdb()
    d, _ = program.build_draft(con)
    v1 = program.save_version(con, program.dump(d), "ручная", "1", True)
    program.save_version(con, program.dump(d) + "\n# x", "ручная", "2", True)
    program.rollback(con, v1)
    row, _ = program.active(con)
    assert row["kind"] == "откат" and "# x" not in row["yaml"]


def test_plan_with_program_and_web():
    import threading, time, urllib.request, urllib.parse
    from datetime import date
    from app import web, settings, plan
    settings.FILE = pathlib.Path(tempfile.mktemp())
    con, act = mkdb()
    d, _ = program.build_draft(con)
    d["sessions"].append({"key": "RUN2", "name": "Бег 2", "kind": "run"})
    program.save_version(con, program.dump(d), "ручная", "т", True)
    row, data = program.active(con)
    slots = program.slots_of(data)
    assert [s["key"] for s in slots] == ["A", "B", "RUN", "RUN2"] and slots[2]["day"] is None
    con.execute("INSERT INTO activity VALUES(?,?,?,?,?)", (50, "2026-10-13 07:00:00", "running", "Бег", "{}"))
    cells, ok = plan.week_status(con, {"slots": slots}, date(2026, 10, 12), date(2026, 10, 14))
    assert [c["status"] for c in cells] == ["done", "planned", "done", "planned"]
    # веб: страница, черновик, сохранение с ошибкой, откат
    threading.Thread(target=web.serve, args=(con, {"weight_divisor": 1000.0}, lambda x: None, 18098), daemon=True).start(); time.sleep(.5)
    base = "http://127.0.0.1:18098/"
    post = lambda path, **kw: urllib.request.urlopen(urllib.request.Request(base + path, data=urllib.parse.urlencode(kw).encode())).read().decode()
    pg = urllib.request.urlopen(base).read().decode()
    assert "Программа" in pg
    assert "Приседания со штангой на спине" in pg
    bad = post("program", yaml="sessions: []")
    assert "Не сохранено" in bad and "evaluate_from" in bad
    post("settings", day_A="2", day_RUN="any")
    assert settings.day_overrides()["A"] == 2 and settings.day_overrides()["RUN"] is None
    v = con.execute("SELECT MIN(id) FROM program_version").fetchone()[0]
    post("rollback", id=str(v))
    assert program.active(con)[0]["kind"] == "откат"
