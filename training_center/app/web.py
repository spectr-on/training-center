"""Страница статуса (ingress). Только стандартная библиотека. Все ссылки относительные."""
import html, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STYLE = ("body{font-family:system-ui;max-width:900px;margin:16px auto;padding:0 16px}"
         "table{border-collapse:collapse;width:100%}td,th{border:1px solid #8884;padding:4px 8px;text-align:left}"
         ".ok{color:#2a8a2a}.bad{color:#c33}button{padding:10px 18px;font-size:16px}")


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
    h.append("</table><h2>Последние силовые тренировки</h2>")
    acts = con.execute("SELECT * FROM activity WHERE type_key='strength_training' ORDER BY start_local DESC LIMIT 3").fetchall()
    div = cfg["weight_divisor"]
    for a in acts:
        h.append(f"<h3>{e(a['start_local'] or '')} — {e(a['name'] or '')}</h3><table><tr><th>#</th><th>Упражнение</th><th>Повторы</th><th>Вес, кг*</th><th>Исходное</th></tr>")
        for s in con.execute("SELECT * FROM workout_set WHERE activity_id=? AND set_type='ACTIVE' ORDER BY idx", (a["id"],)):
            w = s["weight_raw"]
            h.append(f"<tr><td>{s['idx']}</td><td>{e(str(s['exercise']))}</td><td>{s['reps']}</td>"
                     f"<td>{'' if w is None else round(w / div, 2)}</td><td>{w}</td></tr>")
        h.append("</table>")
    h.append(f"<p><small>* вес = исходное значение Garmin / {div} (делитель в настройках, проверяется по 8 октября).</small></p>")
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
