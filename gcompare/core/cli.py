# ═══ gcompare/core/cli.py ══════════════════════════════════════════════
# РОЛЬ        : общие аргументы командной строки для трёх приложений
# ИСПОЛЬЗУЮТ  : tk_app.py, qt_app.py, wx_app.py
# СМОТРИ ТАКЖЕ: ARCHITECTURE.md, §4
# ──────────────────────────────────────────────────────────────────────
"""Общий набор аргументов командной строки для всех трёх приложений."""

from __future__ import annotations

import argparse

from . import data as data_mod


def build_parser(description: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=description)
    p.add_argument("--points", type=int, default=data_mod.N_POINTS,
                   help=f"точек на кадр (по умолчанию {data_mod.N_POINTS})")
    p.add_argument("--interval", type=int, default=1000,
                   help="период замера памяти, мс (по умолчанию 1000)")
    p.add_argument("--stress-interval", type=int, default=500,
                   help="период авто-перерисовки, мс (по умолчанию 500)")
    p.add_argument("--stress", action="store_true",
                   help="сразу включить стресс-тест (авто-перерисовку)")
    p.add_argument("--duration", type=float, default=0.0,
                   help="автостоп через N секунд (3600 = часовой прогон), 0 — до закрытия окна")
    p.add_argument("--log-dir", default="logs", help="каталог для CSV/отчётов")
    p.add_argument("--seed", type=int, default=None, help="фиксировать генератор случайных чисел")
    return p
