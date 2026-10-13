"""Отчёт для ИИ: один Markdown-файл (формат — приложение B спецификации) и отправка в GitHub."""
import base64, json, os
import urllib.request, urllib.error
from datetime import date, timedelta
from pathlib import Path

from . import traffic, program, plan, timeutil, body, settings as stg
from .exercise_names import ru
from .lifestyle import parse_day

RULES_VERSION = 1
DEFAULT_REPO = "spectr-on/training-reports"
DEFAULT_QUESTION = "Оцени неделю, найди проблемы, предложи правки программы."
COLOR = {"green": "зелёный", "yellow": "жёлтый", "red": "красный"}
FACTOR = {"alcohol": "алкоголь", "hookah": "кальян", "illness": "болезнь"}
BLOCKS = {"traffic": "Светофор", "recovery": "Восстановление"}


def n(v):
    """Число по-русски: 92,5."""
    return f"{v:g}".replace(".", ",")


def sg(v):
    """Число со знаком по-русски: +1,3."""
    return f"{v:+g}".replace(".", ",")


def _sets_text(sets):
    return ", ".join(f"{'свой вес' if not kg else n(kg)}×{reps}" for reps, kg in sets)


def _daily_lifestyle(con, d):
    r = con.execute("SELECT json FROM daily_raw WHERE date=? AND kind='lifestyle'", (d,)).fetchone()
    try:
        return parse_day(json.loads(r["json"])) if r else {}
    except Exception:
        return {}


def _days(start, end):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def _traffic_rows(con, start, end):
    rows = []
    for d in _days(start, end):
        m = traffic.metrics(con, d.isoformat())
        if traffic.missing_morning(m):
            continue
        color, why, _ = traffic.decide(m)
        rows.append((d, color, why, m))
    return rows


def _strength(con, start, end, div):
    out = []
    acts = con.execute("SELECT * FROM activity WHERE type_key='strength_training' AND substr(start_local,1,10)>=? "
                       "AND substr(start_local,1,10)<=? ORDER BY start_local", (start.isoformat(), end.isoformat())).fetchall()
    for a in acts:
        per, order = {}, []
        for r in con.execute("SELECT * FROM workout_set WHERE activity_id=? AND set_type='ACTIVE' ORDER BY idx", (a["id"],)):
            ex = r["exercise"]
            if ex not in per:
                per[ex] = []
                order.append(ex)
            per[ex].append((r["reps"] or 0, (r["weight_raw"] or 0) / div))
        out.append((a, order, per))
    return out


def _plan_info(pdata):
    """код упражнения -> (верх диапазона повторов, число плановых подходов) по активной программе."""
    out = {}
    for s in (pdata or {}).get("sessions", []):
        if s.get("kind") != "strength":
            continue
        for b in s.get("blocks", []):
            for x in b["exercises"]:
                out.setdefault(x["code"], (x["reps"][1], len(x["weights"])))
    return out


def _sets_of(con, activity_id, ex, div):
    return [(r["reps"] or 0, (r["weight_raw"] or 0) / div) for r in con.execute(
        "SELECT reps,weight_raw FROM workout_set WHERE activity_id=? AND set_type='ACTIVE' AND exercise=? ORDER BY idx",
        (activity_id, ex))]


def _exercise_notes(con, act, ex, sets, info, div):
    """Пометки к упражнению: сравнение с прошлой тренировкой, верх диапазона, прибавка."""
    notes = []
    prev = con.execute(
        "SELECT a.id FROM activity a WHERE a.type_key='strength_training' AND a.start_local<? AND EXISTS "
        "(SELECT 1 FROM workout_set w WHERE w.activity_id=a.id AND w.set_type='ACTIVE' AND w.exercise=?) "
        "ORDER BY a.start_local DESC LIMIT 1", (act["start_local"], ex)).fetchone()
    kg, reps = max(k for _, k in sets), sum(r for r, _ in sets)
    if not prev:
        notes.append("раньше в базе не было")
    else:
        ps = _sets_of(con, prev["id"], ex, div)
        pk, pr = max(k for _, k in ps), sum(r for r, _ in ps)
        if kg > pk + 1e-9:
            notes.append(f"вес ↑ {n(pk)}→{n(kg)} кг")
        elif kg < pk - 1e-9:
            notes.append(f"вес ↓ {n(pk)}→{n(kg)} кг")
        elif reps > pr:
            notes.append(f"повторов +{reps - pr}")
        elif reps < pr:
            notes.append(f"повторов −{pr - reps}")
        else:
            notes.append("как в прошлый раз")
    if ex in info:
        top, cnt = info[ex]
        short = [r for r, _ in sets[-cnt:] if r < top]
        if short:
            notes.append(f"до верха диапазона ({top}) не дотянул: {', '.join(str(r) for r in short)}")
    log = con.execute("SELECT text FROM progress_log WHERE activity_id=? AND code=?", (act["id"], ex)).fetchone()
    if log:
        notes.append("верх диапазона на всех подходах, прибавка → " + log["text"].split("→")[-1].strip() + " кг")
    return "; ".join(notes)


