# ═══ gcompare/core/data.py ═════════════════════════════════════════════
# РОЛЬ        : генераторы рядов Random / Meander / Saw и константа N_POINTS
# ИСПОЛЬЗУЮТ  : model.py (при каждой генерации), cli.py
# СМОТРИ ТАКЖЕ: ARCHITECTURE.md, §3
# ──────────────────────────────────────────────────────────────────────
"""Генераторы тестовых рядов.

Ключевая идея для 1-часового теста: массив данных фиксированной длины
(``N_POINTS``) создаётся один раз на кадр и полностью заменяется новым.
Никакого накопления в списках, никаких ``append`` в цикле — только
векторизованные операции NumPy, которые возвращают новый буфер, а старый
освобождает сборщик мусора. Это исключает рост памяти от самих данных.
"""

from __future__ import annotations

import numpy as np

#: Идентичные для всех трёх приложений типы графиков.
GRAPH_TYPES = ("Random", "Meander", "Saw")

#: Количество точек на кадр. 4096 — компромисс: ряд выглядит плотным,
#: а кадр на 860×720 px рисуется ~40 мс на всех трёх бэкендах
#: (8 192 точки дают ~55–110 мс — заметно для интерактивного зума).
#: Меняется флагом ``--points`` без правки кода.
N_POINTS = 4096

#: Домены по X (условные единицы).
X_MIN, X_MAX = 0.0, 100.0


def generate(kind: str, n: int = N_POINTS, rng: np.random.Generator | None = None):
    """Возвращает ``(x, y)`` — два новых массива float64 длины ``n``.

    Parameters
    ----------
    kind : str
        Один из :data:`GRAPH_TYPES`.
    n : int
        Число точек (>= 64).
    rng : numpy.random.Generator | None
        Внешний генератор — чтобы можно было воспроизводить картинку.
    """
    if kind not in GRAPH_TYPES:
        raise ValueError(f"Неизвестный тип графика: {kind!r}")
    n = max(64, int(n))
    rng = rng or np.random.default_rng()

    # x — равномерная сетка, создаётся заново (старый буфер уходит в GC).
    x = np.linspace(X_MIN, X_MAX, n, dtype=np.float64)

    if kind == "Random":
        # Чистый гауссов шум: ноль тренда, постоянная дисперсия.
        y = rng.standard_normal(n)

    elif kind == "Meander":
        # Случайное блуждание: кумулятивная сумма шагов, нормировка в [-1, 1].
        walk = np.cumsum(rng.standard_normal(n))
        walk -= walk.mean()
        peak = np.max(np.abs(walk)) or 1.0
        y = walk / peak

    else:  # "Saw"
        # Пилообразная волна: 8 периодов на весь диапазон X.
        periods = 8.0
        phase = (x - X_MIN) / (X_MAX - X_MIN) * periods
        y = 2.0 * (phase - np.floor(phase)) - 1.0

    # ascontiguousarray: гарантирует непрерывный буфер → быстрый set_data.
    return x, np.ascontiguousarray(y, dtype=np.float64)


def describe(kind: str) -> str:
    """Человекочитаемое описание ряда — используется в подсказках UI."""
    return {
        "Random": "Гауссов шум, нулевое среднее",
        "Meander": "Случайное блуждание (меандр)",
        "Saw": "Пилообразная волна, 8 периодов",
    }.get(kind, kind)
