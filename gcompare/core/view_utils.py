# ═══ gcompare/core/view_utils.py ═══════════════════════════════════════
# РОЛЬ        : клиппинг matplotlib, проверка выхода точек за кадр, оформление осей, rcParams
# ИСПОЛЬЗУЮТ  : tk_app.py, qt_app.py, wx_app.py, tools/soak_test.py
# СМОТРИ ТАКЖЕ: ARCHITECTURE.md, §5
# ──────────────────────────────────────────────────────────────────────
"""Помощники matplotlib: жёсткий клиппинг и его runtime-проверка.

Matplotlib по умолчанию клиппит ``Line2D`` по ``axes.bbox``, но «по умолчанию»
легко потерять при вызове ``ax.plot(..., clip_on=False)``, при работе с
``AnnotationBbox`` или при ручной установке ``set_clip_path(None)``.
Поэтому клиппинг включается ЯВНО и проверяется на каждом кадре.
"""

from __future__ import annotations

from typing import Iterable, Sequence

import numpy as np


def configure_matplotlib() -> None:
    """Единые настройки matplotlib для всех трёх приложений.

    ``path.simplify_threshold = 1.0`` (вместо 1/9) выбрасывает точки, которые
    всё равно попадают в тот же пиксель: на 4096 точках это ускоряет кадр
    примерно вдвое без видимой разницы.
    """
    import matplotlib

    matplotlib.rcParams.update({
        "path.simplify": True,
        "path.simplify_threshold": 1.0,
        "agg.path.chunksize": 0,
        "figure.max_open_warning": 0,
        "toolbar": "none",
        "font.size": 8,
        "axes.unicode_minus": True,
    })


def enforce_clipping(ax, artists: Iterable) -> None:
    """Включает клиппинг по области axes для всех переданных художников."""
    patch = ax.patch
    patch.set_clip_on(True)
    patch.set_clip_box(ax.bbox)
    for art in artists:
        art.set_clip_on(True)
        art.set_clip_path(patch)
        art.set_clip_box(ax.bbox)


def check_bounds(
    xlim: Sequence[float],
    ylim: Sequence[float],
    x: np.ndarray,
    y: np.ndarray,
    eps: float = 1e-9,
) -> int:
    """Считает точки, вышедшие за пределы осей (должно быть 0).

    Проверка численная, а не пиксельная: она валидна до ``draw()`` и стоит
    доли миллисекунды, поэтому её можно выполнять на каждом кадре.
    """
    if x.size == 0:
        return 0
    outside = (
        (x < xlim[0] - eps)
        | (x > xlim[1] + eps)
        | (y < ylim[0] - eps)
        | (y > ylim[1] + eps)
    )
    return int(np.count_nonzero(outside))


def style_axes(ax, *, bg: str, grid: str, fg: str, title: str = "") -> None:
    """Единое оформление осей для всех трёх бэкендов."""
    ax.set_facecolor(bg)
    ax.grid(True, color=grid, linewidth=0.6, alpha=0.9)
    ax.tick_params(colors=fg, labelsize=8, length=3)
    for spine in ax.spines.values():
        spine.set_color(grid)
        spine.set_linewidth(1.0)
    ax.set_xlabel("X", color=fg, fontsize=8, labelpad=2)
    ax.set_ylabel("Y", color=fg, fontsize=8, labelpad=2)
    if title:
        ax.set_title(title, color=fg, fontsize=9, pad=4)
    ax.margins(0)  # никаких автоматических отступов
