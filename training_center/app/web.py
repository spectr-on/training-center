"""Страница статуса (ingress). Только стандартная библиотека. Все ссылки относительные."""
import html, threading
from .blocks import group_blocks
from .exercise_names import ru, overrides_status
from . import traffic, plan, program, sync, timeutil, garmin_workout as gw, settings as stg
from datetime import date, timedelta
import json as _json
from urllib.parse import parse_qs, urlparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STYLE = ("body{font-family:system-ui;max-width:900px;margin:16px auto;padding:0 16px}"
         "table{border-collapse:collapse;width:100%}td,th{border:1px solid #8884;padding:4px 8px;text-align:left}"
         ".ss{border-left:4px solid #e08a00;background:#e08a0018;padding:6px 10px;margin:8px 0}.ex{padding:4px 10px;margin:8px 0}.tag{display:inline-block;background:#e08a00;color:#fff;border-radius:4px;padding:0 6px;font-size:12px;margin-bottom:4px}.err{background:#c3333320;border-left:4px solid #c33;padding:8px 12px;margin:8px 0}.ok{color:#2a8a2a}.bad{color:#c33}button{padding:10px 18px;font-size:16px}")


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
    import json
    e = html.escape
    rows = con.execute("SELECT * FROM activity ORDER BY start_local DESC LIMIT 15").fetchall()
    h = ["<h2>Активности</h2><table><tr><th>Когда</th><th>Тип</th><th>Название</th><th>Дистанция</th><th>Время</th><th>Пульс</th></tr>"]
    for r in rows:
        j = json.loads(r["json"] or "{}")
        km = (j.get("distance") or 0) / 1000
        sec = int(j.get("duration") or 0)
        hr = j.get("averageHR")
        h.append(f"<tr><td>{e(r['start_local'] or '')}</td><td>{e(TYPES.get(r['type_key'], r['type_key'] or ''))}</td>"
                 f"<td>{e(r['name'] or '')}</td><td>{f'{km:.2f} км' if km else ''}</td>"
                 f"<td>{sec // 60}:{sec % 60:02d}</td><td>{'' if hr is None else round(hr)}</td></tr>")
    h.append("</table>")
    return h


DAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
TL_NAME = {"green": "🟢 Зелёный", "yellow": "🟡 Жёлтый", "red": "🔴 Красный"}


def traffic_html(con):
    e = html.escape
    today = timeutil.today()
    d = today.isoformat()
    m = traffic.metrics(con, d)
    if all(m[k] is None for k in ("readiness", "sleep_h", "hrv", "rhr", "bb")):
        return [f"<h2>Светофор</h2><p>За {d} данных пока нет. Нажми «Синхронизировать с Garmin».</p>"]
    wait = traffic.missing_morning(m)
    if wait:
        return [f"<h2>Светофор на {d}</h2><div class='tl grey'><b>⚪ Утренних данных ещё нет</b><br>"
                f"Garmin пока не обработал: {e(', '.join(wait))}. Цвет без них не показываю, чтобы не ошибиться.<br>"
                "<small>Открой приложение Garmin Connect на телефоне, дождись синхронизации часов и нажми «Синхронизировать с Garmin». "
                "Утром аддон также сам повторяет синхронизацию каждые 15 минут, пока данные не появятся.</small></div>"]
    color, why, notes = traffic.decide(m)
    f = lambda v, u="": "—" if v is None else f"{v}{u}"
    rn = "" if m["readiness_now"] in (None, m["readiness"]) else f", последнее обновление Garmin {m['readiness_now']} ({m['readiness_now_at']})"
    bn = "" if m["bb_now"] in (None, m["bb"]) else f", на последней синхронизации {m['bb_now']}"
    ra = f" ({m['readiness_at']})" if m["readiness_at"] else ""
    vals = (f"Утренние значения: Readiness {f(m['readiness'])}{ra}{rn} · Body Battery {f(m['bb'])}{m['bb_note']}{bn} · сон {f(m['sleep_h'], ' ч')}"
            f" (Sleep Score {f(m['sleep_score'])}) · HRV {f(m['hrv'])} · пульс покоя {f(m['rhr'])}")
    h = [f"<h2>Светофор на {d}</h2><div class='tl {color}'><b>{TL_NAME[color]}</b><br>{e('; '.join(why))}<br>"
         f"<b>План:</b> {e(traffic.PLANS[color])}<br><small>{e(vals)}</small>"]
    if notes:
        h.append(f"<br><small>{e('; '.join(notes))}</small>")
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
        h.append(f"<br><small>Вчера отмечено: {e(', '.join(names[k] for k in life))}. Сегодня бег только лёгкий, без интервалов.</small>")
    h.append("</div>")
    return h


def current_slots(con):
    row, data = program.active(con)
    if data:
        return program.slots_of(data, stg.day_overrides())
    return stg.load()["slots"]


