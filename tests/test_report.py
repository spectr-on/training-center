import sys, pathlib, tempfile, json, datetime, urllib.error
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "training_center"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from app import program, report, settings as stg
import test_program as tp

stg.FILE = pathlib.Path(tempfile.mkdtemp()) / "settings.json"
TODAY = datetime.date(2026, 10, 13)


def setup():
    con, act = tp.mkdb()
    d, _ = program.build_draft(con)
    program.save_version(con, program.dump(d), "ручная", "т", True)
    con.execute("INSERT INTO activity VALUES(9,'2026-10-11 08:00:00','running','Бег','%s')" %
                json.dumps({"distance": 5200, "duration": 1900, "averageHR": 148.4, "aerobicTrainingEffect": 3.1}))
    con.execute("INSERT INTO daily_raw VALUES('2026-10-12','lifestyle',?,'x')", (json.dumps(
        {"dailyLogsReport": [{"name": "Кальян", "logStatus": "YES", "details": []}]}),))
    con.commit()
    return con


def test_build_has_all_blocks():
    con = setup()
    t = report.build(con, today=TODAY)
    for s in ("# Отчёт о тренировках", "Период: 2026-10-07 — 2026-10-13", "## Светофор", "## План и факт",
              "### 2026-10-12 · Full Body A · done", "## Следующая тренировка", "## Бег", "| 10-11 | 5,2 | 6:05 | 148 |",
              "## Восстановление", "кальян", "## Вопросы для ИИ", report.DEFAULT_QUESTION):
        assert s in t, s
    assert "90×8, 95×8, 95×8" in t and "свой вес×7, свой вес×7" in t
    assert "token" not in t.lower()


def test_hide_blocks():
    t = report.build(setup(), today=TODAY, hide=("traffic", "recovery"))
    assert "## Светофор" not in t and "## Восстановление" not in t


def test_push_without_token_and_errors():
    orig = report.get_token
    report.get_token = lambda: None
    ok, msg = report.push("x", TODAY)
    report.get_token = orig
    assert not ok and "токен" in msg
    calls = []
    def fake(method, url, token, body=None):
        calls.append((method, url.split("/contents/")[1], body and body.get("sha")))
        if method == "GET":
            if "latest" in url:
                return {"sha": "abc"}
            raise urllib.error.HTTPError(url, 404, "nf", {}, None)
        return {}
    orig = report._gh
    report._gh = fake
    ok, msg = report.push("текст", TODAY, token="SECRET", repo="u/r")
    assert ok, msg
    assert ("PUT", "latest.md", "abc") in calls and ("PUT", "reports/2026-10-13.md", None) in calls
    def bad(method, url, token, body=None):
        raise urllib.error.HTTPError(url, 401, "x SECRET", {}, None)
    report._gh = bad
    ok, msg = report.push("т", TODAY, token="SECRET", repo="u/r")
    report._gh = orig
    assert not ok and "401" in msg and "SECRET" not in msg
    assert stg.get("report_status")["ok"] is False


def test_auto():
    con = setup()
    assert report.auto_after_sync(con) is None                 # выключен
    stg.set_value("report_auto", True)
    sent = []
    orig = report.push
    report.push = lambda text, *a, **k: (sent.append(text) or (True, "ок"))
    assert report.auto_after_sync(con)[0] is True
    assert report.auto_after_sync(con) is None                 # новых тренировок нет
    report.push = orig
    assert len(sent) == 1


if __name__ == "__main__":
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v(); print("ok", k)
