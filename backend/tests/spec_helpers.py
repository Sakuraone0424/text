"""Rubrics that satisfy the current specification (an essay needs four to six scoring points)."""


def rubric(kind, points, description="Synthetic fixture criterion"):
    if kind != "essay":
        return [{"description": description, "points": points}]
    parts = [points // 5 + (1 if i < points % 5 else 0) for i in range(5)]
    return [{"description": f"{description} {i + 1}", "points": p} for i, p in enumerate(parts)]
