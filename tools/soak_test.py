# ═══ tools/soak_test.py ════════════════════════════════════════════════
# РОЛЬ        : нагрузочный прогон того же кадрового конвейера без окон (canvas Agg)
# ИСПОЛЬЗУЮТ  : запускается вручную, чтобы проверить память без GUI
# СМОТРИ ТАКЖЕ: ARCHITECTURE.md, §8
# ──────────────────────────────────────────────────────────────────────
"""Нагрузочный прогон без GUI: тот же кадровый конвейер на canvas Agg.

Зачем: проверить утечки в ядре и в matplotlib, не занимая экран.
Виджетный слой (Tk/Qt/wx) здесь не участвует, поэтому отчёт помечается
как «Agg (headless)» — для финального сравнения библиотек запускайте сами
окна с ``--duration 3600``.

Примеры::

    python tools/soak_test.py --duration 60 --fps 20
    python tools/soak_test.py --duration 3600 --fps 10 --stress
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg", force=True)

from matplotlib.backends.backend_agg import FigureCanvasAgg           # noqa: E402
from matplotlib.figure import Figure                                  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gcompare.core import data as dm                                  # noqa: E402
from gcompare.core import theme                                       # noqa: E402
from gcompare.core.model import ViewPayload                            # noqa: E402
from gcompare.core.session import SessionController, SessionOptions     # noqa: E402
from gcompare.core.view_utils import (                               # noqa: E402
    check_bounds, configure_matplotlib, enforce_clipping, style_axes,
)


class HeadlessCanvas:
    """Минимальная копия кадрового слоя приложений (без виджетов)."""

    def __init__(self) -> None:
        configure_matplotlib()
        # Размер как у реального окна, иначе замер не сопоставим с GUI.
        self.fig = Figure(figsize=(12.0, 7.0), dpi=100, facecolor=theme.PLOT_BG)
        self.canvas_agg = FigureCanvasAgg(self.fig)   # без этого draw() — no-op
        self.ax = self.fig.add_axes(theme.AXES_RECT)
        style_axes(self.ax, bg=theme.PLOT_BG, grid=theme.GRID, fg=theme.MUTED)
        (self.line,) = self.ax.plot([], [], color=theme.LINE, linewidth=1.1,
                                    clip_on=True, zorder=3)
        enforce_clipping(self.ax, [self.line])
        self.canvas_agg.draw()            # создаём буфер и кэш шрифтов один раз

    def render(self, payload: ViewPayload) -> tuple[float, int]:
        t0 = time.perf_counter()
        self.line.set_data(payload.x, payload.y)
        self.ax.set_xlim(payload.xlim)
        self.ax.set_ylim(payload.ylim)
        enforce_clipping(self.ax, [self.line])
        violations = check_bounds(payload.xlim, payload.ylim, payload.x, payload.y)
        self.canvas_agg.draw()
        return (time.perf_counter() - t0) * 1000.0, violations


def main() -> int:
    ap = argparse.ArgumentParser(description="Headless soak-тест кадрового конвейера")
    ap.add_argument("--duration", type=float, default=60.0, help="секунды (3600 = час)")
    ap.add_argument("--fps", type=float, default=10.0, help="кадров в секунду")
    ap.add_argument("--points", type=int, default=dm.N_POINTS)
    ap.add_argument("--log-dir", default=str(ROOT / "logs"))
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()

    opts = SessionOptions(
        toolkit="Agg (headless)",
        canvas_name="FigureCanvasAgg",
        log_dir=args.log_dir,
        n_points=args.points,
        sample_interval_ms=1000,
        duration_s=args.duration,
        seed=args.seed,
        auto_stress=True,
    )
    ctrl = SessionController(opts)
    canvas = HeadlessCanvas()

    def on_render(payload: ViewPayload) -> None:
        ms, violations = canvas.render(payload)
        ctrl.record_render(ms, payload.points_visible, violations)

    ctrl.render_request.connect(on_render)
    ctrl.start()

    period = 1.0 / max(0.5, args.fps)
    t0 = time.perf_counter()
    next_frame = t0
    next_tick = t0 + 1.0
    next_report = t0 + 10.0
    kinds = list(dm.GRAPH_TYPES)
    frame = 0
    kind_idx = 0

    print(f"Старт: {args.duration:.0f} с, {args.fps} кадров/с, {args.points} точек/кадр")
    try:
        while True:
            now = time.perf_counter()
            if now - t0 >= args.duration:
                break
            if now < next_frame:
                time.sleep(min(0.05, next_frame - now))
                continue

            # Периодически меняем тип графика — задействуем все три генератора.
            if frame % 200 == 0:
                kind_idx = (kind_idx + 1) % len(kinds)
                ctrl.show_graph(kinds[kind_idx])
            else:
                ctrl.stress_tick()
            frame += 1
            next_frame += period

            now = time.perf_counter()
            if now >= next_tick:
                ctrl.tick()
                next_tick += 1.0
            if now >= next_report:
                m = ctrl.probe.summary()
                print(f"  t={now - t0:5.0f} c · кадров {ctrl.diag.renders:5d} · "
                      f"RSS {m['rss_end']:.1f} МБ (Δ{m['rss_delta']:+.1f}) · "
                      f"тренд {m['slope_mb_per_hour']:+.2f} МБ/ч")
                next_report += 10.0
    except KeyboardInterrupt:
        print("\nПрервано пользователем — формирую отчёт по факту.")

    text, paths = ctrl.finish()
    ctrl.shutdown()
    print("\n" + text)
    print(f"\nФайлы: {paths}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
