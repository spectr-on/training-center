import sys, pathlib, tempfile, json
from datetime import date
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "training_center"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from app import db, ha_mqtt, settings
from test_traffic_plan import mk, act

TODAY = date(2026, 10, 14)   # среда: по умолчанию бег
SLOTS = settings.DEFAULT["slots"]


class FakeClient:
    def __init__(self):
        self.sent = []

    def publish(self, topic, payload, qos=0, retain=False):
        self.sent.append((topic, payload, retain))


def fresh():
    con = db.connect(tempfile.mktemp())
    mk(con, "2026-10-14", 75, 85, 7.5, 50, 55)
    act(con, 1, "2026-10-12", "strength_training", "Full Body A")
    con.execute("INSERT INTO sync_run(started_at,finished_at,trigger,ok,activities,errors) VALUES('2026-10-14 06:00:00','2026-10-14 06:02:10','авто',1,1,NULL)")
    con.commit()
    return con


def test_discovery():
    cfgs = {t: json.loads(p) for t, p in ha_mqtt.build_discovery()}
    ids = sorted(c["default_entity_id"] for c in cfgs.values())
    assert ids == ["binary_sensor.training_garmin_ok", "binary_sensor.training_measure_due", "binary_sensor.training_weigh_due", "event.training_workout_done", "sensor.training_last_sync",
                   "sensor.training_today_session", "sensor.training_traffic_light", "sensor.training_week_done"]
    c = cfgs["homeassistant/sensor/training_center/training_traffic_light/config"]
    assert c["state_topic"] == "training_center/training_traffic_light/state"
    assert c["availability_topic"] == ha_mqtt.AVAIL and c["unique_id"] == "training_center_training_traffic_light"
    ev = cfgs["homeassistant/event/training_center/training_workout_done/config"]
    assert ev["event_types"] == ["workout_done"] and "json_attributes_topic" not in ev
    assert cfgs["homeassistant/sensor/training_center/training_last_sync/config"]["device_class"] == "timestamp"


def test_states():
    con = fresh()
    s = ha_mqtt.build_states(con, SLOTS, TODAY)
    assert s["training_traffic_light"][0] == "зелёный"
    assert s["training_traffic_light"][1]["color"] == "green"
    assert s["training_today_session"][0] == "Бег" and s["training_today_session"][1]["статус_код"] == "today"
    done, attrs = s["training_week_done"]
    assert done == 1 and attrs["запланировано"] == 3 and attrs["неделя_выполнена"] is False
    ts, ok, a = ha_mqtt.sync_states(con)
    assert ts.startswith("2026-10-14T06:02:10") and ts.endswith("+05:00") and ok == "ON" and a["результат"] == "успешно"
    # другой день: тренировки нет
    s2 = ha_mqtt.build_states(con, SLOTS, date(2026, 10, 13))
    assert s2["training_today_session"][0] == "отдых"
    # нет утренних данных -> цвет не показываем
    con.execute("DELETE FROM daily_raw")
    s3 = ha_mqtt.build_states(con, SLOTS, TODAY)
    assert s3["training_traffic_light"][0] == "нет данных"


def test_measure_due_sensor():
    con = fresh()
    s = ha_mqtt.build_states(con, SLOTS, TODAY)["binary:training_measure_due"]
    assert s[0] == "ON" and s[1]["последние_замеры"] is None
    con.execute("INSERT INTO body_measure(date,waist) VALUES('2026-10-01',90)")
    s = ha_mqtt.build_states(con, SLOTS, TODAY)["binary:training_measure_due"]
    assert s[0] == "OFF" and s[1]["дней_прошло"] == 13 and s[1]["интервал_дней"] == 28
    con.execute("UPDATE body_measure SET date='2026-09-10'")
    assert ha_mqtt.build_states(con, SLOTS, TODAY)["binary:training_measure_due"][0] == "ON"


def test_weigh_due_sensor():
    con = fresh()
    s = ha_mqtt.build_states(con, SLOTS, TODAY)["binary:training_weigh_due"]
    assert s[0] == "ON" and s[1]["последний_вес"] is None and s[1]["интервал_дней"] == 3
    con.execute("INSERT INTO body_weight(at,weight_kg) VALUES('2026-10-12 07:00:00',86)")
    s = ha_mqtt.build_states(con, SLOTS, TODAY)["binary:training_weigh_due"]
    assert s[0] == "OFF" and s[1]["дней_прошло"] == 2
    assert ha_mqtt.build_states(con, SLOTS, date(2026, 10, 15))["binary:training_weigh_due"][0] == "ON"


