# ═══ gcompare/apps/tk_app.py ═══════════════════════════════════════════
# РОЛЬ        : приложение №1 — Tkinter + FigureCanvasTkAgg (жёсткая рамка через place)
# ИСПОЛЬЗУЮТ  : запускается как python -m gcompare.apps.tk_app
# СМОТРИ ТАКЖЕ: ARCHITECTURE.md, §5
# ──────────────────────────────────────────────────────────────────────
"""Приложение №1 — Tkinter + ``matplotlib.backends.backend_tkagg``.

Особенности реализации
----------------------
* **Жёсткая рамка.** Canvas не «плавает» в layout: виджет matplotlib
  размещается через ``place`` с отрицательной дельтой
  (``relwidth=1`` + ``width=-4``), то есть его размер ВСЕГДА равен
  «рамка минус 2 px с каждой стороны». Никакой менеджер геометрии
  не может растянуть его больше родителя.
* **Клиппинг.** ``Line2D.set_clip_path(ax.patch)`` + ``clip_box``
  включаются явно, а после каждого кадра выполняется численная проверка
  ``check_bounds()``; нарушения попадают в отчёт.
* **Без утечек.** Объект ``Line2D`` создаётся ОДИН раз; обновление идёт
  через ``set_data``. Таймеры ``after`` отменяются при закрытии,
  сигналы очищаются, ``Figure`` уничтожается.
"""

from __future__ import annotations

import os
import sys
import time
import traceback

# tkinter — часть стандартной поставки python.org, но некоторые
# «урезанные» сборки (в т.ч. портативные) идут без Tcl/Tk.
# Проверяем до переключения бэкенда matplotlib и подсказываем решение.
try:
    import tkinter as tk                  # noqa: E402
    from tkinter import ttk               # noqa: E402
except ModuleNotFoundError as exc:        # pragma: no cover
    raise SystemExit(
        "В этом интерпретаторе нет tkinter (сборка Python без Tcl/Tk).\n"
        "Что делать:\n"
        "  1) в VS Code выбрать интерпретатор python.org (Python 3.12 с python.org);\n"
        "     или  winget install Python.Python.3.12\n"
        "  2) либо запустить другой вариант: python -m gcompare.apps.qt_app\n"
        "     или python -m gcompare.apps.wx_app\n"
        f"Текущий интерпретатор: {sys.executable}"
    ) from exc

import matplotlib

matplotlib.use("TkAgg", force=True)

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg  # noqa: E402
from matplotlib.figure import Figure                              # noqa: E402

from ..core import theme                                          # noqa: E402
from ..core.cli import build_parser                               # noqa: E402
from ..core.data import GRAPH_TYPES, describe                     # noqa: E402
from ..core.model import ViewPayload                              # noqa: E402
from ..core.session import SessionController, SessionOptions       # noqa: E402
from ..core.signals import Signal, SignalGroup                     # noqa: E402
from ..core.view_utils import (
    check_bounds, configure_matplotlib, enforce_clipping, style_axes,
)  # noqa: E402


class AppSignals(SignalGroup):
    """Каналы событий приложения — аналог Slot/Signal в Qt.

    Кнопки не вызывают бизнес-логику напрямую: они лишь ``emit``-ят сигнал,
    а подписанные слоты выполняют работу. Это позволяет менять поведение
    без правки виджетов и одинаково выглядит во всех трёх библиотеках.
    """

    show_graph: Signal      # (kind: str | None)
    zoom_in: Signal
    zoom_out: Signal
    reset_zoom: Signal
    report: Signal
    stress_changed: Signal  # (bool)
    shutdown: Signal

    def __init__(self) -> None:
        super().__init__(
            show_graph=Signal("show_graph"),      # (kind: str | None)
            zoom_in=Signal("zoom_in"),
            zoom_out=Signal("zoom_out"),
            reset_zoom=Signal("reset_zoom"),
            report=Signal("report"),
            stress_changed=Signal("stress_changed"),   # (bool)
            shutdown=Signal("shutdown"),
        )


