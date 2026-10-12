"""Группировка подходов в блоки: обычное упражнение или суперсет (чередование 2-3 упражнений)."""


def group_blocks(sets):
    """sets: список dict с ключом 'exercise'. Возвращает [{'kind':'single'|'superset','items':[...]}]."""
    ex = [s["exercise"] for s in sets]
    out, i, n = [], 0, len(sets)
    while i < n:
        block = None
        for k in (2, 3):
            cyc = ex[i:i + k]
            if len(cyc) == k and len(set(cyc)) == k:
                j = i
                while j < n and ex[j] == cyc[(j - i) % k]:
                    j += 1
                if j - i >= 2 * k:
                    block = {"kind": "superset", "items": sets[i:j]}
                    i = j
                    break
        if block is None:
            j = i
            while j < n and ex[j] == ex[i]:
                j += 1
            block = {"kind": "single", "items": sets[i:j]}
            i = j
        out.append(block)
    return out
