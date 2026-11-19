# ═══ gcompare/registry.py ══════════════════════════════════════════════
# РОЛЬ        : метаданные трёх приложений и сборка команды их запуска
# ИСПОЛЬЗУЮТ  : run.py, launcher_tk.py
# СМОТРИ ТАКЖЕ: ARCHITECTURE.md, §4
# ──────────────────────────────────────────────────────────────────────
"""Реестр приложений: единые метаданные и команда запуска.

Используется и лаунчером, и CLI-режимом ``run.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

from .core.data import N_POINTS

ROOT = Path(__file__).resolve().parents[1]

APPS: dict[str, dict[str, str]] = {
    "tk": {
        "title": "Tkinter",
        "canvas": "FigureCanvasTkAgg",
        "module": "gcompare.apps.tk_app",
        "note": "Входит в стандартную поставку Python. Минимум зависимостей,\n"
                "canvas позиционируется через place() — самая жёсткая\n"
                "гарантия рамки из трёх вариантов.",
    },
    "qt": {
        "title": "PyQt6",
        "canvas": "FigureCanvasQTAgg",
        "module": "gcompare.apps.qt_app",
        "note": "Нативные сигналы/слоты, отличный HiDPI, предсказуемый layout.\n"
                "Тяжёлая зависимость (~90 МБ Qt6), лицензия GPL/коммерческая.",
    },
    "wx": {
        "title": "wxPython",
        "canvas": "FigureCanvasWxAgg",
        "module": "gcompare.apps.wx_app",
        "note": "Нативные контролы ОС, sizer-раскладка, LGPL. Хорош для\n"
                "десктопных корпоративных приложений.",
    },
}

ORDER = ("tk", "qt", "wx")


def child_cmd(key: str, duration: float = 0.0, stress: bool = False,
              points: int = N_POINTS) -> list[str]:
    """Команда запуска приложения в отдельном процессе (свой event loop)."""
    cmd = [sys.executable, "-m", APPS[key]["module"]]
    if duration:
        cmd += ["--duration", str(int(duration))]
    if stress:
        cmd += ["--stress"]
    if points and points != N_POINTS:
        cmd += ["--points", str(points)]
    return cmd
