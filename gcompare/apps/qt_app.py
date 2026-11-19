# ═══ gcompare/apps/qt_app.py ═══════════════════════════════════════════
# РОЛЬ        : приложение №2 — PyQt6 + FigureCanvasQTAgg (нативные сигналы и слоты)
# ИСПОЛЬЗУЮТ  : запускается как python -m gcompare.apps.qt_app
# СМОТРИ ТАКЖЕ: ARCHITECTURE.md, §5
# ──────────────────────────────────────────────────────────────────────
"""Приложение №2 — PyQt6 + ``matplotlib.backends.backend_qtagg``.

Особенности реализации
----------------------
* **Нативные сигналы Qt.** Кнопки используют ``clicked.connect(slot)``,
  слоты помечены ``@pyqtSlot``. Шина контроллера (наш ``Signal``)
  транслируется в ``pyqtSignal`` через объект-мост :class:`Bridge`,
  поэтому GUI-поток и логика остаются развязанными.
* **Жёсткая рамка.** Canvas лежит в ``QFrame#plotFrame`` с layout-полями
  2 px; Qt физически не позволяет ребёнку выйти за родителя
  (``WA_ClipsChildren`` включён по умолчанию).
* **Без утечек.** Один ``Line2D`` + ``set_data``; таймеры —
  ``QTimer`` с родителем (умирают вместе с окном); ``QPlainTextEdit``
  с ``setMaximumBlockCount`` (журнал не растёт).
"""

from __future__ import annotations

import html
import os
import sys
import time
import traceback
from typing import TypeVar

import matplotlib

matplotlib.use("QtAgg", force=True)

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg  # noqa: E402
from matplotlib.figure import Figure                             # noqa: E402

from PyQt6.QtCore import QObject, Qt, QTimer, QUrl, pyqtSignal, pyqtSlot  # noqa: E402
from PyQt6.QtGui import QDesktopServices, QFont                          # noqa: E402
from PyQt6.QtWidgets import (                                            # noqa: E402
    QApplication, QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QMainWindow, QPlainTextEdit, QProgressBar, QPushButton,
    QSizePolicy, QVBoxLayout, QWidget,
)

from ..core import theme                                                 # noqa: E402
from ..core.cli import build_parser                                      # noqa: E402
from ..core.data import GRAPH_TYPES, describe                            # noqa: E402
from ..core.model import ViewPayload                                     # noqa: E402
from ..core.session import SessionController, SessionOptions              # noqa: E402
from ..core.view_utils import (
    check_bounds, configure_matplotlib, enforce_clipping, style_axes,
)  # noqa: E402


QSS = f"""
QMainWindow, #root, #side, #left {{ background: {theme.BG}; }}
QFrame#card {{ background: {theme.PANEL_ALT}; border: 1px solid #262b35; border-radius: 4px; }}
QFrame#plotFrame {{ background: {theme.PLOT_BG}; border: 1px solid {theme.BORDER}; }}
QLabel {{ color: {theme.FG}; background: transparent; }}
QLabel#section {{ color: {theme.ACCENT}; font-size: 8pt; font-weight: bold; }}
QLabel#muted {{ color: {theme.MUTED}; font-size: 8pt; }}
QLabel#value {{ color: {theme.FG}; font-family: Consolas; font-size: 10pt; font-weight: bold; }}
QLabel#hint {{ color: {theme.MUTED}; font-size: 7.5pt; }}
QPushButton {{
    background: {theme.PANEL_ALT}; color: {theme.FG};
    border: 1px solid {theme.BORDER}; border-radius: 4px; padding: 6px 8px;
}}
QPushButton:hover {{ background: #2b3242; }}
QPushButton#accent {{
    background: {theme.ACCENT}; color: #08111c; font-weight: bold;
    border: none; padding: 7px;
}}
QPushButton#accent:hover {{ background: #74bcff; }}
QComboBox {{
    background: {theme.PANEL_ALT}; color: {theme.FG};
    border: 1px solid {theme.BORDER}; border-radius: 4px; padding: 4px 6px;
}}
QComboBox QAbstractItemView {{ background: {theme.PANEL_ALT}; color: {theme.FG};
    selection-background-color: #2f6fb3; }}
QProgressBar {{ background: {theme.PANEL_ALT}; border: none; border-radius: 3px; }}
QProgressBar::chunk {{ background: {theme.ACCENT}; border-radius: 3px; }}
QCheckBox {{ color: {theme.FG}; font-size: 8.5pt; }}
QCheckBox::indicator {{
    width: 14px; height: 14px; border: 1px solid #3a4250; border-radius: 3px;
    background: {theme.PANEL_ALT};
}}
QCheckBox::indicator:hover {{ border-color: {theme.ACCENT}; }}
QCheckBox::indicator:checked {{ background: {theme.ACCENT}; border-color: {theme.ACCENT}; }}
QPlainTextEdit {{
    background: {theme.PLOT_BG}; color: {theme.MUTED};
    border: 1px solid {theme.BORDER}; font-family: Consolas; font-size: 8pt;
}}
QScrollBar:vertical {{ background: {theme.PLOT_BG}; width: 10px; }}
QScrollBar::handle:vertical {{ background: {theme.BORDER}; border-radius: 4px; }}
"""


