"""Страница статуса (ingress). Только стандартная библиотека. Все ссылки относительные.

Страница — одна, с пятью вкладками (Сегодня, Неделя, Программа, Тело, История). Все вкладки собираются
сервером сразу, переключает их небольшой скрипт по адресу страницы (#today, #prog, …). Разделы внутри вкладок —
раскрывающиеся карточки. Оформление лежит в static/style.css, поведение — в static/app.js (читаются при запуске).
"""
import html, threading
from datetime import datetime, timedelta
from pathlib import Path
from .blocks import group_blocks
from .exercise_names import ru, overrides_status
from . import traffic, plan, program, progress, sync, timeutil, report, body, garmin_workout as gw, settings as stg
import json as _json
from urllib.parse import parse_qs, urlparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_STATIC = Path(__file__).parent / "static"


def _asset(name):
    try:
        return (_STATIC / name).read_text(encoding="utf-8")
    except Exception:
        return ""


STYLE = _asset("style.css")
SCRIPT = _asset("app.js")

TABS = [("today", "🚦", "Сегодня"), ("week", "📅", "Неделя"), ("prog", "📋", "Программа"),
        ("body", "⚖️", "Тело"), ("hist", "🗂", "История")]
# после какого действия на какую вкладку возвращаемся
TAB_OF = {"settings": "week", "draft": "prog", "program": "prog", "garmin_send": "prog", "push_toggle": "prog",
          "enrich": "prog", "rollback": "prog", "report_auto": "hist", "report": "hist", "weight": "body",
          "weight_del": "body", "measure": "body", "measure_del": "body", "run_feel": "today"}
THEMES = ["auto", "light", "dark"]
THEME_NAME = {"auto": "Авто", "light": "Светлая", "dark": "Тёмная"}
STALE_HOURS = 6   # синхронизация старше — точка становится жёлтой


def num(v):
    """Число для показа по-русски: 2,5 · 95 · 4,5."""
    return f"{v:g}".replace(".", ",")


def plural(n, forms):
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return forms[0]
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return forms[1]
    return forms[2]


def acc(key, title, sub="", content="", open_=False, force=False, cls=""):
    """Раскрывающаяся карточка. key — ключ для запоминания открыто/закрыто в браузере; force — не трогать
    серверное состояние (например, когда только что получен результат действия)."""
    e = html.escape
    s = f"<small>{e(sub)}</small>" if sub else ""
    fa = " data-force=1" if force else ""
    o = " open" if (open_ or force) else ""
    return (f"<details class='card {cls}' data-k='{key}'{fa}{o}><summary><b>{e(title)}</b>{s}</summary>"
            f"<div class=body>{content}</div></details>")


def fmt_set(x):
    kg = "свой вес" if not x["kg"] else f"{x['kg']:g} кг"
    return f"{kg} × {x['reps']}"


def block_html(b):
    e = html.escape
    names = []
    for x in b["items"]:
        if x["exercise"] not in names:
            names.append(x["exercise"])
    lines = "".join(f"<div><b>{e(ru(n))}</b>: " + ", ".join(fmt_set(x) for x in b["items"] if x["exercise"] == n) + "</div>" for n in names)
    if b["kind"] == "superset":
        rounds = max(sum(1 for x in b["items"] if x["exercise"] == n) for n in names)
        return f"<div class=ss><span class=tag>Суперсет · {rounds} кр.</span>{lines}</div>"
    return f"<div class=ex>{lines}</div>"


TYPES = {"running": "Бег", "strength_training": "Силовая", "walking": "Ходьба", "cycling": "Велосипед",
         "hiking": "Поход", "treadmill_running": "Бег (дорожка)", "indoor_cardio": "Кардио"}


def activities_html(con):
    e = html.escape
    rows = con.execute("SELECT * FROM activity ORDER BY start_local DESC LIMIT 15").fetchall()
    h = ["<table><tr><th>Когда</th><th>Тип</th><th>Название</th><th>Дистанция</th><th>Время</th><th>Пульс</th></tr>"]
    for r in rows:
        j = _json.loads(r["json"] or "{}")
        km = (j.get("distance") or 0) / 1000
        sec = int(j.get("duration") or 0)
        hr = j.get("averageHR")
        h.append(f"<tr><td>{e(r['start_local'] or '')}</td><td>{e(TYPES.get(r['type_key'], r['type_key'] or ''))}</td>"
                 f"<td>{e(r['name'] or '')}</td><td>{f'{km:.2f} км' if km else ''}</td>"
                 f"<td>{sec // 60}:{sec % 60:02d}</td><td>{'' if hr is None else round(hr)}</td></tr>")
    h.append("</table>")
    return acc("hist-acts", "Активности", "последние 15", "".join(h))


DAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
TL_NAME = {"green": "Зелёный", "yellow": "Жёлтый", "red": "Красный"}


# ---------- последняя синхронизация ----------

def _parse_local(s):
    return datetime.strptime(s, timeutil.FMT).replace(tzinfo=timeutil.tz())


