import sys, pathlib, tempfile, json, threading, time, urllib.request, urllib.parse
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "training_center"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from app import db, program, garmin_workout as gw, web
import test_program as tp


def prog(con):
    d, err = program.build_draft(con)
    assert not err, err
    return d


def test_draft_has_rest_and_category():
    con, _ = tp.mkdb()
    d = prog(con)
    sq = d["sessions"][0]["blocks"][0]["exercises"][0]
    assert sq["category"] == "SQUAT" and sq["rest_sec"] == 135        # 130 с -> ближайшие 15 с
    assert not program.validate(d)


def test_build_structure():
    con, _ = tp.mkdb()
    d = prog(con)
    A = d["sessions"][0]
    w = gw.build_workout(A, "Full Body A · ТЦ", gw.category_map(con))
    steps = w["workoutSegments"][0]["workoutSteps"]
    # присед 90/95/95 -> две группы подряд: 1×90 и 2×95
    g1, g2 = steps[0], steps[1]
    assert (g1["numberOfIterations"], g2["numberOfIterations"]) == (1, 2)
    ex1, rest1 = g1["workoutSteps"]
    assert ex1["weightValue"] == 90 and ex1["weightUnit"]["unitKey"] == "kilogram"        # кг как есть, без ×1000
    assert g2["workoutSteps"][0]["weightValue"] == 95
    assert ex1["endConditionValue"] == 8.0 and ex1["category"] == "SQUAT" and ex1["exerciseName"] == "BARBELL_BACK_SQUAT"
    assert rest1["stepType"]["stepTypeKey"] == "rest" and rest1["endConditionValue"] == 135.0
    # подтягивания со своим весом: без веса
    pull = steps[2]["workoutSteps"][0]
    assert pull["weightValue"] is None and pull["weightUnit"] is None
    # суперсет: одна группа из 4 шагов (упр, отдых, упр, отдых), 2 круга
    ss = steps[-1]
    assert ss["numberOfIterations"] == 2 and [c["stepType"]["stepTypeKey"] for c in ss["workoutSteps"]] == ["interval", "rest", "interval", "rest"]
    # нумерация шагов сквозная, childStepId = номер группы
    orders = []
    def walk(n):
        orders.append(n["stepOrder"])
        for c in n.get("workoutSteps", []):
            walk(c)
    for s in steps:
        walk(s)
    assert orders == list(range(1, len(orders) + 1))
    assert [s["childStepId"] for s in steps] == list(range(1, len(steps) + 1))
    json.dumps(w)  # сериализуется


def test_keys_match_real_garmin_sample():
    real = pathlib.Path("/root/.claude/uploads/648f9215-7677-5252-9b12-26f1db5cc1a5/27feeee4-workout_1722494008.json")
    if not real.exists():
        return
    r = json.loads(real.read_text(encoding="utf-8"))["workoutSegments"][0]["workoutSteps"][0]
    con, _ = tp.mkdb()
    mine = gw.build_workout(prog(con)["sessions"][0], "x", gw.category_map(con))["workoutSegments"][0]["workoutSteps"][0]
    # stepId Garmin присваивает сам при создании — его у нас нет намеренно
    k = lambda d: set(d) - {"stepId"}
    assert k(mine) == k(r)
    assert k(mine["workoutSteps"][0]) == k(r["workoutSteps"][0])
    assert k(mine["workoutSteps"][1]) == k(r["workoutSteps"][1])


def test_unknown_category_error():
    con, _ = tp.mkdb()
    d = prog(con)
    d["sessions"][0]["blocks"][0]["exercises"][0].pop("category")
    d["sessions"][0]["blocks"][0]["exercises"][0]["code"] = "WEIRD_MACHINE"
    try:
        gw.build_workout(d["sessions"][0], "x", {})
        assert False
    except gw.WorkoutError as e:
        assert "WEIRD_MACHINE" in str(e)


class FakeApi:
    def __init__(self):
        self.calls = []
    def upload_workout(self, p):
        self.calls.append(("upload", p["workoutName"])); return {"workoutId": 777}
    def update_workout(self, wid, p):
        self.calls.append(("update", wid, p["workoutId"])); return {}


def test_send_create_then_update():
    con, _ = tp.mkdb()
    A = prog(con)["sessions"][0]
    api = FakeApi()
    assert gw.send(api, con, A, {}) == (777, "создана")
    assert gw.send(api, con, A, {}) == (777, "обновлена")
    assert api.calls == [("upload", "Full Body A · ТЦ"), ("update", 777, 777)]
    class BadUpdate(FakeApi):
        def update_workout(self, wid, p): raise RuntimeError("нет")
    assert gw.send(BadUpdate(), con, A, {})[1].startswith("создана заново")


def test_enrich_and_web_send():
    con, _ = tp.mkdb()
    d = prog(con)
    for x in d["sessions"][0]["blocks"][0]["exercises"]:
        x.pop("rest_sec"); x.pop("category", None)
    assert gw.enrich(con, d) >= 2
    ex = d["sessions"][0]["blocks"][0]["exercises"][0]
    assert ex["category"] == "SQUAT" and ex["rest_sec"] == 135
    program.save_version(con, program.dump(d), "ручная", "т", True)
    api = FakeApi()
    threading.Thread(target=web.serve, args=(con, {"weight_divisor": 1000.0, "api_factory": lambda: api}, lambda x: None, 18097), daemon=True).start()
    time.sleep(.5)
    post = lambda path, **kw: urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:18097/" + path, data=urllib.parse.urlencode(kw).encode())).read().decode()
    page = post("garmin_send", key="A")
    assert "создана" in page and "Приседания со штангой на спине" in page and "по 95 кг" in page
    assert api.calls[0] == ("upload", "Full Body A · ТЦ")
    assert "Не отправлено" in post("garmin_send", key="ZZ") or "не найдена" in post("garmin_send", key="ZZ")