_W = TypeVar("_W", bound=QWidget)


def _named(widget: _W, name: str) -> _W:
    """Задаёт objectName (селектор QSS).

    PyQt позволяет ``QWidget(objectName="x")`` в рантайме, но в стабах Qt
    такого параметра нет — поэтому выставляем имя явно.
    """
    widget.setObjectName(name)
    return widget


class Bridge(QObject):
    """Мост «наш Signal» → «нативный pyqtSignal».

    Все четыре канала объявлены как ``pyqtSignal``: Qt гарантирует доставку
    в поток владельца, а мы получаем типобезопасные слоты.
    """

    # arguments=() указан явно: в стабах PyQt6 у pyqtSignal этот параметр
    # объявлен обязательным, хотя в рантайме он необязателен.
    render = pyqtSignal(object, arguments=())        # ViewPayload
    memory = pyqtSignal(dict, arguments=())
    diag_event = pyqtSignal(str, str, arguments=())  # level, message
    report = pyqtSignal(str, str, arguments=())      # text, path


class ZoomCanvas(FigureCanvasQTAgg):
    """Canvas с зумом колесом мыши (без встроенного toolbar-пана)."""

    def __init__(self, figure: Figure, on_wheel) -> None:
        super().__init__(figure)
        self._on_wheel = on_wheel
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(420, 280)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.CrossCursor)

    def wheelEvent(self, event) -> None:  # noqa: N802 — API Qt
        delta = event.angleDelta().y()
        if delta:
            self._on_wheel(1 if delta > 0 else -1)
        event.accept()