def _weight_block(con, today, days):
    """Вес тела: записи по дням за период (не меньше 14 дней) и средние."""
    start = today - timedelta(days=max(days, 14) - 1)
    rows = body.weights_between(con, start, today)
    L = ["## Вес тела", "| Дата | Вес, кг | Жир, % | Жир, кг | Безжировая, кг | Вода, % | Источник |",
         "|---|---|---|---|---|---|---|"]
    f = lambda v: "—" if v is None else n(v)
    for r in rows:
        L.append(f"| {r['at'][5:16]} | {n(r['weight_kg'])} | {f(r['fat_pct'])} | {f(r['fat_kg'])} | {f(r['lean_kg'])} | "
                 f"{f(r['water_pct'])} | {r['source']} |")
    if not rows:
        L.append("| | нет записей | | | | | |")
    a7, a28 = body.window_avg(con, today, 7), body.window_avg(con, today, 28)
    if a7:
        extra = ", ".join(x for x in (f"жир {n(round(a7['fat_pct'], 1))}%" if a7["fat_pct"] is not None else "",
                                       f"безжировая {n(round(a7['lean_kg'], 1))} кг" if a7["lean_kg"] is not None else "") if x)
        line = f"Среднее за 7 дней: {n(round(a7['weight'], 1))} кг" + (f" ({extra})" if extra else "") + f", дней с записью: {a7['days']}."
        if a28 and a28["days"] > a7["days"]:
            line += (f" Среднее за 28 дней: {n(round(a28['weight'], 1))} кг, дней с записью: {a28['days']}; "
                     f"сдвиг недели к 28 дням: {sg(round(a7['weight'] - a28['weight'], 1))} кг.")
        L += ["", line]
    elif rows:
        L += ["", "За последние 7 дней записей веса нет."]
    if rows:
        L += ["Жир и вода — по биоимпедансу, единичное значение шумит на 1–2 кг и 1–2 п.п.: ориентируйся на средние."]
    L.append("")
    return L


def _measure_block(con):
    L = ["## Замеры лентой, см"]
    sets = body.last_measures(con, 3)
    if not sets:
        return L + ["Замеров лентой ещё нет.", ""]
    L += ["| Замер | " + " | ".join(s["date"][5:] for s in sets) + (" | Δ к предыдущим |" if len(sets) > 1 else " |"),
          "|---|" + "---|" * len(sets) + ("---|" if len(sets) > 1 else "")]
    for key, (label, _) in body.MEASURES.items():
        vals = [s[key] for s in sets]
        if all(v is None for v in vals):
            continue
        row = f"| {label} | " + " | ".join("—" if v is None else n(v) for v in vals) + " |"
        if len(sets) > 1:
            a, b = vals[-1], vals[-2]
            row += ("" if a is None or b is None else " " + sg(round(a - b, 1))) + " |"
        L.append(row)
    L.append("")
    return L


