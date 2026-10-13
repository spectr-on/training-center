"""Единое местное время. Контейнер аддона работает в UTC, поэтому часовой пояс задаём явно."""
import os
import time
from datetime import datetime, timedelta, timezone

DEFAULT_TZ = "Asia/Yekaterinburg"   # запасной, если Supervisor не передал TZ
FMT = "%Y-%m-%d %H:%M:%S"
_tz = None
_name = None


def setup():
    global _tz, _name
    _name = os.environ.get("TZ") or DEFAULT_TZ
    os.environ["TZ"] = _name
    try:
        time.tzset()
    except Exception:
        pass
    _tz = _load(_name)
    return _name


def _load(name):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        return timezone(timedelta(hours=5))   # Пермь, если база часовых поясов недоступна


def tz():
    if _tz is None:
        setup()
    return _tz


def tz_name():
    tz()
    return _name


def now_dt():
    return datetime.now(tz())


def now():
    return now_dt().strftime(FMT)


def today():
    return now_dt().date()


def utc_iso_to_local(s):
    """'2026-10-13T02:16:48' (UTC, старый формат) -> '2026-10-13 07:16:48' (местное)."""
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz()).strftime(FMT)
