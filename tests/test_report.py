import sys, pathlib, tempfile, json, datetime, urllib.error
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "training_center"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from app import program, report, body, progress, settings as stg
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
              "## Восстановление", "## Факторы дня", "| 10-12 | да | — | — | — |", "## Вопросы для ИИ", report.DEFAULT_QUESTION):
        assert s in t, s
    assert "90×8, 95×8, 95×8" in t and "свой вес×7, свой вес×7" in t
    assert "token" not in t.lower()


def test_weight_measures_feel_and_notes():
    con, act = tp.mkdb()
    act(3, "2026-10-05", "Full Body A", [("BARBELL_BACK_SQUAT", 8, 85)] * 3 + [("DUMBBELL_BICEPS_CURL", 10, 10)])
    d, _ = program.build_draft(con)
    d["evaluate_from"] = "2026-10-12"
    program.save_version(con, program.dump(d), "ручная", "т", True)
    progress.apply_all(con)
    con.execute("INSERT INTO activity VALUES(9,'2026-10-11 08:00:00','running','Бег','%s')" % json.dumps({"distance": 5200, "duration": 1900}))
    con.execute("INSERT INTO activity VALUES(10,'2026-10-09 08:00:00','running','Бег','{}')")
    con.commit()
    t = report.build(con, today=TODAY)
    # силовые: пометки
    assert "| 90×8, 95×8, 95×8 | вес ↑ 85→95 кг; верх диапазона на всех подходах, вес повышен во всех подходах → 92.5/97.5/97.5 кг; повторы снова с низа диапазона |" in t, t
    assert "до верха диапазона (10) не дотянул: 9" in t
    assert "| Подтягивания" in t or "свой вес×7" in t
    # вес: пока нет записей
    assert "## Вес тела" in t and "| | нет записей |" in t and "За период нет записей веса тела" in t
    assert "Замеров лентой ещё нет." in t and "Полных замеров лентой ещё не было" in t
    assert "| 10-11 | 5,2 | 6:05 | — | — | не отмечено |" in t and "Бег 10-11: не отмечено, как ощущался" in t
    # теперь данные
    for at, w, f in (("2026-10-13 07:30", "86,85", "21,2"), ("2026-10-12 07:30", "85,05", "20"), ("2026-10-06 07:30", "85", ""),
                     ("2026-09-20 07:30", "84", "")):
        assert body.add_weight(con, at, {"weight_kg": w, "fat_pct": f}, TODAY)[0]
    body.add_measures(con, "2026-09-15", {"waist": "91", "chest": "104"}, TODAY)
    body.add_measures(con, "2026-10-10", {"waist": "90,5", "chest": "104,5", "arm": "36"}, TODAY)
    body.set_feel(con, 9, 4, "ноги лёгкие")
    t = report.build(con, today=TODAY)
    assert "| 10-13 07:30 | 86,85 | 21,2 | 18,4 | 68,4 | — | manual |" in t, t
    assert "| 10-06 07:30 | 85 | — | — | — | — | manual |" in t
    assert "09-20" not in t.split("## Вес тела")[1].split("## Замеры")[0].split("Среднее")[0]    # старше 14 дней в таблицу не идёт
    assert "Среднее за 7 дней: 85,9 кг (жир 20,6%, безжировая 68,2 кг), дней с записью: 2." in t, t
    assert "Среднее за 28 дней: 85,2 кг, дней с записью: 4; сдвиг недели к 28 дням: +0,7 кг." in t, t
    assert "| Замер | 09-15 | 10-10 | Δ к предыдущим |" in t and "| Талия | 91 | 90,5 | -0,5 |" in t, t
    assert "| Бицепс (правый) | — | 36 | |" in t
    assert "| 10-11 | 5,2 | 6:05 | — | — | 4/5 — легко; ноги лёгкие |" in t
    assert "Бег 10-09: не отмечено, как ощущался" in t
    assert "Пора делать замеры" not in t and "За период нет записей веса" not in t


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


def test_lifestyle_block_new_format():
    con = setup()
    day = lambda d, items: con.execute("INSERT OR REPLACE INTO daily_raw VALUES(?,?,?,'x')",
                                       (d, "lifestyle", json.dumps({"dailyLogsReport": items})))
    day("2026-10-11", [{"name": "Кальян", "logStatus": "YES", "measurementType": "QUANTITY", "details": [{"amount": 3}]},
                       {"name": "Alcohol", "logStatus": "YES", "measurementType": "QUANTITY",
                        "details": [{"subTypeId": 1, "subTypeName": "BEER", "amount": 1}]},
                       {"name": "Healthy Meals", "logStatus": "YES", "measurementType": "NONE"},
                       {"name": "Болезнь", "logStatus": "NO", "measurementType": "NONE"}])
    day("2026-10-13", [{"name": "Кальян", "measurementType": "QUANTITY"}, {"name": "Alcohol", "measurementType": "QUANTITY",
                       "details": [{"subTypeName": "BEER"}]}])          # ничего не отмечено -> строки нет
    con.commit()
    t = report.build(con, today=TODAY)
    assert "| 10-11 | 3 | пиво 1 | нет | здоровая еда |" in t, t
    assert "| 10-13 |" not in t.split("## Факторы дня")[1].split("Дата —")[0]
    assert "фактор дня —" not in t
    assert "| Дата | Сон, ч | Sleep Score | Стресс во сне | HRV |" in t