def build(con, today=None, days=7, div=1000.0, hide=()):
    """Markdown-отчёт за последние days дней (включая сегодня)."""
    today = today or timeutil.today()
    start = today - timedelta(days=days - 1)
    prow, pdata = program.active(con)
    ver = f"программа v{prow['id']}" if prow else "программы нет"
    L = ["# Отчёт о тренировках", f"Период: {start.isoformat()} — {today.isoformat()}",
         f"Создан: {timeutil.now()[:16]} · {ver} · правила светофора v{RULES_VERSION}", ""]
    notes = []

    if "traffic" not in hide:
        rows = _traffic_rows(con, start, today)
        L += ["## Светофор", "| Дата | Цвет | Причины | Утренние показатели |", "|---|---|---|---|"]
        for d, color, why, m in rows:
            f = lambda v, u="": "—" if v is None else f"{n(v) if isinstance(v, float) else v}{u}"
            vals = (f"Readiness {f(m['readiness'])}, BB {f(m['bb'])}, сон {f(m['sleep_h'], ' ч')}, "
                    f"HRV {f(m['hrv'])}, пульс покоя {f(m['rhr'])}")
            L.append(f"| {d.strftime('%m-%d')} | {COLOR[color]} | {'; '.join(why)} | {vals} |")
        if not rows:
            L.append("| | нет данных | утренние данные за период не загружены | |")
        L.append("")

    L.append("## План и факт")
    strength = _strength(con, start, today, div)
    info = _plan_info(pdata)
    if not strength:
        L += ["Силовых тренировок за период нет.", ""]
    for a, order, per in strength:
        L += [f"### {a['start_local'][:10]} · {a['name'] or 'Силовая'} · done",
              "| Упражнение | Подходы (кг×повторы) | Пометки |", "|---|---|---|"]
        for ex in order:
            mark = ru(ex)
            if mark.endswith("⁇"):
                notes.append(f"Упражнение без русского названия: {ex}")
            L.append(f"| {mark} | {_sets_text(per[ex])} | {_exercise_notes(con, a, ex, per[ex], info, div)} |")
        L.append("")
    # слоты недель без выполнения
    week = []
    mon = plan.monday(today)
    slots = program.slots_of(pdata, stg.day_overrides()) if pdata else stg.load()["slots"]
    cells, _ = plan.week_status(con, {"slots": slots}, mon, today)
    for c in cells:
        if c["status"] in ("missed", "late"):
            week.append(f"{c['slot']['label']}: {plan.LABEL[c['status']]}")
        elif c["status"] == "moved":
            notes.append(f"{c['slot']['label']}: выполнено с переносом, {c['date'].isoformat()}")
    if week:
        L += ["Текущая неделя:"] + [f"- {x}" for x in week] + [""]
    elif cells:
        L += ["Текущая неделя: " + "; ".join(f"{c['slot']['label']} — {plan.LABEL[c['status']]}" for c in cells), ""]

    if pdata:
        for s in pdata["sessions"]:
            if s["kind"] != "strength":
                continue
            L += [f"## Следующая тренировка ({s['name']})",
                  "| Упражнение | Вес по подходам, кг | Цель повторов | Почему |", "|---|---|---|---|"]
            for b in s["blocks"]:
                for x in b["exercises"]:
                    last = con.execute("SELECT at,text FROM progress_log WHERE code=? ORDER BY at DESC LIMIT 1", (x["code"],)).fetchone()
                    why = "по программе"
                    if last:
                        why = f"прибавка {last['at'][:10]}: {last['text'].split(': ', 1)[-1].replace(x['code'], '').strip()}"
                    wt = "/".join(n(w) if w else "свой вес" for w in x["weights"])
                    L.append(f"| {ru(x['code'])} | {wt} | {x['reps'][0]}–{x['reps'][1]} | {why} |")
            L.append("")

    L += ["## Бег", "| Дата | Км | Темп | Пульс | Нагрузка | Как ощущался |", "|---|---|---|---|---|---|"]
    runs = con.execute("SELECT * FROM activity WHERE type_key IN ('running','treadmill_running','trail_running') "
                       "AND substr(start_local,1,10)>=? ORDER BY start_local", (start.isoformat(),)).fetchall()
    for r in runs:
        j = json.loads(r["json"] or "{}")
        km, sec = (j.get("distance") or 0) / 1000, j.get("duration") or 0
        pace = f"{int(sec / km // 60)}:{int(sec / km % 60):02d}" if km else "—"
        hr = j.get("averageHR")
        te = j.get("aerobicTrainingEffect")
        fl = body.feel_of(con, r["id"])
        feel = "не отмечено" if fl is None else f"{fl[0]}/5 — {body.FEEL[fl[0]]}" + (f"; {fl[1]}" if fl[1] else "")
        if fl is None:
            notes.append(f"Бег {r['start_local'][5:10]}: не отмечено, как ощущался")
        L.append(f"| {r['start_local'][5:10]} | {n(round(km, 2))} | {pace} | {'—' if hr is None else round(hr)} | "
                 f"{'—' if te is None else 'аэробный эффект ' + n(round(te, 1))} | {feel} |")
    if not runs:
        L.append("| | | | | за период бега нет | |")
    else:
        L.append("")
        L.append("Как ощущался: 1 — очень тяжело, 3 — нормально, 5 — очень легко.")
    L.append("")

    if "recovery" not in hide:
        L += ["## Восстановление", "| Дата | Сон, ч | HRV | Пульс покоя | Body Battery |", "|---|---|---|---|---|"]
        for d in _days(start, today):
            m = traffic.metrics(con, d.isoformat())
            if all(m[k] is None for k in ("sleep_h", "hrv", "rhr", "bb")):
                continue
            f = lambda v: "—" if v is None else (n(v) if isinstance(v, float) else v)
            L.append(f"| {d.strftime('%m-%d')} | {f(m['sleep_h'])} | {f(m['hrv'])} | {f(m['rhr'])} | {f(m['bb'])} |")
        if L[-1].startswith("|---"):
            L.append("| | нет данных | | | |")
        L.append("")

    L += _weight_block(con, today, days)
    L += _measure_block(con)
    if not body.weights_between(con, start, today):
        notes.append("За период нет записей веса тела")
    wdue, wlast, wpassed, wiv = body.weight_status(con, today)
    if wdue and wlast is not None:
        notes.append(f"Давно не было взвешивания: последняя запись {wlast}, прошло {wpassed} дн.")
    due, last, passed, iv = body.measure_status(con, today)
    if due:
        notes.append("Полных замеров лентой ещё не было" if last is None else
                     f"Пора делать замеры лентой: последние {last}, прошло {passed} дн. (интервал {iv} дн.)")

    for d in _days(start, today):
        fac = _daily_lifestyle(con, d.isoformat())
        if fac:
            notes.append(f"{d.strftime('%m-%d')}: фактор дня — {', '.join(FACTOR[k] for k in fac)}")
    L.append("## Замечания")
    L += [f"- {x}" for x in notes] or ["- Замечаний нет."]
    L += ["", "## Вопросы для ИИ", stg.get("report_question") or DEFAULT_QUESTION, ""]
    return "\n".join(L)


