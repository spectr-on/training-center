"""Сенсоры и события Home Assistant через MQTT discovery (раздел 11 спецификации).

Состояния публикуются с retain, поэтому после перезагрузки HA сенсоры восстанавливаются сами.
Сборка сообщений (build_*) не зависит от сети и покрыта тестами; paho-mqtt подключается лениво.
"""
import json, os, threading, time, urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from . import plan, timeutil, traffic, settings as stg

PREFIX = "training_center"
AVAIL = f"{PREFIX}/status"
HA_STATUS = "homeassistant/status"
DISCOVERY = "homeassistant"
DEVICE = {"identifiers": [PREFIX], "name": "Тренировочный центр", "manufacturer": "spectr-on",
          "model": "Аддон Тренировочный центр"}
TL_RU = {"green": "зелёный", "yellow": "жёлтый", "red": "красный"}
STATUS_RU = plan.LABEL
EVENT_TYPE = "workout_done"
BUS_EVENT = "training_workout_done"
REFRESH_SEC = 300
ANNOUNCE_DAYS = 2   # о тренировках старше этого срока события не шлём


# ---------- сущности ----------
def entities():
    """(домен, ключ, параметры discovery без общих полей)."""
    return [
        ("sensor", "training_traffic_light", {"name": "Светофор готовности", "icon": "mdi:traffic-light",
                                               "json_attributes": True}),
        ("sensor", "training_today_session", {"name": "Тренировка сегодня", "icon": "mdi:dumbbell",
                                               "json_attributes": True}),
        ("sensor", "training_week_done", {"name": "Тренировок за неделю", "icon": "mdi:calendar-check",
                                           "state_class": "measurement", "unit_of_measurement": "шт.",
                                           "json_attributes": True}),
        ("sensor", "training_last_sync", {"name": "Последняя синхронизация Garmin", "device_class": "timestamp",
                                           "icon": "mdi:sync", "json_attributes": True}),
        ("binary_sensor", "training_garmin_ok", {"name": "Garmin: вход работает", "device_class": "connectivity",
                                                  "payload_on": "ON", "payload_off": "OFF", "json_attributes": True}),
        ("event", "training_workout_done", {"name": "Тренировка выполнена", "icon": "mdi:check-circle-outline",
                                             "event_types": [EVENT_TYPE]}),
    ]


def state_topic(key):
    return f"{PREFIX}/{key}/state"


def attr_topic(key):
    return f"{PREFIX}/{key}/attributes"


def event_topic(key):
    return f"{PREFIX}/{key}/event"


def build_discovery():
    """-> [(topic, payload_json)] — конфигурации для автоматического создания сущностей."""
    out = []
    for domain, key, params in entities():
        p = dict(params)
        has_attrs = p.pop("json_attributes", False)
        cfg = {"name": p.pop("name"), "unique_id": f"{PREFIX}_{key}",
               "default_entity_id": f"{domain}.{key}", "device": DEVICE,
               "availability_topic": AVAIL, "payload_available": "online", "payload_not_available": "offline"}
        if domain == "event":
            cfg["state_topic"] = event_topic(key)
        else:
            cfg["state_topic"] = state_topic(key)
        if has_attrs:
            cfg["json_attributes_topic"] = attr_topic(key)
        cfg.update(p)
        out.append((f"{DISCOVERY}/{domain}/{PREFIX}/{key}/config", json.dumps(cfg, ensure_ascii=False)))
    return out


# ---------- состояния ----------
def traffic_state(con, today):
    d = today.isoformat()
    m = traffic.metrics(con, d)
    wait = traffic.missing_morning(m)
    if all(m[k] is None for k in ("readiness", "sleep_h", "hrv", "rhr", "bb")):
        return "нет данных", {"date": d, "причины": ["за сегодня данных ещё нет"], "ожидаем": []}
    if wait:
        return "нет данных", {"date": d, "причины": ["Garmin ещё не обработал: " + ", ".join(wait)], "ожидаем": wait,
                              "показатели": m}
    color, why, notes = traffic.decide(m)
    return TL_RU[color], {"date": d, "color": color, "причины": why, "заметки": notes,
                          "план": traffic.PLANS[color], "показатели": m}


def _cells(con, slots, today):
    cells, ok = plan.week_status(con, {"slots": slots}, plan.monday(today), today)
    return cells, ok


def session_state(cells, today):
    """Тренировка, запланированная на сегодня; иначе — «отдых»."""
    todays = [c for c in cells if c["planned"] == today]
    others = [{"slot": c["slot"]["key"], "название": c["slot"]["label"], "статус": STATUS_RU[c["status"]]}
              for c in cells if c["status"] in ("late", "planned") and c["planned"] != today]
    if not todays:
        return "отдых", {"статус": "на сегодня ничего не запланировано", "статус_код": "rest", "ещё_впереди": others}
    c = todays[0]
    return c["slot"]["label"], {"slot": c["slot"]["key"], "статус": STATUS_RU[c["status"]], "статус_код": c["status"],
                                "ещё_впереди": others}


