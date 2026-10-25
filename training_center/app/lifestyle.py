"""Разбор журнала образа жизни Garmin."""

# какие отметки Garmin считаем факторами (имя в Garmin -> наш код)
FACTORS = {"Alcohol": "alcohol", "Алкоголь": "alcohol", "Кальян": "hookah", "Болезнь": "illness"}


def parse_day(resp):
    """Ответ get_lifestyle_logging_data -> {код: {'amount':..} } только для отмеченных (YES)."""
    out = {}
    for item in (resp or {}).get("dailyLogsReport", []):
        if item.get("logStatus") != "YES":
            continue
        code = FACTORS.get(item.get("name"))
        if not code:
            continue
        amount = sum(d.get("amount") or 0 for d in item.get("details") or [])
        out[code] = {"amount": amount or None}
    return out


# ---------- полный разбор для отчёта ИИ (v0.13.0) ----------
# Все пункты журнала, а не только три известных. Статус YES/NO/нет отметки хранится отдельно:
# «нет» — явно отмечено «Нет», «—» — день не отмечен (это не значит «не было»).
CODES = {**FACTORS, "Healthy Meals": "healthy_meals", "Здоровая еда": "healthy_meals"}
LABELS = {"hookah": "кальян", "alcohol": "алкоголь", "illness": "болезнь", "healthy_meals": "здоровая еда"}
SUBTYPES = {"BEER": "пиво", "WINE": "вино", "SPIRIT": "крепкое", "OTHER": "другое"}


def _num(v):
    return str(int(v)) if float(v) == int(v) else f"{v:g}".replace(".", ",")


def parse_log(resp):
    """Ответ get_lifestyle_logging_data -> {код: запись} по ВСЕМ пунктам журнала.
    Запись: name, status (YES/NO/None), amount (сумма или None), parts ({BEER: 1}), quantity (числовой пункт)."""
    out = {}
    for it in (resp or {}).get("dailyLogsReport") or []:
        name = it.get("name")
        if not name:
            continue
        details = it.get("details") or []
        parts = {}
        for d in details:
            if d.get("amount") and d.get("subTypeName"):
                parts[d["subTypeName"]] = parts.get(d["subTypeName"], 0) + d["amount"]
        out[CODES.get(name) or "x:" + name] = dict(
            name=name, status=it.get("logStatus"),
            amount=sum(d.get("amount") or 0 for d in details) or None,
            parts=parts, quantity=it.get("measurementType") == "QUANTITY")
    return out


def cell(rec):
    """Ячейка таблицы «Факторы дня» для одного пункта."""
    if not rec or rec["status"] is None:
        return "—"
    if rec["status"] == "NO":
        return "нет"
    if rec["parts"]:
        return ", ".join(f"{SUBTYPES.get(k, k.lower())} {_num(v)}" for k, v in rec["parts"].items())
    if rec["amount"] is not None:
        return _num(rec["amount"])
    return "да, кол-во не указано" if rec["quantity"] else "да"


def other_cell(log):
    """Все отмеченные YES пункты, кроме кальяна, алкоголя и болезни."""
    out = []
    for code, rec in log.items():
        if code in ("hookah", "alcohol", "illness") or rec["status"] != "YES":
            continue
        label = LABELS.get(code) or rec["name"]
        out.append(label if not (rec["quantity"] and rec["amount"] is not None) else f"{label} {_num(rec['amount'])}")
    return ", ".join(out) or "—"