# ---------- GitHub ----------

def get_token():
    try:
        t = json.loads(Path("/data/options.json").read_text(encoding="utf-8")).get("github_token")
        if t:
            return t.strip()
    except Exception:
        pass
    try:
        return Path("/config/github_token.txt").read_text(encoding="utf-8-sig").strip() or None
    except Exception:
        return None


def get_repo():
    try:
        r = json.loads(Path("/data/options.json").read_text(encoding="utf-8")).get("github_repo")
        if r:
            return r.strip()
    except Exception:
        pass
    return DEFAULT_REPO


def _gh(method, url, token, body=None):
    req = urllib.request.Request(url, method=method, data=None if body is None else json.dumps(body).encode(), headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "training-center-addon", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read() or b"{}")


def put_file(token, repo, path, text, message):
    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    sha = None
    try:
        sha = _gh("GET", url, token).get("sha")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
    body = {"message": message, "content": base64.b64encode(text.encode("utf-8")).decode()}
    if sha:
        body["sha"] = sha
    _gh("PUT", url, token, body)


def explain(ex):
    """Понятное описание сбоя без токена."""
    if isinstance(ex, urllib.error.HTTPError):
        hint = {401: "токен неверный или просрочен", 403: "у токена нет права записи в репозиторий",
                404: "репозиторий не найден или токен к нему не привязан"}.get(ex.code, "")
        return f"GitHub ответил {ex.code}" + (f": {hint}" if hint else "")
    return f"нет связи с GitHub: {ex}"


def push(text, today=None, token=None, repo=None):
    """-> (ok, сообщение). Пишет latest.md и reports/ГГГГ-ММ-ДД.md."""
    token = token or get_token()
    repo = repo or get_repo()
    if not token:
        return False, "токен GitHub не задан (настройка аддона github_token). Отчёт можно скопировать вручную."
    d = (today or timeutil.today()).isoformat()
    try:
        put_file(token, repo, "latest.md", text, f"Отчёт {d}")
        put_file(token, repo, f"reports/{d}.md", text, f"Отчёт {d}")
    except Exception as ex:
        msg = explain(ex).replace(token, "***")
        stg.set_value("report_status", {"at": timeutil.now(), "ok": False, "text": msg})
        return False, msg
    msg = f"записано в {repo}: latest.md и reports/{d}.md"
    stg.set_value("report_status", {"at": timeutil.now(), "ok": True, "text": msg})
    return True, msg


def auto_after_sync(con, div=1000.0):
    """Если включён авторежим и есть новая силовая/бег после последнего отчёта — формирует и отправляет."""
    if not stg.get("report_auto", False):
        return None
    last = con.execute("SELECT MAX(id) AS i FROM activity WHERE type_key IN "
                       "('strength_training','running','treadmill_running','trail_running')").fetchone()["i"]
    if last is None or last == stg.get("report_last_act"):
        return None
    ok, msg = push(build(con, div=div))
    if ok:
        stg.set_value("report_last_act", last)
    return ok, msg