def plan_html(con):
    e = html.escape
    slots = current_slots(con)
    cfg = {"slots": slots}
    today = timeutil.today()
    mon = plan.monday(today)
    dn = lambda d: "любой день" if d is None else DAYS[d]
    h = ["<h2>Недельный план</h2><table><tr><th>Неделя</th>" + "".join(f"<th>{e(s['label'])}<br><small>план: {dn(s['day'])}</small></th>" for s in slots) + "<th>Неделя</th></tr>"]
    for w in range(4):
        wm = mon - timedelta(days=7 * w)
        cells, ok = plan.week_status(con, cfg, wm, today)
        h.append(f"<tr><td>{wm.strftime('%d.%m')}–{(wm + timedelta(days=6)).strftime('%d.%m')}</td>")
        for c in cells:
            h.append(f"<td class='c-{c['status']}'>{e(plan.LABEL[c['status']])}<br><small>{'' if c['date'] is None else c['date'].strftime('%d.%m') + ' ' + DAYS[c['date'].weekday()]}</small></td>")
        done = all(c['status'] in ('done', 'moved') for c in cells)
        h.append(f"<td>{'✅ выполнена' if done else ('идёт' if w == 0 else '❌ не выполнена')}</td></tr>")
    h.append("</table><details><summary>Изменить дни тренировок</summary><form method=post action=settings>")
    for s in slots:
        opts = f"<option value=any{' selected' if s['day'] is None else ''}>любой день</option>" + "".join(
            f"<option value={i}{' selected' if i == s['day'] else ''}>{n}</option>" for i, n in enumerate(DAYS))
        h.append(f"<p>{e(s['label'])}: <select name='day_{e(s['key'])}'>{opts}</select></p>")
    h.append("<button>Сохранить</button></form></details>")
    return h


def program_html(con, notice=None, editor_text=None):
    e = html.escape
    row, data = program.active(con)
    draft = con.execute("SELECT * FROM program_version WHERE kind='черновик' ORDER BY id DESC LIMIT 1").fetchone()
    h = ["<h2>Программа</h2>"]
    if notice:
        h.append(f"<div class=err>{notice}</div>")
    if data:
        h.append(f"<p>Активная версия №{row['id']} ({e(row['kind'])}, {e(row['created_at'])}). Прибавка: штанга +{data['progression']['step_kg']['barbell']:g} кг, "
                 f"гантели +{data['progression']['step_kg']['dumbbell']:g} кг. Тренировки учитываются с {e(str(data['evaluate_from']))}.</p>")
        for s in data["sessions"]:
            if s["kind"] != "strength":
                h.append(f"<h3>{e(s['name'])}</h3><p>Бег, без упражнений.</p>")
                continue
            sent = con.execute("SELECT * FROM garmin_workout WHERE session_key=?", (s["key"],)).fetchone()
            sent_txt = f" Отправлено в Garmin: «{e(sent['name'])}», {e(sent['updated_at'])}." if sent else " В Garmin ещё не отправлялась."
            h.append(f"<h3>{e(s['name'])}</h3><form method=post action=garmin_send><input type=hidden name=key value='{e(s['key'])}'>"
                     f"<button>Отправить в Garmin</button><small>{sent_txt}</small></form>")
            for b in s["blocks"]:
                lines = "".join(f"<div><b>{e(ru(x['code']))}</b>: {'/'.join(f'{w:g}' for w in x['weights'])} кг · повторы {x['reps'][0]}–{x['reps'][1]}</div>" for x in b["exercises"])
                h.append(f"<div class={'ss' if b['type'] == 'superset' else 'ex'}>{'<span class=tag>Суперсет</span>' if b['type'] == 'superset' else ''}{lines}</div>")
        log = con.execute("SELECT * FROM progress_log WHERE code!='*' ORDER BY at DESC LIMIT 6").fetchall()
        if log:
            h.append("<h3>Последние прибавки веса</h3><ul>" + "".join(f"<li>{e(r['at'][:10])}: {e(r['text'])}</li>" for r in log) + "</ul>")
    else:
        h.append("<p>Программы пока нет. Создай черновик из последних тренировок Garmin «Full Body A» и «Full Body B».</p>")
    h.append("<form method=post action=draft><button>Создать черновик из Garmin</button></form>")
    if data:
        h.append("<form method=post action=enrich><button>Дополнить программу из Garmin (отдых и категории)</button></form>")
    text = editor_text
    if text is None:
        text = draft["yaml"] if draft and (not row or draft["id"] > row["id"]) else (row["yaml"] if row else "")
    if text:
        opened = " open" if (editor_text is not None or (draft and (not row or draft['id'] > row['id']))) else ""
        h.append(f"<details{opened}><summary>Редактор программы (YAML)</summary><form method=post action=program>"
                 f"<textarea name=yaml rows=30 style='width:100%;font-family:monospace'>{e(text)}</textarea><br>"
                 "<button>Проверить и сохранить новой версией</button></form>"
                 "<p><small>Вес в Garmin уходит по верхней границе диапазона повторов; диапазон хранится здесь как справка и для прибавки веса. "
                 "weights — вес по подходам (кг), 0 — свой вес. day: 0 (пн) … 6 (вс) или без day — любой день.</small></p></details>")
    vers = con.execute("SELECT * FROM program_version ORDER BY id DESC LIMIT 8").fetchall()
    if vers:
        h.append("<details><summary>Версии программы</summary><table><tr><th>№</th><th>Когда</th><th>Вид</th><th>Что изменилось</th><th></th></tr>")
        for v in vers:
            btn = "активна" if v["active"] else f"<form method=post action=rollback><input type=hidden name=id value={v['id']}><button>Откатить</button></form>"
            h.append(f"<tr><td>{v['id']}</td><td>{e(v['created_at'])}</td><td>{e(v['kind'])}</td><td>{e((v['note'] or '')[:160])}</td><td>{btn}</td></tr>")
        h.append("</table></details>")
    return h


