# ═══ gcompare/core/model.py ════════════════════════════════════════════
# РОЛЬ        : данные графика, окно зума и пределы осей (главный инвариант клиппинга)
# ИСПОЛЬЗУЮТ  : session.py → приложения через сигнал render_request
# СМОТРИ ТАКЖЕ: ARCHITECTURE.md, §3 и §5
# ──────────────────────────────────────────────────────────────────────
"""Модель графика: данные, окно просмотра (зум) и жёсткие границы кадра.

Здесь живёт главный инвариант приложения:

    Для любого кадра все точки, отданные в ``Line2D``, лежат внутри
    ``(xlim, ylim)``. Пределы по Y вычисляются ИЗ ВИДИМОГО СРЕЗА данных,
    поэтому линия физически не может выйти за рамку axes — даже после
    десятка ZOOM IN.

Модель ничего не знает про GUI: тот лишь подписывается на сигналы и
перерисовывает canvas.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import data as data_mod
from .signals import Signal

#: Во сколько раз окно просмотра сокращается за один шаг ZOOM IN.
ZOOM_FACTOR = 2.0

#: Минимальная ширина окна в точках — защита от «зума в бесконечность»,
#: при котором matplotlib начал бы рисовать единичные пиксели.
MIN_WINDOW_POINTS = 64

#: Максимум шагов зума (2^14 ≈ 16384× — дальше уже нет смысла).
MAX_ZOOM_STEPS = 14

#: Отступ по Y (доля размаха), чтобы линия не касалась рамки.
Y_PADDING = 0.06


@dataclass(frozen=True)
class ViewPayload:
    """Готовый к отрисовке кадр: только видимый срез данных."""

    kind: str
    x: np.ndarray
    y: np.ndarray
    xlim: tuple[float, float]
    ylim: tuple[float, float]
    points_total: int
    points_visible: int
    zoom_steps: int

    @property
    def zoom_label(self) -> str:
        return "1×" if self.zoom_steps == 0 else f"{ZOOM_FACTOR ** self.zoom_steps:.0f}×"


class PlotModel:
    """Данные + окно просмотра. Один экземпляр на окно приложения."""

    def __init__(self, n_points: int = data_mod.N_POINTS, seed: int | None = None) -> None:
        self.n_points = int(n_points)
        self.rng = np.random.default_rng(seed)

        self.data_changed = Signal("data_changed")   # (ViewPayload)
        self.view_changed = Signal("view_changed")   # (ViewPayload)

        self.kind: str = data_mod.GRAPH_TYPES[0]
        self._x, self._y = data_mod.generate(self.kind, self.n_points, self.rng)

        # Центр окна в индексах точек; None → центр массива.
        self._center: float | None = None
        self.zoom_steps: int = 0

    # ------------------------------------------------------------- данные
    def generate(self, kind: str | None = None) -> ViewPayload:
        """Новый ряд (кнопка «Показать график»). Старые массивы уходят в GC.

        Текущий зум сохраняется: так «Показать график» в увеличенном окне
        не сбрасывает масштаб, а стресс-тест дополнительно проверяет
        клиппинг уже на срезе данных.
        """
        if kind is not None:
            self.kind = kind
        self._x, self._y = data_mod.generate(self.kind, self.n_points, self.rng)
        payload = self.payload()
        self.data_changed.emit(payload)
        return payload

    # --------------------------------------------------------------- зум
    def _window_indices(self) -> tuple[int, int]:
        """Индексы [i0, i1) видимого окна. Всегда внутри массива."""
        n = self._x.size
        length = max(MIN_WINDOW_POINTS, n / (ZOOM_FACTOR ** self.zoom_steps))
        length = min(length, float(n))

        center = (n - 1) / 2.0 if self._center is None else self._center
        i0 = center - length / 2.0
        i1 = i0 + length

        # Прижимаем окно к границам массива, сохраняя его ширину.
        # Верхняя граница — эксклюзивный индекс n (полный ряд = n точек).
        if i0 < 0:
            i1 -= i0
            i0 = 0.0
        if i1 > n:
            i0 -= i1 - n
            i1 = float(n)
        i0 = max(0.0, i0)
        i1 = min(float(n), i1)

        a, b = int(round(i0)), int(round(i1))
        if b <= a:                       # защита от вырожденного окна
            b = min(n, a + 1)
            a = max(0, b - 1)
        return a, b

    def zoom_in(self) -> ViewPayload | None:
        if self.zoom_steps >= MAX_ZOOM_STEPS:
            return None
        i0, i1 = self._window_indices()
        self._center = (i0 + i1) / 2.0         # фиксируем центр текущего окна
        self.zoom_steps += 1
        return self._emit_view()

    def zoom_out(self) -> ViewPayload | None:
        if self.zoom_steps <= 0:
            return None
        i0, i1 = self._window_indices()
        self._center = (i0 + i1) / 2.0
        self.zoom_steps -= 1
        return self._emit_view()

    def reset_zoom(self, emit: bool = True) -> ViewPayload | None:
        if self.zoom_steps == 0 and self._center is None:
            return None
        self.zoom_steps = 0
        self._center = None
        return self._emit_view() if emit else None

    # ------------------------------------------------------------ кадр
    def payload(self) -> ViewPayload:
        """Собирает видимый срез и пределы осей, гарантирующие клиппинг."""
        i0, i1 = self._window_indices()
        xv = self._x[i0:i1]
        yv = self._y[i0:i1]

        xlim = (float(xv[0]), float(xv[-1])) if xv.size else (data_mod.X_MIN, data_mod.X_MAX)

        if yv.size:
            lo = float(np.min(yv))
            hi = float(np.max(yv))
            span = hi - lo
            if span < 1e-9:                    # плоский ряд — искусственный зазор
                span = 1.0 if abs(hi) < 1.0 else abs(hi) * 0.1
                lo -= span / 2.0
                hi += span / 2.0
                span = hi - lo
            pad = span * Y_PADDING
            ylim = (lo - pad, hi + pad)
        else:
            ylim = (-1.0, 1.0)

        return ViewPayload(
            kind=self.kind,
            x=xv,
            y=yv,
            xlim=xlim,
            ylim=ylim,
            points_total=int(self._x.size),
            points_visible=int(xv.size),
            zoom_steps=self.zoom_steps,
        )

    def _emit_view(self) -> ViewPayload:
        payload = self.payload()
        self.view_changed.emit(payload)
        return payload
