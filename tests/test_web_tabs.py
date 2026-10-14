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


if __name__ == "__main__":
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v()
    print("OK")