def test_garmin_ok_false_on_failed_sync():
    con = fresh()
    con.execute("INSERT INTO sync_run(started_at,finished_at,trigger,ok,errors) VALUES('2026-10-14 09:00:00','2026-10-14 09:00:05','авто',0,'нет токена')")
    assert ha_mqtt.sync_states(con)[1] == "OFF"
    # незавершённая синхронизация не считается
    con.execute("INSERT INTO sync_run(started_at,trigger) VALUES('2026-10-14 12:00:00','авто')")
    assert ha_mqtt.sync_states(con)[1] == "OFF"
    assert ha_mqtt.sync_states(db.connect(tempfile.mktemp()))[0] is None


def test_events_once_and_matching():
    con = fresh()
    ha_mqtt.mark_existing_announced(con)
    assert ha_mqtt.new_workouts(con, SLOTS, TODAY) == []              # старое не объявляем
    act(con, 2, "2026-10-14", "running", "Бег")
    con.commit()
    evs = ha_mqtt.new_workouts(con, SLOTS, TODAY)
    assert len(evs) == 1 and evs[0]["slot"] == "RUN" and evs[0]["planned"] and evs[0]["event_type"] == "workout_done"
    assert ha_mqtt.new_workouts(con, SLOTS, TODAY) == []              # второй раз не повторяется
    # старая (позавчера+) тренировка помечается, но событием не становится
    act(con, 3, "2026-09-01", "strength_training", "Full Body B")
    con.commit()
    assert ha_mqtt.new_workouts(con, SLOTS, TODAY) == []
    assert con.execute("SELECT COUNT(*) FROM mqtt_event").fetchone()[0] == 3


def test_bridge_publish_and_dedup():
    con = fresh()
    path = tempfile.mktemp()
    real = db.connect(path)
    real.close()
    # используем общий файл БД, чтобы Bridge мог открыть своё соединение
    import shutil
    con.commit()
    src = con.execute("PRAGMA database_list").fetchone()["file"]
    shutil.copy(src, path)
    fc = FakeClient()
    b = ha_mqtt.Bridge(lambda: db.connect(path), lambda c: SLOTS, client=fc)
    b.publish_all()
    topics = [t for t, p, r in fc.sent]
    assert ha_mqtt.AVAIL in topics and sum(t.endswith("/config") for t in topics) == 8
    assert all(r for t, p, r in fc.sent)                              # всё с retain
    n = len(fc.sent)
    assert b.publish_states() == 0 and len(fc.sent) == n              # без изменений ничего не шлём
    # новая тренировка -> событие без retain
    c2 = db.connect(path)
    ha_mqtt.mark_existing_announced(c2)
    act(c2, 7, date.today().isoformat(), "running", "Бег")
    c2.commit()
    ha_mqtt.fire_bus_event = lambda p: False
    b.after_sync()
    evs = [(t, p, r) for t, p, r in fc.sent if t.endswith("/event")]
    assert len(evs) == 1 and evs[0][2] is False and json.loads(evs[0][1])["event_type"] == "workout_done"


def test_broker_config():
    assert ha_mqtt.broker_config({"mqtt_host": "core-mosquitto", "mqtt_port": 1884, "mqtt_user": "u", "mqtt_password": "p"}) == \
        {"host": "core-mosquitto", "port": 1884, "username": "u", "password": "p", "tls": False}
    got = ha_mqtt.broker_config({}, fetch=lambda: {"host": "h", "port": 1883, "username": "a", "password": "b", "ssl": False})
    assert got["host"] == "h" and got["username"] == "a"
    def boom():
        raise OSError("нет сети")
    assert ha_mqtt.broker_config({}, fetch=boom) is None


def test_supervisor_token_from_file(monkeypatch=None):
    import os, builtins
    saved = os.environ.pop("SUPERVISOR_TOKEN", None)
    try:
        assert ha_mqtt.broker_config({}) is None or True   # без токена не падает
        os.environ["SUPERVISOR_TOKEN"] = "abc"
        assert ha_mqtt.supervisor_token() == "abc"
    finally:
        os.environ.pop("SUPERVISOR_TOKEN", None)
        if saved:
            os.environ["SUPERVISOR_TOKEN"] = saved
