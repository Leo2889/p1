# ═══ gcompare/core/theme.py ════════════════════════════════════════════
# РОЛЬ        : палитра, подписи, AXES_RECT и мелкие форматтеры — единый вид трёх окон
# ИСПОЛЬЗУЮТ  : все приложения, launcher_tk.py
# СМОТРИ ТАКЖЕ: ARCHITECTURE.md, §7
# ──────────────────────────────────────────────────────────────────────
"""Единая палитра и подписи для трёх приложений.

Вынесено в отдельный модуль, чтобы сравнение библиотек шло «при прочих
равных»: одинаковые цвета, одинаковые подписи, одинаковая сетка окна.
"""

from __future__ import annotations

# --- палитра -------------------------------------------------------------
BG = "#12141a"
PANEL = "#191c23"
PANEL_ALT = "#20242c"
BORDER = "#2e3440"
FG = "#e6e9ef"
MUTED = "#9aa4b2"
ACCENT = "#4da3ff"
OK = "#3ddc84"
WARN = "#ffb020"
ERR = "#ff5c5c"
PLOT_BG = "#0d0f13"
GRID = "#262b35"
LINE = "#58b6ff"

# --- подписи -------------------------------------------------------------
APP_TITLE = "Matplotlib GUI Compare"
SEC_MEM = "ПАМЯТЬ ПРОЦЕССА"
SEC_RAM = "СИСТЕМНАЯ RAM"
SEC_GRAPH = "ГРАФИК"
SEC_ZOOM = "МАСШТАБ"
SEC_SESSION = "СЕССИЯ"
SEC_DIAG = "ДИАГНОСТИКА · ДЕФЕКТЫ И СОБЫТИЯ"

BTN_SHOW = "Показать график"
BTN_ZOOM_IN = "Zoom In"
BTN_ZOOM_OUT = "Zoom Out"
BTN_RESET = "Сброс"
BTN_REPORT = "Сформировать отчёт"
BTN_LOGS = "Папка логов"
CHK_STRESS = "Стресс-тест (авто-перерисовка)"

HINT_CLIP = "clip = ON · линия ограничена рамкой axes"


#: Прямоугольник осей внутри Figure: (left, bottom, width, height).
#: Тип указан явно — matplotlib ожидает именно tuple, а не list
#: (иначе Pylance ругается на несоответствие типов в add_axes).
AXES_RECT: tuple[float, float, float, float] = (0.085, 0.115, 0.895, 0.855)


def title_for(toolkit: str) -> str:
    return f"{APP_TITLE} — {toolkit}"


def fmt_uptime(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def enable_windows_dpi_awareness() -> None:
    """Резкий текст на HiDPI-экранах (Tk и wx этого сами не делают)."""
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 — не Windows или уже включено
        pass
