import threading, time
from . import db, sync, web

CFG = {"weight_divisor": 1000.0, "token_dir": "/config/garmin", "db": "/data/training.db",
       "auto_hours": 3, "days_first": 30, "days_regular": 3}


def main():
    con = db.connect(CFG["db"])
    first = con.execute("SELECT COUNT(*) FROM sync_run WHERE ok=1").fetchone()[0] == 0

    def start_sync(trigger):
        nonlocal first
        days = CFG["days_first"] if first else CFG["days_regular"]
        # отдельное соединение для потока
        c = db.connect(CFG["db"])
        if sync.run_sync(c, lambda: sync.login(CFG["token_dir"]), days, trigger) and first:
            first = c.execute("SELECT COUNT(*) FROM sync_run WHERE ok=1").fetchone()[0] == 0

    def loop():
        start_sync("при запуске")
        while True:
            time.sleep(CFG["auto_hours"] * 3600)
            start_sync("авто")
    threading.Thread(target=loop, daemon=True).start()
    web.serve(con, CFG, start_sync)


if __name__ == "__main__":
    main()
