import sys, pathlib, tempfile, copy
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "training_center"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from app import db, program, progress, garmin_workout as gw
import test_program as tp


class Api:
    def __init__(self, fail_update=False, device=None):
        self.calls, self.last = [], None
        self.device = device
    def upload_workout(self, p):
        self.last = p; self.calls.append("upload"); return {"workoutId": 501}
    def update_workout(self, wid, p):
        self.last = p; self.calls.append(("update", wid)); return {}
    def push_workout_to_device(self, wid):
        self.calls.append(("device", wid))


def first_weight(payload, group=0):
    return payload["workoutSegments"][0]["workoutSteps"][group]["workoutSteps"][0]["weightValue"]


def setup():
    con, act = tp.mkdb()
    d, _ = program.build_draft(con)
    d["evaluate_from"] = "2026-10-12"
    d["sessions"][0]["blocks"][0]["exercises"][0]["reps"] = [6, 8]
    program.save_version(con, program.dump(d), "ручная", "т", True)
    return con, act


def test_nothing_to_push_means_no_login():
    con, act = setup()
    logins = []
    def factory():
        logins.append(1); return Api()
    row, data = program.active(con)
    assert gw.sync_all(con, data, factory) == []          # A ещё ни разу не отправляли вручную
    assert logins == []


def test_push_only_on_real_change():
    con, act = setup()
    row, data = program.active(con)
    A = data["sessions"][0]
    api = Api()
    gw.push_session(con, api, A, gw.category_map(con), "вручную")           # первая отправка вручную
    logins = []
    def factory():
        logins.append(1); return api
    assert gw.sync_all(con, data, factory) == [] and logins == []           # ничего не изменилось
    # прибавка 1: вес растёт во всех подходах сразу: 90/95/95 -> 92.5/97.5/97.5, наибольший вес вырос -> обновляем
    progress.apply_all(con)
    row, data = program.active(con)
    assert data["sessions"][0]["blocks"][0]["exercises"][0]["weights"] == [92.5, 97.5, 97.5]
    res = gw.sync_all(con, data, factory)
    assert res and res[0][0] == "A" and res[0][1] is True and logins == [1]
    assert api.calls[-1] == ("update", 501) and first_weight(api.last) == 97.5
    assert gw.sync_all(con, data, factory) == []                           # после обновления расхождения нет
    # прибавка 2 (после ещё одной выполненной A): 92.5/97.5/97.5 -> 95/100/100
    act(3, "2026-10-14", "Full Body A", [("BARBELL_BACK_SQUAT", 8, 92.5), ("BARBELL_BACK_SQUAT", 8, 97.5), ("BARBELL_BACK_SQUAT", 8, 97.5)])
    progress.apply_all(con)
    row, data = program.active(con)
    assert data["sessions"][0]["blocks"][0]["exercises"][0]["weights"] == [95, 100, 100]
    res = gw.sync_all(con, data, factory)
    assert res and res[0][0] == "A" and res[0][1] is True and logins == [1, 1]
    assert api.calls[-1] == ("update", 501) and first_weight(api.last) == 100
    assert gw.sync_all(con, data, factory) == []
    log = con.execute("SELECT * FROM push_log ORDER BY id").fetchall()
    assert [r["ok"] for r in log] == [1, 1, 1] and log[0]["why"] == "вручную"


def test_failure_is_retried_next_time():
    con, act = setup()
    row, data = program.active(con)
    api = Api()
    gw.push_session(con, api, data["sessions"][0], gw.category_map(con), "вручную")
    data["sessions"][0]["blocks"][0]["exercises"][0]["weights"] = [100, 100, 100]
    def bad():
        raise RuntimeError("Garmin недоступен")
    res = gw.sync_all(con, data, bad)
    assert res == [("A", False, "Garmin недоступен")]
    assert con.execute("SELECT ok FROM push_log ORDER BY id DESC").fetchone()["ok"] == 0
    res = gw.sync_all(con, data, lambda: api)                               # в следующий раз получилось
    assert res[0][1] is True and first_weight(api.last) == 100


def test_device_push_optional_and_safe():
    con, act = setup()
    row, data = program.active(con)
    api = Api()
    gw.push_session(con, api, data["sessions"][0], gw.category_map(con), "вручную", device=False)
    assert ("device", 501) not in api.calls
    wid, text = gw.push_session(con, api, data["sessions"][0], gw.category_map(con), "вручную", device=True)
    assert ("device", 501) in api.calls and "отправлена на часы" in text
    class NoDevice:
        def upload_workout(self, p): return {"workoutId": 9}
        def update_workout(self, w, p): return {}
    wid, text = gw.push_session(con, NoDevice(), data["sessions"][0], gw.category_map(con), "вручную", device=True)
    assert "нет метода" in text                                            # ошибка не ломает отправку
    class Strict(Api):
        def push_workout_to_device(self, wid, device_id): pass
    wid, text = gw.push_session(con, Strict(), data["sessions"][0], gw.category_map(con), "вручную", device=True)
    assert "device_id" in text