def page(con, cfg, notice=None, editor_text=None):
    e = html.escape
    runs = con.execute("SELECT * FROM sync_run ORDER BY id DESC LIMIT 8").fetchall()
    h = [f"<h1>Тренировочный центр</h1><form method=post action=sync><button>Синхронизировать с Garmin</button></form>"]
    h += traffic_html(con)
    h += plan_html(con)
    h += program_html(con, notice, editor_text)
    h += [
         "<h2>Синхронизации</h2><table><tr><th>Начало</th><th>Запуск</th><th>Итог</th><th>Дней</th><th>Тренировок</th><th>Подходов</th><th>Ошибки</th></tr>"]
    for r in runs:
        st = "идёт" if r["finished_at"] is None else ('<span class=ok>ок</span>' if r["ok"] else '<span class=bad>сбой</span>')
        h.append(f"<tr><td>{e(r['started_at'] or '')}</td><td>{e(r['trigger'] or '')}</td><td>{st}</td><td>{r['days'] or ''}</td>"
                 f"<td>{r['activities'] or ''}</td><td>{r['sets'] or ''}</td><td><pre>{e((r['errors'] or '')[:600])}</pre></td></tr>")
    if not runs:
        h.append("<tr><td colspan=7>Синхронизаций ещё не было</td></tr>")
    h.append("</table>")
    h += activities_html(con)
    h.append("<h2>Последние силовые тренировки</h2>")
    div = cfg["weight_divisor"]
    acts = con.execute("SELECT * FROM activity WHERE type_key='strength_training' ORDER BY start_local DESC LIMIT 3").fetchall()
    for a in acts:
        h.append(f"<h3>{e(a['start_local'] or '')} — {e(a['name'] or '')}</h3>")
        sets = [dict(exercise=r["exercise"], reps=r["reps"], kg=(r["weight_raw"] or 0) / div)
                for r in con.execute("SELECT * FROM workout_set WHERE activity_id=? AND set_type='ACTIVE' ORDER BY idx", (a["id"],))]
        for b in group_blocks(sets):
            h.append(block_html(b))
    h.append(f"<p><small>Вес = значение Garmin / {div}. Суперсет определяется по чередованию упражнений. Знак ⁇ — названия нет в словаре. Свои названия: {e(overrides_status())}.</small></p>")
    return f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'><style>{STYLE}</style>" + "".join(h)


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
            if path.endswith("settings"):
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
                    wid, mode = gw.send(api, con, sess, gw.category_map(con))
                    lines = "".join(f"<li>{html.escape(x)}</li>" for x in gw.summary(sess))
                    msg = (f"<b>Тренировка {mode} в Garmin Connect:</b> «{html.escape(gw.garmin_name(sess))}» (№{wid}). "
                           f"Теперь отправь её на часы из Garmin Connect.<ul>{lines}</ul>")
                except Exception as ex:
                    msg = f"<b>Не отправлено:</b> {html.escape(str(ex))}"
                return self._send(200, page(con, cfg, notice=msg).encode())
            elif path.endswith("enrich"):
                row, data = program.active(con)
                if data:
                    n = gw.enrich(con, data)
                    if n:
                        program.save_version(con, program.dump(data), "ручная", f"дополнено из Garmin (отдых, категории): {n} знач.", True)
            elif path.endswith("rollback"):
                v = (form.get("id") or [""])[0]
                if v.isdigit():
                    program.rollback(con, int(v))
            else:
                threading.Thread(target=start_sync, args=("кнопка",), daemon=True).start()
            self._send(303, loc="./")

        def log_message(self, *a):
            pass
    ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()
