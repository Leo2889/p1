# ═══ gcompare/core/session.py ══════════════════════════════════════════
# РОЛЬ        : контроллер сессии: связывает модель, метрики, диагностику и GUI
# ИСПОЛЬЗУЮТ  : tk_app.py, qt_app.py, wx_app.py, tools/soak_test.py
# СМОТРИ ТАКЖЕ: ARCHITECTURE.md, §3
# ──────────────────────────────────────────────────────────────────────
"""Контроллер сессии — единственное место, где связаны модель, метрики и GUI.

Любое GUI-приложение (Tk/Qt/wx) делает одно и то же:
    1. создаёт :class:`SessionController` с параметрами;
    2. подписывается на его сигналы и перерисовывает canvas;
    3. отдаёт контроллеру время отрисовки через ``record_render``.

Благодаря этому функционал трёх приложений гарантированно идентичен,
а различия сводятся только к виджетам.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from . import data as data_mod
from .diagnostics import Diagnostics
from .metrics import MemoryProbe
from .model import PlotModel, ViewPayload
from .session_log import SessionLog
from .signals import Signal


@dataclass
class SessionOptions:
    """Параметры запуска — одинаковый набор для всех трёх приложений."""

    toolkit: str                          # "Tkinter" / "PyQt6" / "wxPython"
    canvas_name: str = ""                 # имя canvas-бэкенда matplotlib
    log_dir: str | Path = "logs"
    n_points: int = data_mod.N_POINTS
    sample_interval_ms: int = 1000        # период замера памяти
    stress_interval_ms: int = 500         # период авто-перерисовки
    duration_s: float = 0.0               # 0 → работать до закрытия окна
    seed: int | None = None
    auto_stress: bool = False
    extra: dict = field(default_factory=dict)


class SessionController:
    """Шина событий + учёт ресурсов одной сессии."""

    def __init__(self, opts: SessionOptions) -> None:
        self.opts = opts
        self.model = PlotModel(n_points=opts.n_points, seed=opts.seed)
        self.probe = MemoryProbe()
        self.diag = Diagnostics(opts.toolkit)
        self.log = SessionLog(opts.toolkit, opts.log_dir)

        # Публичные сигналы для GUI.
        self.render_request = Signal("render_request")   # (ViewPayload)
        self.memory_update = Signal("memory_update")     # (dict)
        self.session_status = Signal("session_status")   # (dict)
        self.report_ready = Signal("report_ready")       # (str, dict)
        self.finished = Signal("finished")               # (str)

        self._last_render_ms: float | None = None
        self._finished = False
        self._t0 = time.perf_counter()
        self.report_text: str = ""
        self.paths: dict = {}

        # Проброс кадров: и генерация, и зум ведут к одной перерисовке.
        self.model.data_changed.connect(self._on_payload)
        self.model.view_changed.connect(self._on_payload)

    # -------------------------------------------------------------- запуск
    def start(self) -> None:
        """Базовая точка замера + первый кадр."""
        first = self.probe.sample()
        self.log.row(first)
        self.diag.info(
            f"Сессия запущена: {self.opts.toolkit} / {self.opts.canvas_name or 'matplotlib'}"
        )
        self.diag.info(
            f"Точек на кадр: {self.opts.n_points}, замер памяти каждые "
            f"{self.opts.sample_interval_ms} мс"
        )
        self._on_payload(self.model.payload())

    # ------------------------------------------------------------- команды
    def _on_payload(self, payload: ViewPayload) -> None:
        self.render_request.emit(payload)

    def show_graph(self, kind: str | None = None) -> ViewPayload:
        """Слот кнопки «Показать график»."""
        return self.model.generate(kind)

    def zoom_in(self) -> ViewPayload | None:
        payload = self.model.zoom_in()
        if payload is None:
            self.diag.warn("Достигнут максимальный зум (16 384×)", dedupe=True)
        return payload

    def zoom_out(self) -> ViewPayload | None:
        payload = self.model.zoom_out()
        if payload is None:
            self.diag.info("Зум уже минимальный — показан весь ряд", dedupe=True)
        return payload

    def reset_zoom(self) -> ViewPayload | None:
        payload = self.model.reset_zoom()
        if payload is None:
            # Зум уже минимальный: всё равно просим перерисовать кадр,
            # чтобы кнопка «Сброс» всегда давала видимую реакцию.
            payload = self.model.payload()
            self.render_request.emit(payload)
        return payload

    # -------------------------------------------------------------- учёт
    def record_render(self, ms: float, points: int, violations: int) -> None:
        self._last_render_ms = ms
        self.diag.track_render(ms, points, violations)

    def tick(self) -> dict:
        """Вызывается таймером GUI раз в ``sample_interval_ms``."""
        s = self.probe.sample()
        self.log.row(s, self._last_render_ms)
        stats = self.probe.summary()
        rs = self.diag.render_stats()
        payload = {
            "rss_mb": s.rss_mb,
            "rss_delta_mb": stats.get("rss_delta", 0.0),
            "slope_mb_h": stats.get("slope_mb_per_hour", 0.0),
            "peak_mb": stats.get("rss_max", s.rss_mb),
            "threads": s.threads,
            "sys_used_pct": s.sys_used_pct,
            "sys_avail_gb": s.sys_avail_gb,
            "uptime": s.uptime,
            "renders": self.diag.renders,
            "render_ms": self._last_render_ms or 0.0,
            "render_p95": rs.get("p95", 0.0),
            "clip_violations": self.diag.clip_violations,
            "zoom": self.model.payload().zoom_label,
            "samples": stats.get("samples", 0),
        }
        self.memory_update.emit(payload)
        return payload

    def stress_tick(self) -> None:
        """Авто-перерисовка для нагрузочного теста (флажок в UI)."""
        if not self._finished:
            self.model.generate()

    # ------------------------------------------------------------ отчёты
    def build_report(self) -> str:
        stats = self.probe.summary()
        uptime = self.probe.uptime()
        extra = [
            "КОНФИГУРАЦИЯ ПРОГОНА",
            "─" * 78,
            f"Тип графика     : {self.model.kind} (переключение кнопками Random/Meander/Saw)",
            f"Точек на кадр   : {self.opts.n_points}",
            *([f"Пикселей в кадре : {self.opts.extra['canvas_px']} px"]
              if self.opts.extra.get("canvas_px") else []),
            f"Интервал замера : {self.opts.sample_interval_ms} мс",
            f"Стресс-режим    : {'вкл' if self.opts.auto_stress else 'выкл'} "
            f"({self.opts.stress_interval_ms} мс/кадр)",
            f"Лог сессии      : {self.log.csv_path}",
        ]
        text = self.diag.build_report(
            mem_summary=stats,
            uptime=uptime,
            extra=extra,
            canvas_name=self.opts.canvas_name,
        )
        self.report_text = text
        return text

    def finish(self) -> tuple[str, dict]:
        """Итог сессии: отчёт + сводка + закрытие файлов. Идемпотентно."""
        if self._finished:
            return self.report_text, self.paths

        self._finished = True
        text = self.build_report()
        stats = self.probe.summary()
        rs = self.diag.render_stats()
        uptime = self.probe.uptime()

        summary = {
            "toolkit": self.opts.toolkit,
            "canvas": self.opts.canvas_name,
            "python": __import__("sys").version.split()[0],
            "uptime_s": round(uptime, 1),
            "renders": self.diag.renders,
            "render_ms": rs,
            "clip_violations": self.diag.clip_violations,
            "memory": stats,
            "points_per_frame": self.opts.n_points,
            "stress": self.opts.auto_stress,
            "report_file": str(self.log.report_path),
            "csv_file": str(self.log.csv_path),
        }
        self.log.write_summary(summary, text)
        self.log.close()

        self.paths = {
            "csv": str(self.log.csv_path),
            "report": str(self.log.report_path),
            "summary": str(self.log.summary_path),
        }
        self.report_ready.emit(text, self.paths)
        self.finished.emit(text)
        return text, self.paths

    # -------------------------------------------------------------- уборка
    def shutdown(self) -> None:
        """Отписка от всех сигналов — обязательна перед уничтожением окна."""
        for sig in (
            self.render_request, self.memory_update, self.session_status,
            self.report_ready, self.finished,
            self.model.data_changed, self.model.view_changed,
            self.diag.event_signal,
        ):
            sig.clear()
        self.log.close()
