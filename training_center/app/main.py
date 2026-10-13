import threading, time
from . import report, timeutil, db, sync, web, progress, traffic, program, settings as stg, garmin_workout as gw

CFG = {"weight_divisor": 1000.0, "token_dir": "/config/garmin", "db": "/data/training.db",
       "auto_hours": 3, "days_first": 30, "days_regular": 3}


def morning_incomplete(c):
    """Утро (5-12 ч) и по сегодняшнему дню ещё нет сна/Readiness -> стоит повторить синхронизацию."""
    now = timeutil.now_dt()
    if not 5 <= now.hour < 12:
        return False
    return bool(traffic.missing_morning(traffic.metrics(c, now.date().isoformat())))


def main():
    print("Часовой пояс:", timeutil.setup(), timeutil.now(), flush=True)
    con = db.connect(CFG["db"])
    first = con.execute("SELECT COUNT(*) FROM sync_run WHERE ok=1").fetchone()[0] == 0

    def start_sync(trigger):
        nonlocal first
        days = CFG["days_first"] if first else CFG["days_regular"]
        # отдельное соединение для потока
        c = db.connect(CFG["db"])
        if sync.run_sync(c, lambda: sync.login(CFG["token_dir"]), days, trigger) and first:
            first = c.execute("SELECT COUNT(*) FROM sync_run WHERE ok=1").fetchone()[0] == 0
        try:
            progress.apply_all(c, CFG["weight_divisor"])
        except Exception as e:  # noqa — сбой прибавки не должен ломать синхронизацию
            print("Ошибка расчёта прибавки веса:", e, flush=True)
        try:
            row, data = program.active(c)
            if data:
                gw.sync_all(c, data, lambda: sync.login(CFG["token_dir"]), "после синхронизации", stg.get("push_to_device", False))
        except Exception as e:  # noqa
            print("Ошибка обновления тренировки в Garmin:", e, flush=True)
        try:
            report.auto_after_sync(c, CFG["weight_divisor"])
        except Exception as e:  # noqa
            print("Ошибка автоотчёта:", e, flush=True)

    def loop():
        start_sync("при запуске")
        while True:
            try:
                wait = 900 if morning_incomplete(db.connect(CFG["db"])) else CFG["auto_hours"] * 3600
            except Exception:
                wait = CFG["auto_hours"] * 3600
            time.sleep(wait)
            start_sync("авто")
    threading.Thread(target=loop, daemon=True).start()
    web.serve(con, CFG, start_sync)


if __name__ == "__main__":
    main()