def _ago(sec):
    m = int(sec // 60)
    if m < 1:
        return "только что"
    if m < 60:
        return f"{m} мин назад"
    h = m // 60
    if h < 6:
        return f"{h} ч {m % 60:02d} мин назад"
    if h < 24:
        return f"{h} ч назад"
    return f"{h // 24} дн назад"


def _when(dt, now):
    d = dt.date()
    if d == now.date():
        day = "сегодня"
    elif d == now.date() - timedelta(days=1):
        day = "вчера"
    else:
        day = dt.strftime("%d.%m")
    return f"{day} {dt.strftime('%H:%M')}"


def sync_line(con, now=None):
    """-> (цвет точки, текст). Нужна утром: видно, свежие ли данные у светофора."""
    now = now or timeutil.now_dt()
    last = con.execute("SELECT * FROM sync_run WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
    run = con.execute("SELECT * FROM sync_run WHERE finished_at IS NULL ORDER BY id DESC LIMIT 1").fetchone()
    running = ""
    try:
        if run and (now - _parse_local(run["started_at"])).total_seconds() < 20 * 60:
            running = "идёт сейчас · "
    except Exception:
        pass
    if not last:
        return "grey", running + "Синхронизаций ещё не было"
    try:
        dt = _parse_local(last["finished_at"])
    except Exception:
        return "grey", running + "Время последней синхронизации неизвестно"
    age = (now - dt).total_seconds()
    when = f"{_when(dt, now)} · {_ago(age)}"
    if last["ok"]:
        if age > STALE_HOURS * 3600:
            return "yellow", f"{running}Синхронизация: {when} · данные могут быть устаревшими"
        return "green", f"{running}Синхронизация: {when} · ок"
    okrow = con.execute("SELECT finished_at FROM sync_run WHERE ok=1 ORDER BY id DESC LIMIT 1").fetchone()
    tail = ""
    try:
        if okrow:
            tail = f" Последняя успешная: {_when(_parse_local(okrow['finished_at']), now)}."
    except Exception:
        pass
    return "red", f"{running}Синхронизация не удалась: {when}.{tail}"


# ---------- вкладка «Сегодня» ----------

def traffic_html(con):
    e = html.escape
    today = timeutil.today()
    d = today.isoformat()
    m = traffic.metrics(con, d)
    if all(m[k] is None for k in ("readiness", "sleep_h", "hrv", "rhr", "bb")):
        return [f"<div class='card tlcard grey'><div class=tl><div class=lamp></div><div><div class=cap>Светофор</div>"
                f"<h3>Данных пока нет</h3></div></div><div class=more><p>За {d} данных пока нет. Нажми «Синхронизировать с Garmin».</p></div></div>"]
    wait = traffic.missing_morning(m)
    if wait:
        return [f"<div class='card tlcard grey'><div class=tl><div class=lamp></div><div><div class=cap>Светофор на {d}</div>"
                f"<h3>Утренних данных ещё нет</h3></div></div><div class=more>"
                f"<p>Garmin пока не обработал: {e(', '.join(wait))}. Цвет без них не показываю, чтобы не ошибиться.</p>"
                "<p><small>Открой приложение Garmin Connect на телефоне, дождись синхронизации часов и нажми «Синхронизировать с Garmin». "
                "Утром аддон также сам повторяет синхронизацию каждые 15 минут, пока данные не появятся.</small></p></div></div>"]
    color, why, notes = traffic.decide(m)
    if color == "green":
        why = ["Все условия зелёного выполнены"]
    f = lambda v, u="": "—" if v is None else f"{v}{u}"
    chip = lambda c, t="": f"<span class=chip{f' title={chr(39)}{e(t)}{chr(39)}' if t else ''}>{e(c)}</span>"
    cap = "Начало дня" + (f" · {m['readiness_at']}" if m["readiness_at"] else "")
    morning = [chip(f"Readiness {f(m['readiness'])}"), chip(f"Body Battery {f(m['bb'])}"),
               chip(f"Сон {f(m['sleep_h'], ' ч')}", f"Sleep Score {f(m['sleep_score'])}"),
               chip(f"HRV {f(m['hrv'])}"), chip(f"Пульс покоя {f(m['rhr'])}")]
    rows = [f"<div class=chips><span class=rowcap>{e(cap)}</span>{''.join(morning)}</div>"]
    rd_diff = m["readiness_now"] not in (None, m["readiness"])
    bb_diff = m["bb_now"] not in (None, m["bb"])
    if rd_diff or bb_diff:
        now_chips = []
        if m["readiness_now"] is not None:
            now_chips.append(chip(f"Readiness {m['readiness_now']}", f"обновление Garmin в {m['readiness_now_at']}" if m["readiness_now_at"] else ""))
        if m["bb_now"] is not None:
            now_chips.append(chip(f"Body Battery {m['bb_now']}", "на последней синхронизации"))
        rows.append(f"<div class='chips rownow'><span class=rowcap>Сейчас</span>{''.join(now_chips)}</div>")
    more = [f"<p><b>План:</b> {e(traffic.PLANS[color])}</p>"]
    if notes:
        more.append(f"<p><small>{e('; '.join(notes))}</small></p>")
    if m["bb_note"]:
        more.append(f"<p><small>Body Battery в начале дня:{e(m['bb_note'])}, значения на пробуждении нет.</small></p>")
    life = {}
    row = con.execute("SELECT json FROM daily_raw WHERE date=? AND kind='lifestyle'", ((today - timedelta(days=1)).isoformat(),)).fetchone()
    if row:
        from .lifestyle import parse_day
        try:
            life = parse_day(_json.loads(row["json"]))
        except Exception:
            life = {}
    if life:
        names = {"alcohol": "алкоголь", "hookah": "кальян", "illness": "болезнь"}
        more.append(f"<p><small>Вчера отмечено: {e(', '.join(names[k] for k in life))}. Сегодня бег только лёгкий, без интервалов.</small></p>")
    return [f"<div class='card tlcard {color}'><div class=tl><div class=lamp></div><div><div class=cap>Светофор на {d}</div>"
            f"<h3>{TL_NAME[color]}</h3><p>{e('; '.join(why))}</p></div></div>{''.join(rows)}"
            f"<div class=more>{''.join(more)}</div></div>"]


def banners_html(con):
    """Напоминания о весе и замерах — на вкладке «Сегодня»."""
    e = html.escape
    today = timeutil.today()
    h = []
    wdue, wlast, wpassed, wiv = body.weight_status(con, today)
    if wdue:
        wwhen = "записей веса ещё нет" if wlast is None else f"последняя запись {e(wlast)}, прошло {wpassed} дн."
        h.append(f"<div class='card warn banner'><span><b>Пора взвеситься</b> ({wwhen}; напоминание, если нет записи {wiv} дн.)</span>"
                 "<a class='btn sec' href='#body'>Открыть «Тело»</a></div>")
    due, last, passed, iv = body.measure_status(con, today)
    if due:
        when = "ещё ни разу не делали" if last is None else f"последние {e(last)}, прошло {passed} дн."
        h.append(f"<div class='card warn banner'><span><b>Пора делать замеры лентой</b> ({when}; интервал {iv} дн.)</span>"
                 "<a class='btn sec' href='#body'>Открыть «Тело»</a></div>")
    return h


def feel_html(con, msg=None):
    """Вопрос «как ощущался бег» по пробежкам без ответа."""
    e = html.escape
    rows = body.pending_feel(con)
    if not rows:
        return []
    h = []
    if msg:
        h.append(f"<div class={'note' if msg[0] else 'err'}>{e(msg[1])}</div>")
    for r in rows:
        opts = "".join(f"<label><input type=radio name=score value={k}> {k} — {e(v)}</label> " for k, v in body.FEEL.items())
        h.append(f"<form method=post action=run_feel class=warn><input type=hidden name=id value={r['id']}>"
                 f"<b>{e((r['start_local'] or '')[:16])} · {e(r['name'] or 'Бег')}</b><br>{opts}<br>"
                 "<input name=note placeholder='комментарий (необязательно)' maxlength=200 style='width:60%'> "
                 "<button>Сохранить</button></form>")
    return [acc("today-feel", "Как ощущался бег?", f"{len(rows)} без ответа", "".join(h), force=True)]


def current_slots(con):
    row, data = program.active(con)
    if data:
        return program.slots_of(data, stg.day_overrides())
    return stg.load()["slots"]


# ---------- вкладка «Неделя» ----------

SHORT = {"done": "выполнено", "moved": "перенос", "today": "сегодня", "planned": "впереди", "late": "просрочено", "missed": "пропущено"}
STCLS = {"done": "done", "moved": "done", "today": "today", "planned": "plan", "late": "late", "missed": "miss"}


def plan_table(con, slots, weeks):
    """-> (html, выполнено в текущей неделе, всего слотов). Статусы цветными метками, текущая неделя подсвечена."""
    e = html.escape
    cfg = {"slots": slots}
    today = timeutil.today()
    mon = plan.monday(today)
    dn = lambda d: "любой день" if d is None else "план: " + DAYS[d]
    h = ["<table class=w1><thead><tr><th>Неделя</th>" + "".join(f"<th>{e(s['label'])}<small>{dn(s['day'])}</small></th>" for s in slots)
         + "<th>Итог</th></tr></thead><tbody>"]
    done_now = 0
    for w in range(weeks):
        wm = mon - timedelta(days=7 * w)
        cells, ok = plan.week_status(con, cfg, wm, today)
        n_done = sum(1 for c in cells if c["status"] in ("done", "moved"))
        if w == 0:
            done_now = n_done
        rng = f"{wm.strftime('%d.%m')}–{(wm + timedelta(days=6)).strftime('%d.%m')}"
        h.append(f"<tr{' class=cur' if w == 0 else ''}><td>{'<b>' + rng + '</b>' if w == 0 else rng}</td>")
        for c in cells:
            when = "" if c["date"] is None else f"<small>{c['date'].strftime('%d.%m')} {DAYS[c['date'].weekday()]}</small>"
            h.append(f"<td><span class='st {STCLS[c['status']]}' title='{e(plan.LABEL[c['status']])}'>{SHORT[c['status']]}</span>{when}</td>")
        if n_done == len(cells):
            res = "<span class='res ok'>выполнена</span>"
        elif w == 0:
            res = f"<span class='res go'>идёт · {n_done}/{len(cells)}</span>"
        else:
            res = "<span class='res no'>не выполнена</span>"
        h.append(f"<td>{res}</td></tr>")
    h.append("</tbody></table>")
    return "".join(h), done_now, len(slots)


def plan_today_html(con):
    t, done, total = plan_table(con, current_slots(con), 1)
    return [acc("today-plan", "Недельный план", f"{done} из {total} выполнено", t)]


def plan_week_html(con):
    e = html.escape
    slots = current_slots(con)
    t, done, total = plan_table(con, slots, 4)
    f = ["<form method=post action=settings>"]
    for s in slots:
        opts = f"<option value=any{' selected' if s['day'] is None else ''}>любой день</option>" + "".join(
            f"<option value={i}{' selected' if i == s['day'] else ''}>{n}</option>" for i, n in enumerate(DAYS))
        f.append(f"<p>{e(s['label'])}: <select name='day_{e(s['key'])}'>{opts}</select></p>")
    f.append("<button>Сохранить</button></form>")
    return [acc("week-plan", "Недельный план", "последние 4 недели", t, open_=True),
            acc("week-days", "Изменить дни тренировок", "", "".join(f))]


# ---------- вкладка «Программа» ----------

def gain_text(r):
    """Текст прибавки из progress_log с русским названием упражнения.
    В базе хранится код Garmin (его разбирает report.py), название подставляется только при показе,
    через ru() — то же, что в блоке «Программа», включая /config/exercise_names.json."""
    text = r["text"] or ""
    code = r["code"]
    return text.replace(code, ru(code), 1) if code and code in text else text


def strength_table(sess, step):
    """Таблица силовой: упражнение, веса по подходам (кг), диапазон повторов.
    Подход, которому по правилу волны достанется следующая прибавка, подсвечен стрелкой."""
    e = html.escape
    rows, legend = [], False
    for b in sess["blocks"]:
        sup = b["type"] == "superset"
        rounds = max(len(x["weights"]) for x in b["exercises"])
        for x in b["exercises"]:
            ws = x["weights"]
            eq = x.get("equipment") or program.equipment_of(x["code"], ws)
            bump = progress.next_bump(ws) if eq in step else None
            chips = []
            for i, w in enumerate(ws):
                label = "свой вес" if not w else num(w)
                if i == bump:
                    legend = True
                    tip = f"Следующая прибавка: {num(w)} → {num(w + step[eq])} кг, когда все подходы выполнены на верх диапазона"
                    chips.append(f"<span class='wchip up' title='{e(tip)}'>{label}</span>")
                else:
                    chips.append(f"<span class=wchip>{label}</span>")
            tag = f"<span class=tag>Суперсет · {rounds} кр.</span>" if sup else ""
            rows.append(f"<tr{' class=ssr' if sup else ''}><td>{tag}{e(ru(x['code']))}</td><td>{''.join(chips)}</td>"
                        f"<td><span class=reps>{x['reps'][0]}–{x['reps'][1]}</span></td></tr>")
    note = ("<p class=muted><small>↑ — подход, которому добавится вес, когда все подходы выполнены на верх диапазона повторов. "
            "Вес в килограммах.</small></p>") if legend else ""
    return ("<table class=prog><thead><tr><th>Упражнение</th><th>Вес, кг</th><th>Повторы</th></tr></thead><tbody>"
            + "".join(rows) + "</tbody></table>" + note)


def run_parts(s):
    """Параметры бега из программы: (css-класс, нагрузка, зона, минуты, км); что не задано — None."""
    lv = program.run_level(s.get("level"))
    return (lv[0] if lv else None, lv[1] if lv else None, s.get("zone"), s.get("duration_min"), s.get("distance_km"))


def run_summary(s):
    cls, label, zone, mins, km = run_parts(s)
    parts = [x for x in (label, f"Зона {zone}" if zone else None, f"{num(mins)} мин" if mins else None, f"{num(km)} км" if km else None) if x]
    return " · ".join(parts) if parts else "без параметров"


def run_table(s):
    """Таблица бега: нагрузка, пульсовая зона Garmin, время, расстояние. Нет значения — прочерк."""
    cls, label, zone, mins, km = run_parts(s)
    dash = "<span class=dash>—</span>"
    lvl = f"<span class='dot {cls}'></span>{label}" if label else dash
    zn = ("<span style='white-space:nowrap'><span class=zbar>" + "".join(f"<i class='{'on ' if k <= zone else ''}z{k}'></i>" for k in range(1, 6)) + f"</span>Зона {zone}</span>") if zone else dash
    body_ = (f"<tr><td>{lvl}</td><td>{zn}</td><td>{f'{num(mins)} мин' if mins else dash}</td><td>{f'{num(km)} км' if km else dash}</td></tr>")
    hint = ("<p class=muted><small>Параметры бега задаются в редакторе программы (YAML): level — лёгкий, обычный или интенсивный; "
            "zone — зона Garmin 1–5; duration_min — минуты; distance_km — километры. Незаданное остаётся прочерком.</small></p>"
            if not (label or zone or mins or km) else "")
    return ("<table class=prog><thead><tr><th>Нагрузка</th><th>Пульсовая зона</th><th>Время</th><th>Расстояние</th></tr></thead><tbody>"
            + body_ + "</tbody></table>" + hint)


def week_program_table(con, data):
    """Недельная программа одним взглядом: день, тренировка, состав, когда отправлена в Garmin."""
    e = html.escape
    slots = {s["key"]: s for s in current_slots(con)}
    rows = []
    for s in data["sessions"]:
        day = (slots.get(s["key"]) or {}).get("day", s.get("day"))
        if s["kind"] == "strength":
            n_ex = sum(len(b["exercises"]) for b in s["blocks"])
            n_ss = sum(1 for b in s["blocks"] if b["type"] == "superset")
            comp = f"{n_ex} {plural(n_ex, ('упражнение', 'упражнения', 'упражнений'))}"
            if n_ss:
                comp += f", {n_ss} {plural(n_ss, ('суперсет', 'суперсета', 'суперсетов'))}"
            sent = con.execute("SELECT updated_at FROM garmin_workout WHERE session_key=?", (s["key"],)).fetchone()
            g = f"{sent['updated_at'][8:10]}.{sent['updated_at'][5:7]}" if sent else "<span class=dash>—</span>"
        else:
            comp, g = run_summary(s), "<span class=dash>—</span>"
        rows.append(f"<tr><td>{'Любой' if day is None else DAYS[day]}</td><td><b>{e(s['name'])}</b></td><td>{e(comp)}</td><td>{g}</td></tr>")
    return ("<table class=prog><thead><tr><th>День</th><th>Тренировка</th><th>Состав</th><th>В Garmin</th></tr></thead><tbody>"
            + "".join(rows) + "</tbody></table>")


def program_html(con, notice=None, editor_text=None):
    e = html.escape
    row, data = program.active(con)
    draft = con.execute("SELECT * FROM program_version WHERE kind='черновик' ORDER BY id DESC LIMIT 1").fetchone()
    h = []
    if notice:
        h.append(f"<div class=err>{notice}</div>")
    if data:
        step = data["progression"]["step_kg"]
        h.append(f"<p class=muted>Активная версия №{row['id']} ({e(row['kind'])}, {e(row['created_at'])}). Прибавка: штанга +{step['barbell']:g} кг, "
                 f"гантели +{step['dumbbell']:g} кг. Тренировки учитываются с {e(str(data['evaluate_from']))}.</p>")
        names = " · ".join(s["name"] for s in data["sessions"])
        h.append(acc("prog-week", "Недельная программа", names, week_program_table(con, data), open_=True))
        h.append("<div class=grp>Тренировки</div>")
        first = True
        for s in data["sessions"]:
            if s["kind"] != "strength":
                h.append(acc("prog-run-" + s["key"], s["name"], run_summary(s), run_table(s)))
                continue
            sent = con.execute("SELECT * FROM garmin_workout WHERE session_key=?", (s["key"],)).fetchone()
            sent_txt = f" Отправлено в Garmin: «{e(sent['name'])}», {e(sent['updated_at'])}." if sent else " В Garmin ещё не отправлялась."
            sub = f"в Garmin: {sent['updated_at'][8:10]}.{sent['updated_at'][5:7]}" if sent else "в Garmin не отправлялась"
            c = [f"<form method=post action=garmin_send><input type=hidden name=key value='{e(s['key'])}'>"
                 f"<button class=sec>Отправить в Garmin</button> <small>{sent_txt}</small></form>", strength_table(s, step)]
            h.append(acc("prog-s-" + s["key"], s["name"], sub, "".join(c), open_=first))
            first = False
        log = con.execute("SELECT * FROM progress_log WHERE code!='*' ORDER BY at DESC LIMIT 6").fetchall()
        if log:
            h.append(acc("prog-gain", "Предлагаемые изменения веса", f"последних: {len(log)}",
                         "<ul>" + "".join(f"<li>{e(r['at'][:10])}: {e(gain_text(r))}</li>" for r in log) + "</ul>"))
    else:
        h.append("<p>Программы пока нет. Создай черновик из последних тренировок Garmin «Full Body A» и «Full Body B».</p>")
    # ---- Управление тренировками: отправка на часы, редактор, версии, черновик
    m = []
    on = bool(stg.get("push_to_device", False))
    c = ["<p><small>Если тренировка уже отправлялась, после каждой синхронизации система сверяет её с программой "
         "и сама обновляет в Garmin при расхождении (например, после прибавки веса).</small></p>"
         f"<form method=post action=push_toggle><input type=hidden name=on value={0 if on else 1}>"
         f"<button class=sec>{'Выключить' if on else 'Включить'} автоматическую отправку на часы</button> "
         f"<small>сейчас: {'включена' if on else 'выключена'}</small></form>"]
    plog = con.execute("SELECT * FROM push_log ORDER BY id DESC LIMIT 6").fetchall()
    if plog:
        c.append("<table><tr><th>Когда</th><th>Тренировка</th><th>Повод</th><th>Итог</th></tr>" + "".join(
            f"<tr><td>{e(r['at'])}</td><td>{e(r['session_key'])}</td><td>{e(r['why'] or '')}</td>"
            f"<td>{'<span class=ok>ок</span>' if r['ok'] else '<span class=bad>сбой</span>'} {e(r['text'] or '')}</td></tr>" for r in plog) + "</table>")
    m.append(acc("prog-push", "Отправка на часы", "включена" if on else "выключена", "".join(c)))
    text = editor_text
    if text is None:
        text = draft["yaml"] if draft and (not row or draft["id"] > row["id"]) else (row["yaml"] if row else "")
    forced = False
    if text:
        forced = editor_text is not None or bool(draft and (not row or draft['id'] > row['id']))
        m.append(acc("prog-yaml", "Редактор программы (YAML)", "",
                     f"<form method=post action=program><textarea name=yaml rows=30>{e(text)}</textarea><br>"
                     "<button>Проверить и сохранить новой версией</button></form>"
                     "<p><small>Вес в Garmin уходит по верхней границе диапазона повторов; диапазон хранится здесь как справка и для прибавки веса. "
                     "weights — вес по подходам (кг), 0 — свой вес. day: 0 (пн) … 6 (вс) или без day — любой день. "
                     "Для бега (kind: run) необязательно: level — лёгкий/обычный/интенсивный, zone — зона Garmin 1–5, "
                     "duration_min — минуты, distance_km — километры.</small></p>", force=forced))
    vers = con.execute("SELECT * FROM program_version ORDER BY id DESC LIMIT 8").fetchall()
    if vers:
        t = ["<table><tr><th>№</th><th>Когда</th><th>Вид</th><th>Что изменилось</th><th></th></tr>"]
        for v in vers:
            btn = "активна" if v["active"] else f"<form method=post action=rollback><input type=hidden name=id value={v['id']}><button class=sec>Откатить</button></form>"
            t.append(f"<tr><td>{v['id']}</td><td>{e(v['created_at'])}</td><td>{e(v['kind'])}</td><td>{e((v['note'] or '')[:160])}</td><td>{btn}</td></tr>")
        t.append("</table>")
        m.append(acc("prog-versions", "Версии программы", f"последних: {len(vers)}", "".join(t)))
    drafts = "<form method=post action=draft><button class=sec>Создать черновик из Garmin</button></form>"
    if data:
        drafts += "<form method=post action=enrich><button class=sec>Дополнить программу из Garmin (отдых и категории)</button></form>"
    m.append(acc("prog-draft", "Черновик и дополнение из Garmin", "", drafts, open_=not data))
    h.append(acc("prog-manage", "Управление тренировками", "отправка на часы, редактор, версии", "".join(m), open_=not data, force=forced))
    return h


# ---------- вкладка «Тело» ----------

def body_html(con, msg=None):
    """Ввод веса и замеров лентой. msg — (ok, текст) после неудачной отправки формы."""
    e = html.escape
    today = timeutil.today()
    h = []
    if msg:
        h.append(f"<div class={'note' if msg[0] else 'err'}>{e(msg[1])}</div>")
    wdue, wlast, wpassed, wiv = body.weight_status(con, today)
    due, last, passed, iv = body.measure_status(con, today)
    now = timeutil.now()[:16].replace(" ", "T")
    fields = "".join(f"<label>{e(lbl)}<input name={k} inputmode=decimal{' required' if req else ''}></label>"
                     for k, (lbl, lo, hi, req) in body.WEIGHT_FIELDS.items())
    w = ["<form method=post action=weight class=f>"
         f"<label>Дата и время<input type=datetime-local name=at value='{now}' style='width:230px'></label>{fields}"
         "<label>Заметка<input name=note maxlength=200 style='width:180px'></label><button>Записать вес</button></form>"
         "<p><small>Утром, натощак, после туалета, в одинаковых условиях. Жир и вода — с весов (биоимпеданс), их можно не заполнять. "
         "Жировая и безжировая масса считаются сами.</small></p>"]
    rows = con.execute("SELECT * FROM body_weight ORDER BY at DESC, id DESC LIMIT 8").fetchall()
    if rows:
        w.append("<table><tr><th>Когда</th><th>Вес, кг</th><th>Жир, %</th><th>Вода, %</th><th>Заметка</th><th></th></tr>")
        v = lambda x: "" if x is None else f"{x:g}"
        for r in rows:
            w.append(f"<tr><td>{e(r['at'][:16])}</td><td>{v(r['weight_kg'])}</td><td>{v(r['fat_pct'])}</td><td>{v(r['water_pct'])}</td>"
                     f"<td>{e(r['note'] or '')}</td>"
                     f"<td><form method=post action=weight_del><input type=hidden name=id value={r['id']}><button class=sec>Удалить</button></form></td></tr>")
        w.append("</table>")
    wsub = ("записей ещё нет" if not rows else f"последний: {rows[0]['weight_kg']:g} кг, {rows[0]['at'][:10]}") + (" · пора взвеситься" if wdue else "")
    h.append(acc("body-weight", "Взвешивание", wsub, "".join(w), open_=True))
    mf = "".join(f"<label title='{e(how)}'>{e(lbl)}<input name={k} inputmode=decimal></label>" for k, (lbl, how) in body.MEASURES.items())
    m = ["<form method=post action=measure class=f>"
         f"<label>Дата<input type=date name=day value='{today.isoformat()}' style='width:160px'></label>{mf}"
         "<button>Записать замеры</button></form>"]
    m.append("<details><summary>Как мерить</summary><ul>" + "".join(f"<li><b>{e(lbl)}</b>: {e(how)}</li>" for lbl, how in body.MEASURES.values()) +
             "</ul><p><small>Утром, натощак, после туалета, расслабленно, на выдохе. Лента плотно прилегает, но не врезается, параллельно полу. "
             "Каждый замер — дважды, записывай среднее. Мерь всегда в тех же точках и (для рук и ног) с той же стороны. "
             "Достаточно заполнить часть полей; повторная запись за ту же дату дополняет и обновляет значения. "
             "Полные замеры раз в 4 недели, талию можно мерить чаще (раз в неделю).</small></p></details>")
    ms = con.execute("SELECT * FROM body_measure ORDER BY date DESC LIMIT 4").fetchall()
    if ms:
        m.append("<table><tr><th>Замер, см</th>" + "".join(f"<th>{e(r['date'])}</th>" for r in ms) + "</tr>")
        for k, (lbl, _) in body.MEASURES.items():
            if all(r[k] is None for r in ms):
                continue
            m.append(f"<tr><td>{e(lbl)}</td>" + "".join(f"<td>{'' if r[k] is None else f'{r[k]:g}'}</td>" for r in ms) + "</tr>")
        m.append("<tr><td></td>" + "".join(
            f"<td><form method=post action=measure_del><input type=hidden name=day value={e(r['date'])}><button class=sec>Удалить</button></form></td>" for r in ms) + "</tr></table>")
    if due:
        msub = "ещё ни разу не делали" if last is None else f"пора: прошло {passed} дн."
    else:
        msub = f"последние {last}, следующие через {iv - passed} дн."
    h.append(acc("body-measure", "Замеры лентой", msub, "".join(m)))
    return h


# ---------- вкладка «История» ----------

def report_html(con, cfg, text=None, notice=None):
    e = html.escape
    st = stg.get("report_status") or {}
    auto = bool(stg.get("report_auto", False))
    c = []
    if notice:
        c.append(f"<div class=err>{notice}</div>")
    if st:
        c.append(f"<p>Последняя отправка: {e(st.get('at', ''))} — {'<span class=ok>ок</span>' if st.get('ok') else '<span class=bad>сбой</span>'} {e(st.get('text', ''))}</p>")
    c.append("<form method=post action=report><select name=days><option value=7>7 дней</option><option value=14>14 дней</option>"
             "<option value=30>30 дней</option></select> <button name=do value=make>Сформировать</button> "
             "<button class=sec name=do value=push>Отправить в GitHub</button></form>")
    c.append(f"<form method=post action=report_auto><input type=hidden name=on value={0 if auto else 1}>"
             f"<button class=sec>{'Выключить' if auto else 'Включить'} автоотчёт после новой тренировки</button> <small>сейчас: {'включён' if auto else 'выключен'}</small></form>")
    if not report.get_token():
        c.append("<p><small>Токен GitHub не задан: отправка недоступна, отчёт можно скопировать вручную.</small></p>")
    if text:
        c.append("<details open><summary>Текст отчёта (выдели всё и скопируй)</summary>"
                 f"<textarea readonly rows=25 onclick='this.select()'>{e(text)}</textarea></details>")
    sub = ("последняя отправка: " + st.get("at", "")[:16] + (" · ок" if st.get("ok") else " · сбой")) if st else "ещё не отправлялся"
    return [acc("hist-report", "Отчёт для ИИ", sub, "".join(c), open_=True, force=bool(text or notice))]


def sync_log_html(con):
    e = html.escape
    runs = con.execute("SELECT * FROM sync_run ORDER BY id DESC LIMIT 8").fetchall()
    t = ["<table><tr><th>Начало</th><th>Запуск</th><th>Итог</th><th>Дней</th><th>Тренировок</th><th>Подходов</th><th>Ошибки</th></tr>"]
    for r in runs:
        st = "идёт" if r["finished_at"] is None else ('<span class=ok>ок</span>' if r["ok"] else '<span class=bad>сбой</span>')
        t.append(f"<tr><td>{e(r['started_at'] or '')}</td><td>{e(r['trigger'] or '')}</td><td>{st}</td><td>{r['days'] or ''}</td>"
                 f"<td>{r['activities'] or ''}</td><td>{r['sets'] or ''}</td><td><pre>{e((r['errors'] or '')[:600])}</pre></td></tr>")
    if not runs:
        t.append("<tr><td colspan=7>Синхронизаций ещё не было</td></tr>")
    t.append("</table>")
    sub = ""
    if runs:
        r = runs[0]
        sub = f"последняя: {(r['started_at'] or '')[:16]} · " + ("идёт" if r["finished_at"] is None else ("ок" if r["ok"] else "сбой"))
    return [acc("hist-sync", "Журнал синхронизаций", sub, "".join(t))]


def strength_html(con, cfg):
    e = html.escape
    div = cfg["weight_divisor"]
    acts = con.execute("SELECT * FROM activity WHERE type_key='strength_training' ORDER BY start_local DESC LIMIT 3").fetchall()
    c = []
    for a in acts:
        c.append(f"<h3>{e(a['start_local'] or '')} — {e(a['name'] or '')}</h3>")
        sets = [dict(exercise=r["exercise"], reps=r["reps"], kg=(r["weight_raw"] or 0) / div)
                for r in con.execute("SELECT * FROM workout_set WHERE activity_id=? AND set_type='ACTIVE' ORDER BY idx", (a["id"],))]
        for b in group_blocks(sets):
            c.append(block_html(b))
    c.append(f"<p><small>Вес = значение Garmin / {div}. Суперсет определяется по чередованию упражнений. Знак ⁇ — названия нет в словаре. Свои названия: {e(overrides_status())}.</small></p>")
    return [acc("hist-strength", "Последние силовые тренировки", f"показано: {len(acts)}", "".join(c))]


# ---------- сборка страницы ----------

def theme_form(cls=""):
    cur = stg.get("theme", "auto")
    cur = cur if cur in THEMES else "auto"
    nxt = THEMES[(THEMES.index(cur) + 1) % len(THEMES)]
    return (f"<form method=post action=theme class='theme-form {cls}'><input type=hidden name=v value={nxt}>"
            f"<button class=theme title='Переключить тему: авто, светлая, тёмная'><span class=pre>Тема: </span><span class=lbl>{THEME_NAME[cur]}</span></button></form>")


def page(con, cfg, notice=None, editor_text=None, report_text=None, body_msg=None, report_notice=None, feel_msg=None, tab=None):
    e = html.escape
    theme = stg.get("theme", "auto")
    theme = theme if theme in THEMES else "auto"
    if tab is None:
        tab = ("prog" if (notice or editor_text is not None) else "hist" if (report_text or report_notice)
               else "body" if body_msg else "today")
    cls, line = sync_line(con)
    today = timeutil.today()
    today_pane = [f"<div class=top><div><h2>Сегодня, {today.strftime('%d.%m')}</h2>"
                  f"<div class='sync {cls}'><span class=dot></span>{e(line)}</div></div>"
                  f"<div class=actions><form method=post action=sync><button>Синхронизировать с Garmin</button></form>{theme_form('m')}</div></div>"]
    today_pane += traffic_html(con) + banners_html(con) + feel_html(con, feel_msg) + plan_today_html(con)
    week_pane = ["<div class=top><h2>Неделя</h2></div>"] + plan_week_html(con)
    prog_pane = ["<div class=top><h2>Программа</h2></div>"] + program_html(con, notice, editor_text)
    body_pane = ["<div class=top><h2>Вес и замеры</h2></div>"] + body_html(con, body_msg)
    hist_pane = ["<div class=top><h2>История и отчёт</h2></div>"] + report_html(con, cfg, report_text, report_notice) \
        + sync_log_html(con) + [activities_html(con)] + strength_html(con, cfg)
    panes = [("today", today_pane), ("week", week_pane), ("prog", prog_pane), ("body", body_pane), ("hist", hist_pane)]
    nav = "".join(f"<a class='t' href='#{k}' data-t='{k}'><span>{ic}</span>{e(name)}</a>" for k, ic, name in TABS)
    main = "".join(f"<section class=tabpane id={k}>{''.join(parts)}</section>" for k, parts in panes)
    return (f"<!doctype html><html lang=ru data-theme={theme}><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'><title>Тренировочный центр</title>"
            f"<style>{STYLE}</style><noscript><style>.tabpane{{display:block}}</style></noscript></head>"
            f"<body data-tab={tab}><div class=app><nav><h1>🏋 Тренировочный центр</h1>{nav}<div class=sp></div>{theme_form('d')}</nav>"
            f"<main>{main}</main></div><script>{SCRIPT}</script></body></html>")


def serve(con, cfg, start_sync, port=8099):
    class H(BaseHTTPRequestHandler):
        def _send(self, code, body=b"", loc=None):
            self.send_response(code)
            if loc:
                self.send_header("Location", loc)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            u = urlparse(self.path)
            if u.path.rstrip("/").endswith("raw"):
                d = (parse_qs(u.query).get("date") or [timeutil.today().isoformat()])[0]
                rows = {r["kind"]: _json.loads(r["json"]) for r in con.execute("SELECT kind,json FROM daily_raw WHERE date=?", (d,))}
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(_json.dumps(rows, ensure_ascii=False, indent=1).encode())
                return
            self._send(200, page(con, cfg).encode())

        def do_POST(self):
            path = urlparse(self.path).path.rstrip("/")
            n = int(self.headers.get("Content-Length") or 0)
            form = parse_qs(self.rfile.read(n).decode()) if n else {}
            tab = next((t for k, t in TAB_OF.items() if path.endswith(k)), "today")
            if path.endswith("theme"):
                v = (form.get("v") or [""])[0]
                if v in THEMES:
                    stg.set_value("theme", v)
                if self.headers.get("X-Fetch"):
                    return self._send(204)
                tab = "today"
            elif path.endswith("settings"):
                days = dict(stg.day_overrides())
                for sl in current_slots(con):
                    v = (form.get("day_" + sl["key"]) or [None])[0]
                    if v == "any":
                        days[sl["key"]] = None
                    elif v is not None and v.isdigit() and 0 <= int(v) <= 6:
                        days[sl["key"]] = int(v)
                stg.set_days(days)
            elif path.endswith("draft"):
                data, errs = program.build_draft(con, cfg["weight_divisor"])
                if errs:
                    return self._send(200, page(con, cfg, notice=html.escape(" ".join(errs))).encode())
                program.save_version(con, program.dump(data), "черновик", "из последних тренировок Garmin", False)
            elif path.endswith("program"):
                text = (form.get("yaml") or [""])[0]
                data, errs = program.parse(text)
                if errs:
                    msg = "<b>Не сохранено.</b><ul>" + "".join(f"<li>{html.escape(x)}</li>" for x in errs) + "</ul>"
                    return self._send(200, page(con, cfg, notice=msg, editor_text=text).encode())
                program.save_version(con, text, "ручная", "сохранено вручную", True)
            elif path.endswith("garmin_send"):
                key = (form.get("key") or [""])[0]
                row, data = program.active(con)
                sess = next((s for s in (data or {}).get("sessions", []) if s["key"] == key and s["kind"] == "strength"), None)
                if not sess:
                    return self._send(200, page(con, cfg, notice="Тренировка не найдена в активной программе.").encode())
                try:
                    api = (cfg.get("api_factory") or (lambda: sync.login(cfg["token_dir"])))()
                    dev = bool(stg.get("push_to_device", False))
                    wid, text = gw.push_session(con, api, sess, gw.category_map(con), "вручную", dev)
                    lines = "".join(f"<li>{html.escape(x)}</li>" for x in gw.summary(sess))
                    tail = "" if dev else " Отправь её на часы из Garmin Connect."
                    msg = (f"<b>В Garmin Connect: {html.escape(text)}.</b> «{html.escape(gw.garmin_name(sess))}».{tail}<ul>{lines}</ul>")
                except Exception as ex:
                    msg = f"<b>Не отправлено:</b> {html.escape(str(ex))}"
                return self._send(200, page(con, cfg, notice=msg).encode())
            elif path.endswith("push_toggle"):
                stg.set_value("push_to_device", (form.get("on") or [""])[0] == "1")
            elif path.endswith("enrich"):
                row, data = program.active(con)
                if data:
                    n = gw.enrich(con, data)
                    if n:
                        program.save_version(con, program.dump(data), "ручная", f"дополнено из Garmin (отдых, категории): {n} знач.", True)
            elif path.endswith("report_auto"):
                stg.set_value("report_auto", (form.get("on") or [""])[0] == "1")
            elif path.endswith("report"):
                days = int((form.get("days") or ["7"])[0]) if (form.get("days") or ["7"])[0].isdigit() else 7
                text = report.build(con, days=min(max(days, 1), 60), div=cfg["weight_divisor"])
                notice = None
                if (form.get("do") or [""])[0] == "push":
                    ok, msg = report.push(text)
                    notice = (f"<b>{'Отправлено' if ok else 'Не отправлено'}:</b> {html.escape(msg)}")
                return self._send(200, page(con, cfg, report_notice=notice, report_text=text, tab="hist").encode())
            elif path.endswith("weight"):
                f = {k: (v[0] if v else "") for k, v in form.items()}
                ok, text = body.add_weight(con, f.get("at"), f)
                if not ok:
                    return self._send(200, page(con, cfg, body_msg=(ok, text), tab="body").encode())
            elif path.endswith("weight_del"):
                v = (form.get("id") or [""])[0]
                if v.isdigit():
                    body.delete_weight(con, int(v))
            elif path.endswith("measure"):
                f = {k: (v[0] if v else "") for k, v in form.items()}
                ok, text = body.add_measures(con, f.get("day"), f)
                if not ok:
                    return self._send(200, page(con, cfg, body_msg=(ok, text), tab="body").encode())
            elif path.endswith("measure_del"):
                body.delete_measures(con, (form.get("day") or [""])[0])
            elif path.endswith("run_feel"):
                f = {k: (v[0] if v else "") for k, v in form.items()}
                ok, text = body.set_feel(con, f.get("id"), f.get("score"), f.get("note"))
                if not ok:
                    return self._send(200, page(con, cfg, feel_msg=(ok, text), tab="today").encode())
            elif path.endswith("rollback"):
                v = (form.get("id") or [""])[0]
                if v.isdigit():
                    program.rollback(con, int(v))
            else:
                threading.Thread(target=start_sync, args=("кнопка",), daemon=True).start()
                tab = "today"
            self._send(303, loc="./#" + tab)

        def log_message(self, *a):
            pass
    ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()
