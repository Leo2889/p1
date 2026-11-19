# ═══ gcompare/apps/wx_app.py ═══════════════════════════════════════════
# РОЛЬ        : приложение №3 — wxPython + FigureCanvasWxAgg (sizer-раскладка, GenButton)
# ИСПОЛЬЗУЮТ  : запускается как python -m gcompare.apps.wx_app
# СМОТРИ ТАКЖЕ: ARCHITECTURE.md, §5
# ──────────────────────────────────────────────────────────────────────
"""Приложение №3 — wxPython + ``matplotlib.backends.backend_wxagg``.

Особенности реализации
----------------------
* **Жёсткая рамка.** Canvas лежит в ``wx.Panel`` с ``wx.SIMPLE_BORDER``
  и добавляется в ``wx.BoxSizer`` с ``wx.EXPAND``. Родной для wx
  ``wxAUI``-подобный менеджер раскладки не даёт виджету выйти за панель,
  а сам canvas рисует только в своём клиентском прямоугольнике.
* **События.** Кнопки используют ``Bind(wx.EVT_BUTTON, slot)`` — это
  канонический аналог Slot/Signal в wxPython; шина контроллера
  подключается к методам-слотам ``_render/_on_memory``.
* **Без утечек.** Один ``Line2D`` + ``set_data``; таймеры
  ``wx.Timer`` останавливаются в ``EVT_CLOSE``, журнал обрезается
  по длине, ``Figure`` очищается перед ``Destroy``.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import traceback

import matplotlib

matplotlib.use("WXAgg", force=True)

from matplotlib.backends.backend_wxagg import FigureCanvasWxAgg  # noqa: E402
from matplotlib.figure import Figure                             # noqa: E402

import wx                                                       # noqa: E402
from wx.lib.buttons import GenButton                            # noqa: E402

from ..core import theme                                        # noqa: E402
from ..core.cli import build_parser                              # noqa: E402
from ..core.data import GRAPH_TYPES, describe                     # noqa: E402
from ..core.model import ViewPayload                              # noqa: E402
from ..core.session import SessionController, SessionOptions       # noqa: E402
from ..core.view_utils import (
    check_bounds, configure_matplotlib, enforce_clipping, style_axes,
)  # noqa: E402

LOG_MAX_CHARS = 60_000   # жёсткий предел журнала диагностики


def _font(size: int = 9, *, bold: bool = False, mono: bool = False) -> wx.Font:
    family = wx.FONTFAMILY_TELETYPE if mono else wx.FONTFAMILY_DEFAULT
    weight = wx.FONTWEIGHT_BOLD if bold else wx.FONTWEIGHT_NORMAL
    return wx.Font(size, family, wx.FONTSTYLE_NORMAL, weight)


class WxCompareFrame(wx.Frame):
    """Главное окно wxPython-варианта."""

    def __init__(self, opts: SessionOptions) -> None:
        super().__init__(None, title=theme.title_for(opts.toolkit), size=(1280, 780))
        configure_matplotlib()
        self.opts = opts
        self.ctrl = SessionController(opts)

        self._closing = False
        self._stress_on = bool(opts.auto_stress)
        self._labels: dict[str, wx.StaticText] = {}
        self._call_later: wx.CallLater | None = None

        self.SetMinSize((1080, 660))
        self.SetBackgroundColour(theme.BG)
        self._build_ui()
        self._connect_events()

        self._timer_mem = wx.Timer(self)
        self._timer_stress = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._tick_memory, self._timer_mem)
        self.Bind(wx.EVT_TIMER, self._tick_stress, self._timer_stress)
        self.Bind(wx.EVT_CLOSE, self._on_close)

        wx.CallLater(60, self._start_session)
    # =================================================================== UI
    def _build_ui(self) -> None:
        root = wx.Panel(self)
        root.SetBackgroundColour(theme.BG)
        outer = wx.BoxSizer(wx.HORIZONTAL)

        outer.Add(self._build_plot_area(root), 1, wx.EXPAND | wx.ALL, 8)
        outer.Add(self._build_side_panel(root), 0, wx.EXPAND | wx.TOP | wx.BOTTOM | wx.RIGHT, 8)
        root.SetSizer(outer)

    def _build_plot_area(self, parent: wx.Window) -> wx.Window:
        left = wx.Panel(parent)
        left.SetBackgroundColour(theme.BG)
        lay = wx.BoxSizer(wx.VERTICAL)

        self.lbl_head = wx.StaticText(left, label="ожидание первого кадра…")
        self.lbl_head.SetForegroundColour(theme.FG)
        self.lbl_head.SetFont(_font(9, bold=True))
        lay.Add(self.lbl_head, 0, wx.BOTTOM, 6)

        # --- Жёсткая рамка графика -------------------------------------
        self.plot_panel = wx.Panel(left, style=wx.SIMPLE_BORDER)
        self.plot_panel.SetBackgroundColour(theme.PLOT_BG)
        plot_sizer = wx.BoxSizer(wx.VERTICAL)

        self.fig = Figure(figsize=(8.4, 5.2), dpi=100, facecolor=theme.PLOT_BG)
        self.ax = self.fig.add_axes(theme.AXES_RECT)
        style_axes(self.ax, bg=theme.PLOT_BG, grid=theme.GRID, fg=theme.MUTED,
                   title=GRAPH_TYPES[0])
        (self.line,) = self.ax.plot([], [], color=theme.LINE, linewidth=1.1,
                                    antialiased=True, clip_on=True, zorder=3)
        enforce_clipping(self.ax, [self.line])

        self.canvas = FigureCanvasWxAgg(self.plot_panel, -1, self.fig)
        self.canvas.SetMinSize((420, 280))
        plot_sizer.Add(self.canvas, 1, wx.EXPAND | wx.ALL, 2)   # отступ 2 px внутри рамки
        self.plot_panel.SetSizer(plot_sizer)
        lay.Add(self.plot_panel, 1, wx.EXPAND)

        hint = wx.StaticText(left, label=theme.HINT_CLIP)
        hint.SetForegroundColour(theme.MUTED)
        hint.SetFont(_font(8))
        lay.Add(hint, 0, wx.TOP, 6)

        left.SetSizer(lay)
        return left

    def _build_side_panel(self, parent: wx.Window) -> wx.Window:
        side = wx.Panel(parent)
        side.SetBackgroundColour(theme.BG)
        side.SetMinSize((380, -1))
        lay = wx.BoxSizer(wx.VERTICAL)

        block, grid, card = self._card(side, theme.SEC_MEM)
        self._row(grid, card, 0, "RSS процесса", "rss")
        self._row(grid, card, 1, "Δ с начала", "delta")
        self._row(grid, card, 2, "Тренд", "slope")
        self._row(grid, card, 3, "Потоков", "threads")
        lay.Add(block, 0, wx.EXPAND | wx.BOTTOM, 8)

        block, grid, card = self._card(side, theme.SEC_RAM)
        self._row(grid, card, 0, "Занято / свободно", "ram")
        self.gauge = wx.Gauge(card, range=100, size=(-1, 6), style=wx.GA_HORIZONTAL)
        grid.Add(self.gauge, (1, 0), (1, 2), wx.EXPAND | wx.TOP, 4)
        lay.Add(block, 0, wx.EXPAND | wx.BOTTOM, 8)

        block, grid, card = self._card(side, theme.SEC_GRAPH)
        self.combo = wx.ComboBox(card, choices=list(GRAPH_TYPES), style=wx.CB_READONLY)
        self.combo.SetSelection(0)
        grid.Add(self.combo, (0, 0), (1, 2), wx.EXPAND)
        self.lbl_kind_hint = wx.StaticText(card, label=describe(GRAPH_TYPES[0]))
        self.lbl_kind_hint.SetForegroundColour(theme.MUTED)
        self.lbl_kind_hint.SetFont(_font(8))
        grid.Add(self.lbl_kind_hint, (1, 0), (1, 2), wx.TOP, 4)

        self.btn_show = self._button(card, theme.BTN_SHOW, accent=True)
        grid.Add(self.btn_show, (2, 0), (1, 2), wx.EXPAND | wx.TOP, 6)
        lay.Add(block, 0, wx.EXPAND | wx.BOTTOM, 8)

        block, grid, card = self._card(side, theme.SEC_ZOOM)
        self.btn_in = self._button(card, theme.BTN_ZOOM_IN)
        self.btn_out = self._button(card, theme.BTN_ZOOM_OUT)
        self.btn_reset = self._button(card, theme.BTN_RESET)
        for i, btn in enumerate((self.btn_in, self.btn_out, self.btn_reset)):
            grid.Add(btn, (0, i), (1, 1), wx.EXPAND)
            if i:                       # колонка 0 уже сделана растягиваемой в _card
                grid.AddGrowableCol(i, 1)
        self.lbl_zoom = wx.StaticText(card, label="Масштаб 1× · весь ряд")
        self.lbl_zoom.SetForegroundColour(theme.MUTED)
        self.lbl_zoom.SetFont(_font(8))
        grid.Add(self.lbl_zoom, (1, 0), (1, 3), wx.TOP, 5)
        lay.Add(block, 0, wx.EXPAND | wx.BOTTOM, 8)

        block, grid, card = self._card(side, theme.SEC_SESSION)
        self._row(grid, card, 0, "Время работы", "uptime")
        self._row(grid, card, 1, "Перерисовок", "renders")
        self._row(grid, card, 2, "Последний кадр", "render_ms")
        self._row(grid, card, 3, "Нарушений клипа", "clip")
        lay.Add(block, 0, wx.EXPAND | wx.BOTTOM, 8)

        self.chk_stress = wx.CheckBox(side, label=theme.CHK_STRESS)
        self.chk_stress.SetValue(self._stress_on)
        self.chk_stress.SetForegroundColour(theme.FG)
        lay.Add(self.chk_stress, 0, wx.BOTTOM, 4)

        self.btn_report = self._button(side, theme.BTN_REPORT)
        lay.Add(self.btn_report, 0, wx.EXPAND | wx.BOTTOM, 4)

        self.btn_logs = self._button(side, theme.BTN_LOGS)
        lay.Add(self.btn_logs, 0, wx.EXPAND | wx.BOTTOM, 10)

        sec = wx.StaticText(side, label=theme.SEC_DIAG)
        sec.SetForegroundColour(theme.ACCENT)
        sec.SetFont(_font(8, bold=True))
        lay.Add(sec, 0, wx.BOTTOM, 4)

        self.txt = wx.TextCtrl(
            side, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2 | wx.BORDER_SIMPLE,
        )
        self.txt.SetBackgroundColour(theme.PLOT_BG)
        self.txt.SetForegroundColour(theme.MUTED)
        self.txt.SetFont(_font(8, mono=True))
        lay.Add(self.txt, 1, wx.EXPAND)

        side.SetSizer(lay)
        self._side = side
        return side

    def _button(self, parent: wx.Window, label: str, *, accent: bool = False) -> GenButton:
        """Кнопка с собственным рисованием: нативные wx.Button игнорируют цвета."""
        btn = GenButton(parent, -1, label)
        btn.SetBezelWidth(0)
        btn.SetBackgroundColour(theme.ACCENT if accent else theme.PANEL_ALT)
        btn.SetForegroundColour("#08111c" if accent else theme.FG)
        btn.SetFont(_font(9, bold=accent))
        return btn

    def _card(self, parent: wx.Window, title: str):
        """Секция: заголовок + карточка с сеткой «подпись/значение»."""
        block = wx.Panel(parent)
        block.SetBackgroundColour(theme.BG)
        v = wx.BoxSizer(wx.VERTICAL)

        lbl = wx.StaticText(block, label=title)
        lbl.SetForegroundColour(theme.ACCENT)
        lbl.SetFont(_font(8, bold=True))
        v.Add(lbl, 0, wx.BOTTOM, 3)

        card = wx.Panel(block)
        card.SetBackgroundColour(theme.PANEL_ALT)
        grid = wx.GridBagSizer(vgap=2, hgap=6)
        grid.AddGrowableCol(0, 1)   # одна колонка «подпись» тянется всегда
        wrap = wx.BoxSizer(wx.VERTICAL)
        wrap.Add(grid, 1, wx.EXPAND | wx.ALL, 8)
        card.SetSizer(wrap)
        v.Add(card, 0, wx.EXPAND)

        block.SetSizer(v)
        return block, grid, card

    def _row(self, grid: wx.GridBagSizer, card: wx.Window, row: int,
             label: str, key: str) -> None:
        lbl = wx.StaticText(card, label=label)
        lbl.SetForegroundColour(theme.MUTED)
        lbl.SetFont(_font(8))
        val = wx.StaticText(card, label="—", style=wx.ALIGN_RIGHT)
        val.SetForegroundColour(theme.FG)
        val.SetFont(_font(10, bold=True, mono=True))
        # Фиксированная ширина: StaticText не пересчитывает размер при
        # смене текста, иначе «42.1 мс» обрезалось бы до «42».
        val.SetMinSize((145, -1))
        grid.Add(lbl, (row, 0), (1, 1), wx.ALIGN_CENTER_VERTICAL)
        grid.Add(val, (row, 1), (1, 1), wx.ALIGN_RIGHT | wx.ALIGN_CENTER_VERTICAL)
        self._labels[key] = val

    # ============================================================= события
    def _connect_events(self) -> None:
        # wx-события → слоты
        self.btn_show.Bind(wx.EVT_BUTTON, self._on_show)
        self.btn_in.Bind(wx.EVT_BUTTON, self._on_zoom_in)
        self.btn_out.Bind(wx.EVT_BUTTON, self._on_zoom_out)
        self.btn_reset.Bind(wx.EVT_BUTTON, self._on_reset_zoom)
        self.btn_report.Bind(wx.EVT_BUTTON, self._on_report)
        self.btn_logs.Bind(wx.EVT_BUTTON, self._on_open_logs)
        self.combo.Bind(wx.EVT_COMBOBOX, self._on_kind)
        self.chk_stress.Bind(wx.EVT_CHECKBOX, self._on_stress_toggled)
        self.canvas.Bind(wx.EVT_MOUSEWHEEL, self._on_wheel)
        # шина контроллера → слоты
        self.ctrl.render_request.connect(self._render)
        self.ctrl.memory_update.connect(self._on_memory)
        self.ctrl.diag.event_signal.connect(self._on_event)
        self.ctrl.report_ready.connect(self._on_report_ready)

    def _on_show(self, _evt) -> None:
        self.ctrl.show_graph(self.combo.GetStringSelection())

    def _on_zoom_in(self, _evt=None) -> None:
        self.ctrl.zoom_in()

    def _on_zoom_out(self, _evt=None) -> None:
        self.ctrl.zoom_out()

    def _on_reset_zoom(self, _evt=None) -> None:
        self.ctrl.reset_zoom()

    def _on_kind(self, _evt) -> None:
        kind = self.combo.GetStringSelection()
        self.lbl_kind_hint.SetLabel(describe(kind))
        self.ctrl.show_graph(kind)

    def _on_wheel(self, evt) -> None:
        if evt.GetWheelRotation() > 0:
            self._on_zoom_in()
        else:
            self._on_zoom_out()

    def _on_stress_toggled(self, _evt) -> None:
        self._stress_on = self.chk_stress.GetValue()
        if self._stress_on:
            self._timer_stress.Start(self.opts.stress_interval_ms)
            self._log("INFO", f"Стресс-тест включён: кадр каждые "
                              f"{self.opts.stress_interval_ms} мс")
        else:
            self._timer_stress.Stop()
            self._log("INFO", "Стресс-тест выключен")

    def _on_report(self, _evt) -> None:
        self.ctrl.build_report()
        self._on_report_ready(self.ctrl.report_text, self.ctrl.paths.get("report", ""))

    def _on_open_logs(self, _evt) -> None:
        path = os.path.abspath(self.ctrl.log.dir)
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception:  # noqa: BLE001
            self._log("WARN", f"Не удалось открыть каталог: {path}")

    # =========================================================== отрисовка
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
            size = self.canvas.GetClientSize()
            self.ctrl.opts.extra["canvas_px"] = f"{size.width}×{size.height}"
            self.lbl_head.SetLabel(
                f"{payload.kind} · {payload.points_total} точек · зум {payload.zoom_label} "
                f"· в кадре {payload.points_visible} · {ms:.1f} мс"
            )
            self.lbl_zoom.SetLabel(
                f"Масштаб {payload.zoom_label} · окно {payload.points_visible} из "
                f"{payload.points_total} точек"
            )
            self._set("renders", str(self.ctrl.diag.renders))
            self._set("render_ms", f"{ms:.1f} мс")
            self._set("clip", str(self.ctrl.diag.clip_violations))
        except Exception:  # noqa: BLE001
            self.ctrl.diag.error("Ошибка отрисовки кадра")
            self._log("ERROR", traceback.format_exc(limit=3))

    # ============================================================== память
    def _on_memory(self, m: dict) -> None:
        self._set("rss", f"{m['rss_mb']:.1f} МБ")
        self._set("delta", f"{m['rss_delta_mb']:+.1f} МБ")
        self._set("slope", f"{m['slope_mb_h']:+.2f} МБ/ч")
        self._set("threads", str(m["threads"]))
        self._set("ram", f"{m['sys_used_pct']:.1f}% · {m['sys_avail_gb']:.2f} ГБ")
        self.gauge.SetValue(int(m["sys_used_pct"]))
        self._set("uptime", theme.fmt_uptime(m["uptime"]))
        self._set("renders", str(m["renders"]))
        self._set("render_ms", f"{m['render_ms']:.1f} мс")
        # Раз в секунду (не на каждый кадр) — выравниваем панель под новый текст.
        self._side.Layout()

    def _set(self, key: str, text: str) -> None:
        widget = self._labels.get(key)
        if widget is not None and widget:
            widget.SetLabel(text)

    # ========================================================= диагностика
    def _on_event(self, level: str, message: str) -> None:
        self._log(level, message)

    def _on_report_ready(self, text: str, path: str) -> None:
        self._log("HEAD", "── ИТОГОВЫЙ ОТЧЁТ " + "─" * 24)
        for line in text.splitlines():
            self._log("INFO", line)
        if path:
            self._log("OK", f"Сохранено: {path}")

    def _log(self, level: str, message: str) -> None:
        color = wx.Colour(
            {"INFO": theme.MUTED, "OK": theme.OK, "WARN": theme.WARN,
             "ERROR": theme.ERR, "HEAD": theme.ACCENT}.get(level, theme.MUTED)
        )
        prefix = {"INFO": "·", "OK": "✓", "WARN": "!", "ERROR": "×", "HEAD": "»"}.get(level, "·")
        self.txt.SetDefaultStyle(wx.TextAttr(color))
        self.txt.AppendText(f"{prefix} {message}\n")
        # Обрезка журнала по символам: TextCtrl не умеет ограничивать строки.
        if self.txt.GetLastPosition() > LOG_MAX_CHARS:
            self.txt.Remove(0, self.txt.GetLastPosition() - int(LOG_MAX_CHARS * 0.7))

    # ============================================================== таймеры
    def _start_session(self) -> None:
        self.ctrl.start()
        self._timer_mem.Start(self.opts.sample_interval_ms)
        if self._stress_on:
            self._timer_stress.Start(self.opts.stress_interval_ms)
        if self.opts.duration_s > 0:
            self._log("INFO", f"Автостоп через {self.opts.duration_s:.0f} с")
            self._call_later = wx.CallLater(int(self.opts.duration_s * 1000), self.Close)

    def _tick_memory(self, _evt=None) -> None:
        if not self._closing:
            self.ctrl.tick()

    def _tick_stress(self, _evt=None) -> None:
        if not self._closing:
            self.ctrl.stress_tick()

    # ============================================================== закрытие
    def _on_close(self, _evt) -> None:
        if self._closing:
            return
        self._closing = True

        self._timer_mem.Stop()
        self._timer_stress.Stop()
        if self._call_later is not None:
            self._call_later.Stop()
            self._call_later = None

        try:
            text, paths = self.ctrl.finish()
            print("\n" + text)
            print(f"\nФайлы: {paths}")
        except Exception:  # noqa: BLE001
            print(traceback.format_exc())

        self.ctrl.shutdown()
        try:
            self.fig.clear()
        except Exception:  # noqa: BLE001
            pass
        self.Destroy()


# ===================================================================== CLI
def make_options(args) -> SessionOptions:
    return SessionOptions(
        toolkit="wxPython",
        canvas_name="FigureCanvasWxAgg",
        log_dir=args.log_dir,
        n_points=args.points,
        sample_interval_ms=args.interval,
        stress_interval_ms=args.stress_interval,
        duration_s=args.duration,
        seed=args.seed,
        auto_stress=args.stress,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser("Matplotlib GUI Compare — wxPython + WXAgg").parse_args(argv)
    app = wx.App(False)
    frame = WxCompareFrame(make_options(args))
    frame.Show()
    app.MainLoop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
