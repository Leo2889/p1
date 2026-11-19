# ═══ tools/smoke_test.py ═══════════════════════════════════════════════
# РОЛЬ        : проверки ядра без GUI и опциональный запуск всех трёх окон (--gui)
# ИСПОЛЬЗУЮТ  : запускается вручную или из tasks/CI
# СМОТРИ ТАКЖЕ: ARCHITECTURE.md, §8
# ──────────────────────────────────────────────────────────────────────
"""Быстрые проверки ядра без запуска GUI.

Запуск::

    python tools/smoke_test.py          # только логика
    python tools/smoke_test.py --gui    # + пробный запуск окон (4 с каждое)

Проверяется главное:
  * генераторы дают корректные массивы;
  * зум никогда не выпускает точки за пределы осей (инвариант клиппинга);
  * отчёт формируется и содержит обязательные секции;
  * сессия пишет CSV/JSON/TXT и корректно закрывается (идемпотентно).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gcompare.core import data as dm                                  # noqa: E402
from gcompare.core import report as rp                                # noqa: E402
from gcompare.core.diagnostics import Diagnostics                     # noqa: E402
from gcompare.core.metrics import MemoryProbe                          # noqa: E402
from gcompare.core.model import MAX_ZOOM_STEPS, MIN_WINDOW_POINTS, PlotModel  # noqa: E402
from gcompare.core.session import SessionController, SessionOptions     # noqa: E402
from gcompare.core.view_utils import check_bounds                       # noqa: E402

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, fn) -> None:
    try:
        fn()
        results.append((PASS, name, ""))
    except AssertionError as exc:
        results.append((FAIL, name, str(exc)))
    except Exception as exc:  # noqa: BLE001
        results.append((FAIL, name, f"{type(exc).__name__}: {exc}"))


# ------------------------------------------------------------------ тесты
def test_generators() -> None:
    for kind in dm.GRAPH_TYPES:
        x, y = dm.generate(kind, 4096, None)
        assert x.size == 4096 and y.size == 4096, f"{kind}: неверная длина"
        assert x.dtype == y.dtype == "float64", f"{kind}: неверный dtype"
        assert y.flags["C_CONTIGUOUS"], f"{kind}: буфер не непрерывный"
        assert abs(float(y.min())) < 50 and abs(float(y.max())) < 50, f"{kind}: выброс значений"
    # Saw обязан быть периодическим и детерминированным
    _, y1 = dm.generate("Saw", 1024)
    _, y2 = dm.generate("Saw", 1024)
    assert (y1 == y2).all(), "Saw должен быть детерминированным"


def test_zoom_invariant() -> None:
    """Самый важный тест: линия не выходит за рамку при любом зуме."""
    for kind in dm.GRAPH_TYPES:
        m = PlotModel(n_points=8192, seed=42)
        m.generate(kind)
        for step in range(MAX_ZOOM_STEPS + 3):
            p = m.payload()
            assert p.points_visible >= MIN_WINDOW_POINTS, (
                f"{kind} шаг {step}: окно {p.points_visible} < {MIN_WINDOW_POINTS}")
            assert check_bounds(p.xlim, p.ylim, p.x, p.y) == 0, (
                f"{kind} шаг {step}: точки вне кадра")
            assert p.xlim[1] > p.xlim[0], f"{kind} шаг {step}: пустой xlim"
            assert p.ylim[1] > p.ylim[0], f"{kind} шаг {step}: пустой ylim"
            m.zoom_in()
        for _ in range(MAX_ZOOM_STEPS + 3):
            p = m.payload()
            assert check_bounds(p.xlim, p.ylim, p.x, p.y) == 0, f"{kind}: точки вне кадра при зуме"
            m.zoom_out()
        assert m.zoom_steps == 0, "зум не вернулся к 1×"


def test_zoom_center_kept() -> None:
    """Окно должно оставаться внутри массива, без выхода за края."""
    m = PlotModel(n_points=1000, seed=7)
    for _ in range(6):
        m.zoom_in()
    p = m.payload()
    assert p.x[0] >= 0 and p.x[-1] <= 100.0 + 1e-9, "срез вышел за домен"
    assert p.points_visible > 0


def test_memory_probe() -> None:
    probe = MemoryProbe(maxlen=50)
    for _ in range(5):
        probe.sample()
        time.sleep(0.01)
    s = probe.summary()
    for key in ("rss_start", "rss_warm", "rss_end", "rss_delta", "rss_delta_warm",
                "slope_mb_per_hour", "trend_reliable", "samples", "sys_used_pct"):
        assert key in s, f"нет ключа {key}"
    assert s["warmup_s"] == 60.0, "окно прогрева должно быть 60 с"
    assert s["samples"] == 5
    assert s["rss_end"] > 10, "RSS подозрительно мал"
    assert len(probe.history) <= 50, "кольцевой буфер не ограничивает историю"


def test_diagnostics_report() -> None:
    d = Diagnostics("UnitTest")
    d.info("старт")
    d.warn("тестовое предупреждение", dedupe=True)
    d.warn("тестовое предупреждение", dedupe=True)
    for ms in (5.0, 7.0, 300.0):
        d.track_render(ms, 1024, 0)
    d.track_render(6.0, 1024, 3)          # имитируем нарушение клиппинга
    text = d.build_report(
        mem_summary={"rss_start": 100.0, "rss_warm": 101.0, "rss_end": 103.0,
                     "rss_min": 99.0, "rss_max": 106.0,
                     "rss_delta": 3.0, "rss_delta_pct": 3.0, "rss_delta_warm": 2.0,
                     "slope_mb_per_hour": 0.5, "warmup_s": 60, "tail_samples": 60,
                     "trend_reliable": True,
                     "samples": 120, "threads": 12, "sys_used_pct": 55.0,
                     "sys_avail_gb": 7.5},
        uptime=3600,
        extra=["КОНФИГУРАЦИЯ", "тест"],
        canvas_name="UnitCanvas",
    )
    for must in ("ОТЧЁТ О СЕССИИ", "НАГРУЗКА", "ПАМЯТЬ", "ДЕФЕКТЫ", "ВЕРДИКТ"):
        assert must in text, f"в отчёте нет секции {must}"
    assert "клиппинг" in text.lower(), "нарушение клиппинга не попало в отчёт"


def test_session_roundtrip() -> None:
    tmp = tempfile.mkdtemp(prefix="gcompare_")
    opts = SessionOptions(toolkit="UnitTest", canvas_name="UnitCanvas", log_dir=tmp,
                          n_points=2048, sample_interval_ms=100, seed=1)
    ctrl = SessionController(opts)
    frames: list = []
    ctrl.render_request.connect(frames.append)
    ctrl.start()
    assert len(frames) == 1, "старт не отрисовал первый кадр"

    ctrl.show_graph("Saw")
    ctrl.zoom_in()
    ctrl.zoom_in()
    ctrl.tick()
    ctrl.stress_tick()
    assert len(frames) >= 4, "кадры не приходят по сигналу"

    text, paths = ctrl.finish()
    assert text and "ОТЧЁТ" in text
    for key in ("csv", "report", "summary"):
        assert Path(paths[key]).exists(), f"нет файла {key}"
    summary = json.loads(Path(paths["summary"]).read_text(encoding="utf-8"))
    assert summary["toolkit"] == "UnitTest"
    assert summary["clip_violations"] == 0
    assert summary["renders"] == 0, "renders учитывает только GUI-слой"

    # повторный finish не должен падать и перезаписывать файлы
    text2, paths2 = ctrl.finish()
    assert text2 == text and paths2 == paths
    ctrl.shutdown()

    csv_lines = Path(paths["csv"]).read_text(encoding="utf-8").strip().splitlines()
    assert len(csv_lines) >= 2, "CSV пуст"


def test_report_table() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="gcompare_rp_"))
    for name, slope in (("Tkinter", 0.4), ("PyQt6", 3.5), ("wxPython", 12.0)):
        (tmp / f"{name}_summary.json").write_text(json.dumps({
            "toolkit": name, "canvas": "X", "uptime_s": 3600, "renders": 7200,
            "render_ms": {"p95": 12.0}, "clip_violations": 0,
            "memory": {"rss_start": 100.0, "rss_end": 100.0 + slope, "rss_delta": slope,
                       "slope_mb_per_hour": slope, "trend_reliable": True},
        }), encoding="utf-8")
    best = rp.best_per_toolkit(rp.load_summaries(tmp))
    table = rp.format_table(best)
    assert "Tkinter" in table and "wxPython" in table
    assert rp.verdict_for(best["wxPython"]) == rp.VERDICT_LEAK
    assert rp.verdict_for(best["Tkinter"]) == rp.VERDICT_OK

    # Короткая сессия: тренд ненадёжен → «мало данных», а не «утечка»
    short = {"toolkit": "PyQt6", "clip_violations": 0, "uptime_s": 6,
             "memory": {"rss_delta": 45.0, "slope_mb_per_hour": 0.0, "trend_reliable": False}}
    assert rp.verdict_for(short) == rp.VERDICT_SHORT
    # Headless-прогоны не попадают в сравнение библиотек
    (tmp / "headless_summary.json").write_text(json.dumps({
        "toolkit": "Agg (headless)", "uptime_s": 3600, "clip_violations": 0,
        "memory": {"trend_reliable": True, "slope_mb_per_hour": 0.1, "rss_delta": 0.2},
    }), encoding="utf-8")
    best2 = rp.best_per_toolkit(rp.load_summaries(tmp))
    assert "Agg (headless)" not in best2, "headless-прогон не должен попадать в сравнение"
    md = rp.write_markdown(best, tmp / "comparison.md")
    assert md.exists() and "Сравнение" in md.read_text(encoding="utf-8")


# ------------------------------------------------------------ GUI-проверка
def gui_smoke() -> None:
    """Пробный запуск каждого окна на 4 секунды (нужен рабочий стол)."""
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")   # Qt не требует экрана
    # Разные интерпретаторы для Tk и остальных — на случай, если tkinter
    # есть только в отдельной сборке Python.
    tk_python = os.environ.get("GCOMPARE_TK_PYTHON", sys.executable)
    gui_python = os.environ.get("GCOMPARE_GUI_PYTHON", sys.executable)
    for key, module in (("tk", "gcompare.apps.tk_app"),
                        ("qt", "gcompare.apps.qt_app"),
                        ("wx", "gcompare.apps.wx_app")):
        exe = tk_python if key == "tk" else gui_python
        cmd = [exe, "-m", module, "--duration", "4", "--stress",
               "--stress-interval", "200", "--log-dir", str(ROOT / "logs")]
        try:
            proc = subprocess.run(cmd, cwd=str(ROOT), env=env, timeout=90,
                                  capture_output=True, text=True)
            ok = proc.returncode == 0 and "ОТЧЁТ О СЕССИИ" in proc.stdout
            detail = "" if ok else (proc.stderr or proc.stdout)[-400:]
            results.append((PASS if ok else FAIL, f"GUI smoke: {key}", detail))
        except subprocess.TimeoutExpired:
            results.append((FAIL, f"GUI smoke: {key}", "окно не закрылось за 90 с"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gui", action="store_true", help="проверить и окна (4 с на каждое)")
    args = ap.parse_args()

    check("генераторы данных", test_generators)
    check("инвариант клиппинга при зуме", test_zoom_invariant)
    check("окно зума не выходит за домен", test_zoom_center_kept)
    check("замеры памяти", test_memory_probe)
    check("отчёт по дефектам", test_diagnostics_report)
    check("сессия: CSV/JSON/TXT", test_session_roundtrip)
    check("сводная таблица", test_report_table)
    if args.gui:
        gui_smoke()

    width = max(len(n) for _, n, _ in results)
    print()
    for status, name, detail in results:
        mark = "OK  " if status == PASS else "FAIL"
        print(f"[{mark}] {name.ljust(width)}  {detail}")
    failed = sum(1 for s, _, _ in results if s == FAIL)
    print(f"\nИтог: {len(results) - failed}/{len(results)} проверок пройдено")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
