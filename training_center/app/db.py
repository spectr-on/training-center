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
    # 2
    """
    CREATE TABLE program_version(id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, kind TEXT,
        note TEXT, yaml TEXT, active INTEGER DEFAULT 0);
    CREATE TABLE progress_log(activity_id INTEGER, code TEXT, at TEXT, text TEXT,
        PRIMARY KEY(activity_id, code));
    """,
    # 3
    """
    CREATE TABLE garmin_workout(session_key TEXT PRIMARY KEY, workout_id INTEGER, name TEXT, updated_at TEXT);
    """,
    # 4: метки времени, записанные раньше в UTC с буквой T, переводим в местное время
    "PY:times",
    # 5: слепок отправленной тренировки и журнал отправок в Garmin
    """
    ALTER TABLE garmin_workout ADD COLUMN payload_hash TEXT;
    CREATE TABLE push_log(id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, session_key TEXT, why TEXT, ok INTEGER, text TEXT);
    """,
    # 6: активности, о которых уже сообщено событием training_workout_done
    """
    CREATE TABLE mqtt_event(activity_id INTEGER PRIMARY KEY, at TEXT);
    """,
    # 7: вес тела, замеры лентой, ощущения после бега (ввод вручную)
    """
    CREATE TABLE body_weight(id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, weight_kg REAL NOT NULL,
        fat_pct REAL, water_pct REAL, bone_kg REAL, visceral REAL, source TEXT NOT NULL DEFAULT 'manual', note TEXT);
    CREATE INDEX body_weight_at ON body_weight(at);
    CREATE TABLE body_measure(date TEXT PRIMARY KEY, neck REAL, shoulders REAL, chest REAL, waist REAL, hips REAL,
        arm REAL, thigh REAL, calf REAL, note TEXT);
    CREATE TABLE run_feel(activity_id INTEGER PRIMARY KEY, score INTEGER NOT NULL, note TEXT, at TEXT);
    """,
]


def _migrate_times(con):
    from . import timeutil
    for table, cols in (("sync_run", ("started_at", "finished_at")), ("program_version", ("created_at",)),
                        ("progress_log", ("at",)), ("garmin_workout", ("updated_at",)), ("daily_raw", ("fetched_at",))):
        for col in cols:
            for r in con.execute(f"SELECT rowid AS rid, {col} AS v FROM {table} WHERE {col} LIKE '____-__-__T%'").fetchall():
                try:
                    new = timeutil.utc_iso_to_local(r["v"])
                except Exception:
                    continue
                con.execute(f"UPDATE {table} SET {col}=? WHERE rowid=?", (new, r["rid"]))


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
            if MIGRATIONS[i] == "PY:times":
                _migrate_times(con)
            else:
                con.executescript(MIGRATIONS[i])
            con.execute(f"PRAGMA user_version={i + 1}")
        con.commit()
    return con