class TkCompareApp(tk.Tk):
    """Главное окно Tkinter-варианта."""

    def __init__(self, opts: SessionOptions) -> None:
        super().__init__()
        configure_matplotlib()
        self.opts = opts
        self.ctrl = SessionController(opts)
        self.signals = AppSignals()

        self._closing = False
        self._job_mem: str | None = None
        self._job_stress: str | None = None
        self._job_deadline: str | None = None
        self._stress_on = bool(opts.auto_stress)

        self.title(theme.title_for(opts.toolkit))
        self.configure(bg=theme.BG)
        self.geometry("1280x780")
        self.minsize(1080, 660)
        self.protocol("WM_DELETE_WINDOW", self.close)

        self._init_vars()
        self._build_styles()
        self._build_layout()
        self._connect()

        self.after(60, self._start_session)

    # =================================================================== UI
    def _init_vars(self) -> None:
        self.var_head = tk.StringVar(value="ожидание первого кадра…")
        self.var_kind_hint = tk.StringVar(value=describe(GRAPH_TYPES[0]))
        self.var_kind = tk.StringVar(value=GRAPH_TYPES[0])

        self.var_rss = tk.StringVar(value="— МБ")
        self.var_delta = tk.StringVar(value="— МБ")
        self.var_slope = tk.StringVar(value="— МБ/ч")
        self.var_threads = tk.StringVar(value="—")
        self.var_ram = tk.StringVar(value="—")
        self.var_ram_pct = tk.DoubleVar(value=0.0)

        self.var_zoom = tk.StringVar(value="1× · весь ряд")
        self.var_uptime = tk.StringVar(value="00:00:00")
        self.var_renders = tk.StringVar(value="0")
        self.var_render_ms = tk.StringVar(value="— мс")
        self.var_clip = tk.StringVar(value="0")
        self.var_stress = tk.BooleanVar(value=self._stress_on)

    def _build_styles(self) -> None:
        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure(".", background=theme.PANEL, foreground=theme.FG, font=("Segoe UI", 9))
        st.configure("TFrame", background=theme.PANEL)
        st.configure("Bg.TFrame", background=theme.BG)
        st.configure("Card.TFrame", background=theme.PANEL_ALT, relief="flat")
        st.configure("TLabel", background=theme.PANEL, foreground=theme.FG)
        st.configure("Bg.TLabel", background=theme.BG, foreground=theme.FG)
        st.configure("Head.TLabel", background=theme.BG, foreground=theme.FG,
                     font=("Segoe UI", 9, "bold"))
        st.configure("Hint.TLabel", background=theme.BG, foreground=theme.MUTED,
                     font=("Segoe UI", 8))
        st.configure("Section.TLabel", background=theme.PANEL, foreground=theme.ACCENT,
                     font=("Segoe UI", 8, "bold"))
        st.configure("CardLabel.TLabel", background=theme.PANEL_ALT, foreground=theme.MUTED,
                     font=("Segoe UI", 8))
        st.configure("CardValue.TLabel", background=theme.PANEL_ALT, foreground=theme.FG,
                     font=("Consolas", 10, "bold"))
        st.configure("CardHint.TLabel", background=theme.PANEL_ALT, foreground=theme.MUTED,
                     font=("Segoe UI", 7))
        st.configure("TButton", background=theme.PANEL_ALT, foreground=theme.FG,
                     borderwidth=0, focusthickness=0, padding=(8, 5))
        st.map("TButton", background=[("active", "#2b3242")])
        st.configure("Accent.TButton", background=theme.ACCENT, foreground="#08111c",
                     font=("Segoe UI", 9, "bold"), padding=(8, 6))
        st.map("Accent.TButton", background=[("active", "#74bcff")])
        st.configure("TCheckbutton", background=theme.PANEL, foreground=theme.FG)
        st.map("TCheckbutton", background=[("active", theme.PANEL)])
        st.configure("TCombobox", fieldbackground=theme.PANEL_ALT, background=theme.PANEL_ALT,
                     foreground=theme.FG, arrowcolor=theme.FG, borderwidth=0)
        # readonly-состояние рисуется отдельной картой, иначе поле остаётся белым
        st.map("TCombobox",
               fieldbackground=[("readonly", theme.PANEL_ALT)],
               foreground=[("readonly", theme.FG)],
               selectbackground=[("readonly", theme.PANEL_ALT)],
               selectforeground=[("readonly", theme.FG)],
               background=[("readonly", theme.PANEL_ALT)])
        # цвета выпадающего списка задаются только через option database
        self.option_add("*TCombobox*Listbox.background", theme.PANEL_ALT)
        self.option_add("*TCombobox*Listbox.foreground", theme.FG)
        self.option_add("*TCombobox*Listbox.selectBackground", "#2f6fb3")
        self.option_add("*TCombobox*Listbox.selectForeground", theme.FG)
        st.configure("TProgressbar", background=theme.ACCENT, troughcolor=theme.PANEL_ALT,
                     borderwidth=0, thickness=6)

    def _build_layout(self) -> None:
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=0)

        self._build_plot_area()
        self._build_side_panel()

    # ------------------------------------------------------- левая колонка
    def _build_plot_area(self) -> None:
        left = ttk.Frame(self, style="Bg.TFrame")
        left.grid(row=0, column=0, sticky="nsew", padx=(10, 6), pady=10)
        left.grid_rowconfigure(1, weight=1)
        left.grid_columnconfigure(0, weight=1)

        ttk.Label(left, textvariable=self.var_head, style="Head.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 6))
        ttk.Label(left, text=theme.HINT_CLIP, style="Hint.TLabel").grid(
            row=2, column=0, sticky="w", pady=(6, 0))

        # --- Жёсткая рамка графика -------------------------------------
        self.plot_shell = tk.Frame(
            left, bg=theme.PLOT_BG, bd=0,
            highlightthickness=1, highlightbackground=theme.BORDER,
            highlightcolor=theme.BORDER,
        )
        self.plot_shell.grid(row=1, column=0, sticky="nsew")
        self.plot_shell.grid_propagate(False)   # размер задаёт grid, не дети

        self.fig = Figure(figsize=(8.4, 5.2), dpi=100, facecolor=theme.PLOT_BG)
        # Явный rect внутри figure: axes не может выйти за её пределы.
        self.ax = self.fig.add_axes(theme.AXES_RECT)
        style_axes(self.ax, bg=theme.PLOT_BG, grid=theme.GRID, fg=theme.MUTED,
                   title=GRAPH_TYPES[0])
        # Единственный Line2D на всё время жизни окна (set_data, не plot).
        (self.line,) = self.ax.plot(
            [], [], color=theme.LINE, linewidth=1.1, antialiased=True,
            clip_on=True, zorder=3, label="signal",
        )
        enforce_clipping(self.ax, [self.line])

        self.canvas = FigureCanvasTkAgg(self.fig, master=self.plot_shell)
        self.canvas_widget = self.canvas.get_tk_widget()
        self.canvas_widget.configure(bg=theme.PLOT_BG, highlightthickness=0, bd=0)
        # relwidth=1 + width=-4 → ровно «родитель − 2 px с каждой стороны».
        self.canvas_widget.place(x=2, y=2, relwidth=1.0, relheight=1.0, width=-4, height=-4)
        self.canvas_widget.bind("<MouseWheel>", self._on_wheel)
        self.canvas_widget.bind("<Button-4>", lambda _e: self.signals.zoom_in.emit())
        self.canvas_widget.bind("<Button-5>", lambda _e: self.signals.zoom_out.emit())

    # ------------------------------------------------------- правая колонка
    def _build_side_panel(self) -> None:
        side = ttk.Frame(self, style="TFrame", width=380)
        side.grid(row=0, column=1, sticky="nsew", padx=(6, 10), pady=10)
        # Дети панели упакованы pack-ом, поэтому наследование размера
        # запрещает именно pack_propagate (grid_propagate здесь не действует).
        side.pack_propagate(False)   # фиксированная ширина панели

        # --- память процесса ------------------------------------------
        card = self._card(side, theme.SEC_MEM)
        self._row(card, 0, "RSS процесса", self.var_rss)
        self._row(card, 1, "Δ с начала", self.var_delta)
        self._row(card, 2, "Тренд", self.var_slope)
        self._row(card, 3, "Потоков", self.var_threads)

        # --- системная RAM --------------------------------------------
        card = self._card(side, theme.SEC_RAM)
        self._row(card, 0, "Занято / свободно", self.var_ram)
        bar = ttk.Progressbar(card, maximum=100.0, variable=self.var_ram_pct,
                              style="TProgressbar")
        bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(3, 0))

        # --- график ----------------------------------------------------
        card = self._card(side, theme.SEC_GRAPH)
        combo = ttk.Combobox(card, textvariable=self.var_kind, values=list(GRAPH_TYPES),
                             state="readonly")
        combo.grid(row=0, column=0, columnspan=2, sticky="ew")
        combo.bind("<<ComboboxSelected>>", lambda _e: self._on_kind_changed())
        ttk.Label(card, textvariable=self.var_kind_hint, style="CardHint.TLabel").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(3, 5))
        ttk.Button(card, text=theme.BTN_SHOW, style="Accent.TButton",
                   command=lambda: self.signals.show_graph.emit(None)).grid(
            row=2, column=0, columnspan=2, sticky="ew")

        # --- масштаб ---------------------------------------------------
        card = self._card(side, theme.SEC_ZOOM)
        ttk.Button(card, text=theme.BTN_ZOOM_IN,
                   command=lambda: self.signals.zoom_in.emit()).grid(
            row=0, column=0, sticky="ew", padx=(0, 3))
        ttk.Button(card, text=theme.BTN_ZOOM_OUT,
                   command=lambda: self.signals.zoom_out.emit()).grid(
            row=0, column=1, sticky="ew", padx=3)
        ttk.Button(card, text=theme.BTN_RESET,
                   command=lambda: self.signals.reset_zoom.emit()).grid(
            row=0, column=2, sticky="ew", padx=(3, 0))
        for col in range(3):
            card.grid_columnconfigure(col, weight=1)
        ttk.Label(card, textvariable=self.var_zoom, style="CardHint.TLabel").grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(5, 0))

        # --- сессия ----------------------------------------------------
        card = self._card(side, theme.SEC_SESSION)
        self._row(card, 0, "Время работы", self.var_uptime)
        self._row(card, 1, "Перерисовок", self.var_renders)
        self._row(card, 2, "Последний кадр", self.var_render_ms)
        self._row(card, 3, "Нарушений клипа", self.var_clip)

        ttk.Checkbutton(side, text=theme.CHK_STRESS, variable=self.var_stress,
                        command=lambda: self.signals.stress_changed.emit(self.var_stress.get())
                        ).pack(fill="x", pady=(2, 4))
        ttk.Button(side, text=theme.BTN_REPORT,
                   command=lambda: self.signals.report.emit()).pack(fill="x", pady=2)
        ttk.Button(side, text=theme.BTN_LOGS,
                   command=self._open_logs).pack(fill="x", pady=2)

        # --- диагностика ----------------------------------------------
        ttk.Label(side, text=theme.SEC_DIAG, style="Section.TLabel").pack(
            fill="x", pady=(10, 4))
        box = ttk.Frame(side, style="TFrame")
        box.pack(fill="both", expand=True)
        self.txt = tk.Text(
            box, height=10, wrap="word", bd=0, highlightthickness=1,
            highlightbackground=theme.BORDER, bg=theme.PLOT_BG, fg=theme.MUTED,
            insertbackground=theme.FG, font=("Consolas", 8), state="disabled",
            padx=6, pady=4,
        )
        scroll = ttk.Scrollbar(box, orient="vertical", command=self.txt.yview)
        self.txt.configure(yscrollcommand=scroll.set)
        self.txt.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        for tag, color in (("INFO", theme.MUTED), ("OK", theme.OK),
                           ("WARN", theme.WARN), ("ERROR", theme.ERR),
                           ("HEAD", theme.ACCENT)):
            self.txt.tag_configure(tag, foreground=color)
        self.txt.tag_configure("HEAD", font=("Consolas", 8, "bold"))

    # ---------------------------------------------------------- помощники
    def _card(self, parent: ttk.Frame, title: str) -> ttk.Frame:
        """Карточка-секция: заголовок + сетка «подпись/значение»."""
        wrapper = ttk.Frame(parent, style="TFrame")
        wrapper.pack(fill="x", pady=(0, 8))
        ttk.Label(wrapper, text=title, style="Section.TLabel").pack(fill="x", pady=(0, 3))
        body = ttk.Frame(wrapper, style="Card.TFrame", padding=(8, 6))
        body.pack(fill="x")
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=0)
        return body

    def _row(self, parent: ttk.Frame, row: int, label: str, var: tk.StringVar) -> None:
        ttk.Label(parent, text=label, style="CardLabel.TLabel").grid(
            row=row, column=0, sticky="w", pady=1)
        ttk.Label(parent, textvariable=var, style="CardValue.TLabel").grid(
            row=row, column=1, sticky="e", pady=1)

    # ============================================================ сигналы
    def _connect(self) -> None:
        # UI → контроллер (слоты)
        self.signals.show_graph.connect(self._slot_show_graph)
        self.signals.zoom_in.connect(self._slot_zoom_in)
        self.signals.zoom_out.connect(self._slot_zoom_out)
        self.signals.reset_zoom.connect(self._slot_reset_zoom)
        self.signals.report.connect(self._slot_report)
        self.signals.stress_changed.connect(self._slot_stress_changed)
        self.signals.shutdown.connect(self.close)
        # контроллер → UI
        self.ctrl.render_request.connect(self._render)
        self.ctrl.memory_update.connect(self._on_memory)
        self.ctrl.report_ready.connect(self._on_report_ready)
        self.ctrl.diag.event_signal.connect(self._on_event)

    def _slot_show_graph(self, kind: str | None = None) -> None:
        self.ctrl.show_graph(kind or self.var_kind.get())

    def _slot_zoom_in(self) -> None:
        self.ctrl.zoom_in()

    def _slot_zoom_out(self) -> None:
        self.ctrl.zoom_out()

    def _slot_reset_zoom(self) -> None:
        self.ctrl.reset_zoom()

    def _slot_report(self) -> None:
        self.ctrl.build_report()
        self._on_report_ready(self.ctrl.report_text, self.ctrl.paths)

    def _slot_stress_changed(self, enabled: bool) -> None:
        self._stress_on = bool(enabled)
        if self._stress_on:
            self._schedule_stress()
            self._log("INFO", "Стресс-тест включён: авто-перерисовка каждые "
                              f"{self.opts.stress_interval_ms} мс")
        else:
            self._cancel(self._job_stress)
            self._job_stress = None
            self._log("INFO", "Стресс-тест выключен")

    def _on_kind_changed(self) -> None:
        self.var_kind_hint.set(describe(self.var_kind.get()))
        self.signals.show_graph.emit(self.var_kind.get())

    def _on_wheel(self, event: tk.Event) -> None:
        if event.delta > 0:
            self.signals.zoom_in.emit()
        elif event.delta < 0:
            self.signals.zoom_out.emit()

    # =========================================================== отрисовка
    def _render(self, payload: ViewPayload) -> None:
        """Слот кадра: единственное место, где трогаем matplotlib."""
        try:
            t0 = time.perf_counter()

            self.line.set_data(payload.x, payload.y)
            self.ax.set_xlim(payload.xlim)
            self.ax.set_ylim(payload.ylim)
            self.ax.set_title(f"{payload.kind} · {payload.points_visible:,} точек в кадре"
                              .replace(",", " "))
            # Клиппинг подтверждаем после каждой смены данных/лимитов.
            enforce_clipping(self.ax, [self.line])
            violations = check_bounds(payload.xlim, payload.ylim, payload.x, payload.y)

            self.canvas.draw()   # синхронно: нужно для честного замера кадра
            ms = (time.perf_counter() - t0) * 1000.0

            self.ctrl.record_render(ms, payload.points_visible, violations)
            self.ctrl.opts.extra["canvas_px"] = (
                f"{self.canvas_widget.winfo_width()}×{self.canvas_widget.winfo_height()}"
            )
            self.var_head.set(
                f"{payload.kind} · {payload.points_total} точек · зум {payload.zoom_label} "
                f"· в кадре {payload.points_visible} · {ms:.1f} мс"
            )
            self.var_zoom.set(
                f"Масштаб {payload.zoom_label} · окно {payload.points_visible} из "
                f"{payload.points_total} точек"
            )
            self.var_renders.set(str(self.ctrl.diag.renders))
            self.var_render_ms.set(f"{ms:.1f} мс")
            self.var_clip.set(str(self.ctrl.diag.clip_violations))
        except Exception:  # noqa: BLE001 — не роняем event loop
            self.ctrl.diag.error("Ошибка отрисовки кадра")
            self._log("ERROR", traceback.format_exc(limit=3))

    # ============================================================== память
    def _on_memory(self, m: dict) -> None:
        self.var_rss.set(f"{m['rss_mb']:.1f} МБ")
        self.var_delta.set(f"{m['rss_delta_mb']:+.1f} МБ")
        self.var_slope.set(f"{m['slope_mb_h']:+.2f} МБ/ч")
        self.var_threads.set(str(m["threads"]))
        self.var_ram.set(f"{m['sys_used_pct']:.1f} % · {m['sys_avail_gb']:.2f} ГБ своб.")
        self.var_ram_pct.set(m["sys_used_pct"])
        self.var_uptime.set(theme.fmt_uptime(m["uptime"]))
        self.var_renders.set(str(m["renders"]))
        self.var_render_ms.set(f"{m['render_ms']:.1f} мс")

    # ========================================================= диагностика
    def _on_event(self, level: str, message: str) -> None:
        self._log(level, message)

    def _on_report_ready(self, text: str, paths: dict) -> None:
        self._log("HEAD", "── ИТОГОВЫЙ ОТЧЁТ " + "─" * 24)
        for line in text.splitlines():
            self._log("INFO", line)
        if paths:
            self._log("OK", f"Сохранено: {paths.get('report')}")

    def _log(self, level: str, message: str) -> None:
        """Пишет строку в панель диагностики, обрезая историю (анти-утечка)."""
        if not self.txt.winfo_exists():
            return
        prefix = {"INFO": "·", "OK": "✓", "WARN": "!", "ERROR": "×", "HEAD": "»"}.get(level, "·")
        self.txt.configure(state="normal")
        self.txt.insert("end", f"{prefix} {message}\n", level)
        # Держим не больше 600 строк — Text тоже склонен накапливать память.
        lines = int(self.txt.index("end-1c").split(".")[0])
        if lines > 600:
            self.txt.delete("1.0", f"{lines - 600}.0")
        self.txt.see("end")
        self.txt.configure(state="disabled")

    def _open_logs(self) -> None:
        path = os.path.abspath(self.ctrl.log.dir)
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            self._log("WARN", f"Не удалось открыть каталог: {path}")

    # ============================================================== таймеры
    def _start_session(self) -> None:
        self.ctrl.start()
        self._schedule_memory()
        if self._stress_on:
            self._schedule_stress()
        if self.opts.duration_s > 0:
            self._log("INFO", f"Автостоп через {self.opts.duration_s:.0f} с")
            self._job_deadline = self.after(int(self.opts.duration_s * 1000), self._on_deadline)

    def _schedule_memory(self) -> None:
        self._job_mem = self.after(self.opts.sample_interval_ms, self._tick_memory)

    def _tick_memory(self) -> None:
        if self._closing:
            return
        self.ctrl.tick()
        self._schedule_memory()

    def _schedule_stress(self) -> None:
        self._cancel(self._job_stress)
        self._job_stress = self.after(self.opts.stress_interval_ms, self._tick_stress)

    def _tick_stress(self) -> None:
        if self._closing or not self._stress_on:
            return
        self.ctrl.stress_tick()
        self._schedule_stress()

    def _on_deadline(self) -> None:
        self._log("OK", "Автостоп по таймеру: формирую отчёт и закрываю окно")
        self.close()

    def _cancel(self, job: str | None) -> None:
        if job:
            try:
                self.after_cancel(job)
            except Exception:  # noqa: BLE001
                pass

    # ============================================================== закрытие
    def close(self) -> None:
        if self._closing:
            return
        self._closing = True

        for job in (self._job_mem, self._job_stress, self._job_deadline):
            self._cancel(job)
        self._job_mem = self._job_stress = self._job_deadline = None

        try:
            text, paths = self.ctrl.finish()
            print("\n" + text)
            print(f"\nФайлы: {paths}")
        except Exception:  # noqa: BLE001
            print(traceback.format_exc())

        self.ctrl.shutdown()
        self.signals.clear()

        try:
            self.fig.clear()
            self.canvas_widget.destroy()
        except Exception:  # noqa: BLE001
            pass
        self.destroy()


# ===================================================================== CLI
def make_options(args) -> SessionOptions:
    return SessionOptions(
        toolkit="Tkinter",
        canvas_name="FigureCanvasTkAgg",
        log_dir=args.log_dir,
        n_points=args.points,
        sample_interval_ms=args.interval,
        stress_interval_ms=args.stress_interval,
        duration_s=args.duration,
        seed=args.seed,
        auto_stress=args.stress,
    )


def main(argv: list[str] | None = None) -> int:
    theme.enable_windows_dpi_awareness()
    args = build_parser("Matplotlib GUI Compare — Tkinter + TkAgg").parse_args(argv)
    TkCompareApp(make_options(args)).mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
