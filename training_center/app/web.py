"""Страница статуса (ingress). Только стандартная библиотека. Все ссылки относительные."""
import html, threading
from .blocks import group_blocks
from .exercise_names import ru
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STYLE = ("body{font-family:system-ui;max-width:900px;margin:16px auto;padding:0 16px}"
         "table{border-collapse:collapse;width:100%}td,th{border:1px solid #8884;padding:4px 8px;text-align:left}"
         ".ss{border-left:4px solid #e08a00;background:#e08a0018;padding:6px 10px;margin:8px 0}.ex{padding:4px 10px;margin:8px 0}.tag{display:inline-block;background:#e08a00;color:#fff;border-radius:4px;padding:0 6px;font-size:12px;margin-bottom:4px}.ok{color:#2a8a2a}.bad{color:#c33}button{padding:10px 18px;font-size:16px}")


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


def page(con, cfg):
    e = html.escape
    runs = con.execute("SELECT * FROM sync_run ORDER BY id DESC LIMIT 8").fetchall()
    h = [f"<h1>Тренировочный центр</h1><form method=post action=sync><button>Синхронизировать с Garmin</button></form>",
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
    h.append(f"<p><small>Вес = значение Garmin / {div}. Суперсет определяется по чередованию упражнений. Знак ⁇ — названия нет в словаре.</small></p>")
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
            self._send(200, page(con, cfg).encode())

        def do_POST(self):
            threading.Thread(target=start_sync, args=("кнопка",), daemon=True).start()
            self._send(303, loc="./")

        def log_message(self, *a):
            pass
    ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()
