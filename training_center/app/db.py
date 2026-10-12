"""SQLite: схема и миграции с номерами."""
import shutil, sqlite3
from pathlib import Path

MIGRATIONS = [
    # 1
    """
    CREATE TABLE daily_raw(date TEXT, kind TEXT, json TEXT, fetched_at TEXT, PRIMARY KEY(date, kind));
    CREATE TABLE activity(id INTEGER PRIMARY KEY, start_local TEXT, type_key TEXT, name TEXT, json TEXT);
    CREATE TABLE workout_set(activity_id INTEGER, idx INTEGER, set_type TEXT, exercise TEXT,
        reps INTEGER, weight_raw REAL, duration REAL, start_time TEXT, json TEXT,
        PRIMARY KEY(activity_id, idx));
    CREATE TABLE sync_run(id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, finished_at TEXT,
        trigger TEXT, ok INTEGER, days INTEGER, activities INTEGER, sets INTEGER, errors TEXT);
    """,
]


def connect(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, check_same_thread=False)
    con.row_factory = sqlite3.Row
    cur = con.execute("PRAGMA user_version").fetchone()[0]
    if cur < len(MIGRATIONS):
        if cur > 0 and path.exists():
            shutil.copy(path, f"{path}.bak-v{cur}")
        for i in range(cur, len(MIGRATIONS)):
            con.executescript(MIGRATIONS[i])
            con.execute(f"PRAGMA user_version={i + 1}")
        con.commit()
    return con