class QtCompareWindow(QMainWindow):
    """Главное окно PyQt6-варианта."""

    def __init__(self, opts: SessionOptions) -> None:
        super().__init__()
        configure_matplotlib()
        self.opts = opts
        self.ctrl = SessionController(opts)
        self.bridge = Bridge()

        self._closing = False
        self._stress_on = bool(opts.auto_stress)
        self._labels: dict[str, QLabel] = {}

        self.setWindowTitle(theme.title_for(opts.toolkit))
        self.resize(1280, 780)
        self.setMinimumSize(1080, 660)
        self.setStyleSheet(QSS)

        self._build_ui()
        self._connect()

        self._timer_mem = QTimer(self)
        self._timer_mem.setInterval(opts.sample_interval_ms)
        self._timer_mem.timeout.connect(self._tick_memory)

        self._timer_stress = QTimer(self)
        self._timer_stress.setInterval(opts.stress_interval_ms)
        self._timer_stress.timeout.connect(self._tick_stress)

        QTimer.singleShot(60, self._start_session)

    # =================================================================== UI
    def _build_ui(self) -> None:
        root = _named(QWidget(), "root")
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        outer.addWidget(self._build_plot_area(), 1)
        outer.addWidget(self._build_side_panel(), 0)

    def _build_plot_area(self) -> QWidget:
        left = _named(QWidget(), "left")
        lay = QVBoxLayout(left)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        self.lbl_head = _named(QLabel("ожидание первого кадра…"), "head")
        f = QFont("Segoe UI", 9)
        f.setBold(True)
        self.lbl_head.setFont(f)
        lay.addWidget(self.lbl_head)

        frame = _named(QFrame(), "plotFrame")
        frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        inner = QVBoxLayout(frame)
        inner.setContentsMargins(2, 2, 2, 2)   # рамка 1 px + отступ 2 px
        inner.setSpacing(0)

        self.fig = Figure(figsize=(8.4, 5.2), dpi=100, facecolor=theme.PLOT_BG)
        self.ax = self.fig.add_axes(theme.AXES_RECT)
        style_axes(self.ax, bg=theme.PLOT_BG, grid=theme.GRID, fg=theme.MUTED,
                   title=GRAPH_TYPES[0])
        (self.line,) = self.ax.plot([], [], color=theme.LINE, linewidth=1.1,
                                    antialiased=True, clip_on=True, zorder=3)
        enforce_clipping(self.ax, [self.line])

        self.canvas = ZoomCanvas(self.fig, self._wheel_zoom)
        inner.addWidget(self.canvas)
        lay.addWidget(frame, 1)

        lay.addWidget(_named(QLabel(theme.HINT_CLIP), "hint"))
        return left

    def _build_side_panel(self) -> QWidget:
        side = _named(QWidget(), "side")
        side.setFixedWidth(380)
        lay = QVBoxLayout(side)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        body, grid = self._card(lay, theme.SEC_MEM)
        self._row(grid, 0, "RSS процесса", "rss")
        self._row(grid, 1, "Δ с начала", "delta")
        self._row(grid, 2, "Тренд", "slope")
        self._row(grid, 3, "Потоков", "threads")

        body, grid = self._card(lay, theme.SEC_RAM)
        self._row(grid, 0, "Занято / свободно", "ram")
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        grid.addWidget(self.bar, 1, 0, 1, 2)

        body, grid = self._card(lay, theme.SEC_GRAPH)
        self.combo = QComboBox()
        self.combo.addItems(list(GRAPH_TYPES))
        self.combo.currentTextChanged.connect(self._on_kind_changed)
        grid.addWidget(self.combo, 0, 0, 1, 2)
        self.lbl_kind_hint = _named(QLabel(describe(GRAPH_TYPES[0])), "hint")
        grid.addWidget(self.lbl_kind_hint, 1, 0, 1, 2)

        btn_show = _named(QPushButton(theme.BTN_SHOW), "accent")
        btn_show.clicked.connect(self._on_show_clicked)
        grid.addWidget(btn_show, 2, 0, 1, 2)

        body, grid = self._card(lay, theme.SEC_ZOOM)
        btn_in = QPushButton(theme.BTN_ZOOM_IN)
        btn_out = QPushButton(theme.BTN_ZOOM_OUT)
        btn_reset = QPushButton(theme.BTN_RESET)
        btn_in.clicked.connect(self._on_zoom_in)
        btn_out.clicked.connect(self._on_zoom_out)
        btn_reset.clicked.connect(self._on_reset_zoom)
        grid.addWidget(btn_in, 0, 0)
        grid.addWidget(btn_out, 0, 1)
        grid.addWidget(btn_reset, 0, 2)
        for col in range(3):
            grid.setColumnStretch(col, 1)
        self.lbl_zoom = _named(QLabel("Масштаб 1× · весь ряд"), "hint")
        grid.addWidget(self.lbl_zoom, 1, 0, 1, 3)

        body, grid = self._card(lay, theme.SEC_SESSION)
        self._row(grid, 0, "Время работы", "uptime")
        self._row(grid, 1, "Перерисовок", "renders")
        self._row(grid, 2, "Последний кадр", "render_ms")
        self._row(grid, 3, "Нарушений клипа", "clip")

        self.chk = QCheckBox(theme.CHK_STRESS)
        self.chk.setChecked(self._stress_on)
        self.chk.toggled.connect(self._on_stress_toggled)
        lay.addWidget(self.chk)

        btn_report = QPushButton(theme.BTN_REPORT)
        btn_report.clicked.connect(self._on_report_clicked)
        lay.addWidget(btn_report)

        btn_logs = QPushButton(theme.BTN_LOGS)
        btn_logs.clicked.connect(self._open_logs)
        lay.addWidget(btn_logs)

        lay.addSpacing(8)
        lay.addWidget(_named(QLabel(theme.SEC_DIAG), "section"))
        self.txt = QPlainTextEdit()
        self.txt.setReadOnly(True)
        self.txt.setMaximumBlockCount(600)   # жёсткий предел журнала
        lay.addWidget(self.txt, 1)
        return side

    def _card(self, parent_layout: QVBoxLayout, title: str):
        """Карточка-секция + сетка для «подпись/значение»."""
        block = QWidget()
        v = QVBoxLayout(block)
        v.setContentsMargins(0, 0, 0, 8)
        v.setSpacing(3)
        v.addWidget(_named(QLabel(title), "section"))

        body = _named(QFrame(), "card")
        grid = QGridLayout(body)
        grid.setContentsMargins(8, 6, 8, 6)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(2)
        grid.setColumnStretch(0, 1)
        v.addWidget(body)
        parent_layout.addWidget(block)
        return body, grid

    def _row(self, grid: QGridLayout, row: int, label: str, key: str) -> None:
        grid.addWidget(_named(QLabel(label), "muted"), row, 0)
        value = _named(QLabel("—"), "value")
        value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        grid.addWidget(value, row, 1)
        self._labels[key] = value

    # ============================================================= сигналы
    def _connect(self) -> None:
        # наш Signal → нативный сигнал Qt → слоты
        self.ctrl.render_request.connect(self.bridge.render.emit)
        self.ctrl.memory_update.connect(self.bridge.memory.emit)
        self.ctrl.diag.event_signal.connect(self.bridge.diag_event.emit)
        self.ctrl.report_ready.connect(
            lambda text, paths: self.bridge.report.emit(text, paths.get("report", ""))
        )
        self.bridge.render.connect(self._render)
        self.bridge.memory.connect(self._on_memory)
        self.bridge.diag_event.connect(self._on_event)
        self.bridge.report.connect(self._on_report_ready)

    @pyqtSlot()
    def _on_show_clicked(self) -> None:
        self.ctrl.show_graph(self.combo.currentText())

    @pyqtSlot()
    def _on_zoom_in(self) -> None:
        self.ctrl.zoom_in()

    @pyqtSlot()
    def _on_zoom_out(self) -> None:
        self.ctrl.zoom_out()

    @pyqtSlot()
    def _on_reset_zoom(self) -> None:
        self.ctrl.reset_zoom()

    @pyqtSlot(str)
    def _on_kind_changed(self, kind: str) -> None:
        self.lbl_kind_hint.setText(describe(kind))
        self.ctrl.show_graph(kind)

    def _wheel_zoom(self, direction: int) -> None:
        if direction > 0:
            self._on_zoom_in()
        else:
            self._on_zoom_out()

    @pyqtSlot(bool)
    def _on_stress_toggled(self, enabled: bool) -> None:
        self._stress_on = enabled
        if enabled:
            self._timer_stress.start()
            self._log("INFO", f"Стресс-тест включён: кадр каждые "
                              f"{self.opts.stress_interval_ms} мс")
        else:
            self._timer_stress.stop()
            self._log("INFO", "Стресс-тест выключен")

    @pyqtSlot()
    def _on_report_clicked(self) -> None:
        self.ctrl.build_report()
        self._on_report_ready(self.ctrl.report_text, self.ctrl.paths.get("report", ""))

    @pyqtSlot()
    def _open_logs(self) -> None:
        path = os.path.abspath(self.ctrl.log.dir)
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    # =========================================================== отрисовка
    @pyqtSlot(object)
    def _render(self, payload: ViewPayload) -> None:
        try:
            t0 = time.perf_counter()

            self.line.set_data(payload.x, payload.y)
            self.ax.set_xlim(payload.xlim)
            self.ax.set_ylim(payload.ylim)
            self.ax.set_title(f"{payload.kind} · {payload.points_visible:,} точек в кадре"
                              .replace(",", " "))
            enforce_clipping(self.ax, [self.line])
            violations = check_bounds(payload.xlim, payload.ylim, payload.x, payload.y)

            self.canvas.draw()
            ms = (time.perf_counter() - t0) * 1000.0

            self.ctrl.record_render(ms, payload.points_visible, violations)
            dpr = self.canvas.devicePixelRatioF()
            self.ctrl.opts.extra["canvas_px"] = (
                f"{int(self.canvas.width() * dpr)}×{int(self.canvas.height() * dpr)}"
            )
            self.lbl_head.setText(
                f"{payload.kind} · {payload.points_total} точек · зум {payload.zoom_label} "
                f"· в кадре {payload.points_visible} · {ms:.1f} мс"
            )
            self.lbl_zoom.setText(
                f"Масштаб {payload.zoom_label} · окно {payload.points_visible} из "
                f"{payload.points_total} точек"
            )
            self._labels["renders"].setText(str(self.ctrl.diag.renders))
            self._labels["render_ms"].setText(f"{ms:.1f} мс")
            self._labels["clip"].setText(str(self.ctrl.diag.clip_violations))
        except Exception:  # noqa: BLE001
            self.ctrl.diag.error("Ошибка отрисовки кадра")
            self._log("ERROR", traceback.format_exc(limit=3))

    # ============================================================== память
    @pyqtSlot(dict)
    def _on_memory(self, m: dict) -> None:
        self._labels["rss"].setText(f"{m['rss_mb']:.1f} МБ")
        self._labels["delta"].setText(f"{m['rss_delta_mb']:+.1f} МБ")
        self._labels["slope"].setText(f"{m['slope_mb_h']:+.2f} МБ/ч")
        self._labels["threads"].setText(str(m["threads"]))
        self._labels["ram"].setText(f"{m['sys_used_pct']:.1f} % · {m['sys_avail_gb']:.2f} ГБ своб.")
        self.bar.setValue(int(m["sys_used_pct"]))
        self._labels["uptime"].setText(theme.fmt_uptime(m["uptime"]))
        self._labels["renders"].setText(str(m["renders"]))
        self._labels["render_ms"].setText(f"{m['render_ms']:.1f} мс")

    # ========================================================= диагностика
    @pyqtSlot(str, str)
    def _on_event(self, level: str, message: str) -> None:
        self._log(level, message)

    @pyqtSlot(str, str)
    def _on_report_ready(self, text: str, path: str) -> None:
        self._log("HEAD", "── ИТОГОВЫЙ ОТЧЁТ " + "─" * 24)
        for line in text.splitlines():
            self._log("INFO", line)
        if path:
            self._log("OK", f"Сохранено: {path}")

    def _log(self, level: str, message: str) -> None:
        color = {"INFO": theme.MUTED, "OK": theme.OK, "WARN": theme.WARN,
                 "ERROR": theme.ERR, "HEAD": theme.ACCENT}.get(level, theme.MUTED)
        prefix = {"INFO": "·", "OK": "✓", "WARN": "!", "ERROR": "×", "HEAD": "»"}.get(level, "·")
        safe = html.escape(message).replace("\n", "<br>")
        bold = " font-weight:bold;" if level == "HEAD" else ""
        self.txt.appendHtml(
            f'<span style="color:{color};{bold}">{prefix} {safe}</span>'
        )

    # ============================================================== таймеры
    def _start_session(self) -> None:
        self.ctrl.start()
        self._timer_mem.start()
        if self._stress_on:
            self._timer_stress.start()
        if self.opts.duration_s > 0:
            self._log("INFO", f"Автостоп через {self.opts.duration_s:.0f} с")
            QTimer.singleShot(int(self.opts.duration_s * 1000), self.close)

    @pyqtSlot()
    def _tick_memory(self) -> None:
        if not self._closing:
            self.ctrl.tick()

    @pyqtSlot()
    def _tick_stress(self) -> None:
        if not self._closing:
            self.ctrl.stress_tick()

    # ============================================================== закрытие
    def closeEvent(self, event) -> None:  # noqa: N802 — API Qt
        if self._closing:
            event.accept()
            return
        self._closing = True

        self._timer_mem.stop()
        self._timer_stress.stop()

        try:
            text, paths = self.ctrl.finish()
            print("\n" + text)
            print(f"\nФайлы: {paths}")
        except Exception:  # noqa: BLE001
            print(traceback.format_exc())

        self.ctrl.shutdown()
        self.bridge.blockSignals(True)
        try:
            self.fig.clear()
        except Exception:  # noqa: BLE001
            pass
        event.accept()
        super().closeEvent(event)


# ===================================================================== CLI
def make_options(args) -> SessionOptions:
    return SessionOptions(
        toolkit="PyQt6",
        canvas_name="FigureCanvasQTAgg",
        log_dir=args.log_dir,
        n_points=args.points,
        sample_interval_ms=args.interval,
        stress_interval_ms=args.stress_interval,
        duration_s=args.duration,
        seed=args.seed,
        auto_stress=args.stress,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser("Matplotlib GUI Compare — PyQt6 + QtAgg").parse_args(argv)

    app = QApplication(sys.argv[:1])
    app.setApplicationName("Matplotlib GUI Compare — PyQt6")
    app.setFont(QFont("Segoe UI", 9))
    win = QtCompareWindow(make_options(args))
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