def week_state(cells, ok, today):
    done = sum(1 for c in cells if c["status"] in ("done", "moved"))
    return done, {"запланировано": len(cells), "неделя_выполнена": bool(ok),
                  "начало_недели": plan.monday(today).isoformat(),
                  "тренировки": [{"slot": c["slot"]["key"], "название": c["slot"]["label"],
                                   "статус": STATUS_RU[c["status"]]} for c in cells]}


def _iso_local(s):
    """'2026-10-13 08:05:00' (местное) -> ISO с часовым поясом для device_class timestamp."""
    return datetime.strptime(s, timeutil.FMT).replace(tzinfo=timeutil.tz()).isoformat()


def sync_states(con):
    r = con.execute("SELECT * FROM sync_run WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
    if not r:
        return None, "OFF", {"результат": "синхронизаций ещё не было"}
    attrs = {"результат": "успешно" if r["ok"] else "ошибка", "запуск": r["trigger"], "активностей": r["activities"],
             "ошибки": r["errors"] or ""}
    ts = None
    try:
        ts = _iso_local(r["finished_at"])
    except Exception:
        pass
    return ts, "ON" if r["ok"] else "OFF", attrs


def build_states(con, slots, today=None):
    """-> {ключ: (состояние, атрибуты)} для всех сенсоров."""
    today = today or timeutil.today()
    cells, ok = _cells(con, slots, today)
    ts, garmin_ok, sattrs = sync_states(con)
    out = {"training_traffic_light": traffic_state(con, today),
           "training_today_session": session_state(cells, today),
           "training_week_done": week_state(cells, ok, today),
           "training_last_sync": (ts, sattrs),
           "binary:training_garmin_ok": (garmin_ok, sattrs)}
    return out


def state_messages(states):
    """-> [(topic, payload, retain)]"""
    msgs = []
    for key, (state, attrs) in states.items():
        key = key.split(":")[-1]
        payload = "None" if state is None else str(state)
        msgs.append((state_topic(key), payload, True))
        msgs.append((attr_topic(key), json.dumps(attrs, ensure_ascii=False, default=str), True))
    return msgs


# ---------- событие «тренировка выполнена» ----------
def mark_existing_announced(con):
    """При первом включении (таблица пуста) считаем уже известные тренировки объявленными, чтобы не сыпать событиями."""
    if con.execute("SELECT COUNT(*) FROM mqtt_event").fetchone()[0] == 0:
        con.execute("INSERT OR IGNORE INTO mqtt_event SELECT id, ? FROM activity", (timeutil.now(),))
        con.commit()


def new_workouts(con, slots, today=None, days=ANNOUNCE_DAYS):
    """Новые (ещё не объявленные) тренировки с сопоставлением со слотом плана. Помечает их объявленными."""
    today = today or timeutil.today()
    since = (today - timedelta(days=days)).isoformat()
    rows = con.execute("SELECT id,start_local,type_key,name FROM activity WHERE id NOT IN (SELECT activity_id FROM mqtt_event) "
                       "ORDER BY start_local").fetchall()
    out = []
    for r in rows:
        con.execute("INSERT OR IGNORE INTO mqtt_event VALUES(?,?)", (r["id"], timeutil.now()))
        day = (r["start_local"] or "")[:10]
        if not day or day < since:
            continue
        mon = plan.monday(datetime.strptime(day, "%Y-%m-%d").date())
        cells, ok = plan.week_status(con, {"slots": slots}, mon, today)
        cell = next((c for c in cells if c["activity"] and c["activity"]["id"] == r["id"]), None)
        out.append({"event_type": EVENT_TYPE, "activity_id": r["id"], "start": r["start_local"],
                    "type": r["type_key"], "name": r["name"],
                    "slot": cell["slot"]["key"] if cell else None,
                    "slot_label": cell["slot"]["label"] if cell else None,
                    "planned": cell is not None,
                    "status": STATUS_RU[cell["status"]] if cell else None,
                    "week_done": ok})
    con.commit()
    return out


# ---------- подключение ----------
def _options():
    try:
        return json.loads(Path("/data/options.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def broker_config(opts=None, fetch=None):
    """Брокер: сначала настройки аддона, затем сервис Supervisor (Mosquitto из магазина HA)."""
    o = _options() if opts is None else opts
    if o.get("mqtt_host"):
        return {"host": o["mqtt_host"], "port": int(o.get("mqtt_port") or 1883),
                "username": o.get("mqtt_user") or None, "password": o.get("mqtt_password") or None, "tls": False}
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token and fetch is None:
        return None
    try:
        if fetch:
            d = fetch()
        else:
            req = urllib.request.Request("http://supervisor/services/mqtt", headers={"Authorization": f"Bearer {token}"})
            d = json.loads(urllib.request.urlopen(req, timeout=10).read().decode())["data"]
        return {"host": d["host"], "port": int(d.get("port") or 1883), "username": d.get("username"),
                "password": d.get("password"), "tls": bool(d.get("ssl"))}
    except Exception as e:  # noqa
        print("MQTT: брокер не получен от Supervisor:", e, flush=True)
        return None


def fire_bus_event(payload):
    """Настоящее событие шины HA training_workout_done (по желанию; нужен homeassistant_api в config.yaml)."""
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return False
    try:
        req = urllib.request.Request(f"http://supervisor/core/api/events/{BUS_EVENT}", method="POST",
                                     data=json.dumps(payload, ensure_ascii=False).encode(),
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10).read()
        return True
    except Exception as e:  # noqa
        print("MQTT: событие шины HA не отправлено:", e, flush=True)
        return False


class Bridge:
    """Держит соединение с брокером и публикует сообщения. client можно подменить в тестах."""

    def __init__(self, db_connect, slots_fn, client=None):
        self.db_connect = db_connect      # () -> sqlite-соединение для текущего потока
        self.slots_fn = slots_fn          # (con) -> слоты плана
        self.client = client
        self.connected = False
        self.last = {}
        self.lock = threading.Lock()
        self.error = None

    # --- сеть ---
    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        cfg = None
        while cfg is None:
            cfg = broker_config()
            if cfg is None:
                self.error = "брокер MQTT не найден: установите Mosquitto или задайте mqtt_host в настройках аддона"
                print("MQTT:", self.error, flush=True)
                time.sleep(600)
        try:
            self._connect(cfg)
        except Exception as e:  # noqa
            self.error = str(e)
            print("MQTT: ошибка запуска:", e, flush=True)
            return
        while True:
            time.sleep(REFRESH_SEC)
            try:
                self.publish_states()
            except Exception as e:  # noqa
                print("MQTT: ошибка обновления:", e, flush=True)

    def _connect(self, cfg):
        import paho.mqtt.client as mqtt
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="training_center")
        if cfg.get("username"):
            c.username_pw_set(cfg["username"], cfg.get("password"))
        if cfg.get("tls"):
            c.tls_set()
        c.will_set(AVAIL, "offline", retain=True)
        c.on_connect = self._on_connect
        c.on_disconnect = self._on_disconnect
        c.on_message = self._on_message
        c.reconnect_delay_set(min_delay=2, max_delay=120)
        self.client = c
        c.connect_async(cfg["host"], cfg["port"], keepalive=60)
        c.loop_start()

    def _on_connect(self, client, userdata, flags, rc, props=None):
        if getattr(rc, "is_failure", False) or (isinstance(rc, int) and rc != 0):
            self.error = f"брокер отклонил подключение: {rc}"
            print("MQTT:", self.error, flush=True)
            return
        self.connected, self.error = True, None
        print("MQTT: подключено", flush=True)
        client.subscribe(HA_STATUS)
        self.publish_all()

    def _on_disconnect(self, client, userdata, *args):
        self.connected = False
        print("MQTT: соединение потеряно", flush=True)

    def _on_message(self, client, userdata, msg):
        if msg.topic == HA_STATUS and msg.payload.decode(errors="ignore") == "online":
            self.publish_all()   # HA перезапустился — заново объявляем сущности

    # --- публикация ---
    def _pub(self, topic, payload, retain=True):
        if self.client is not None:
            self.client.publish(topic, payload, qos=1, retain=retain)

    def publish_all(self):
        with self.lock:
            self.last = {}
            for t, p in build_discovery():
                self._pub(t, p)
            self._pub(AVAIL, "online")
        self.publish_states()

    def publish_states(self):
        """Публикует только изменившиеся состояния. Возвращает число отправленных сообщений."""
        con = self.db_connect()
        try:
            msgs = state_messages(build_states(con, self.slots_fn(con)))
        finally:
            con.close()
        n = 0
        with self.lock:
            for t, p, r in msgs:
                if self.last.get(t) != p:
                    self._pub(t, p, r)
                    self.last[t] = p
                    n += 1
        return n

    def after_sync(self):
        """Вызывается после синхронизации: обновляет сенсоры и объявляет новые тренировки."""
        self.publish_states()
        con = self.db_connect()
        try:
            events = new_workouts(con, self.slots_fn(con))
        finally:
            con.close()
        for ev in events:
            self._pub(event_topic("training_workout_done"), json.dumps(ev, ensure_ascii=False), retain=False)
            fire_bus_event(ev)
            print("MQTT: событие training_workout_done —", ev["name"], flush=True)
