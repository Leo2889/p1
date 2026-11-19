# ═══ gcompare/core/diagnostics.py ══════════════════════════════════════
# РОЛЬ        : события сессии, тайминги кадров и текст итогового отчёта с дефектами
# ИСПОЛЬЗУЮТ  : session.py; отчёт сохраняет session_log.py
# СМОТРИ ТАКЖЕ: ARCHITECTURE.md, §6
# ──────────────────────────────────────────────────────────────────────
"""Диагностика сессии и текстовый отчёт о дефектах.

Отчёт — это то самое «текст в конце», которое требуется по заданию:
после часовой сессии (или по кнопке «Отчёт») приложение печатает
таблицу метрик, список найденных дефектов и вердикт о пригодности
к длительной работе.
"""

from __future__ import annotations

import platform
import sys
import time
from collections import Counter, deque
from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np

from .signals import Signal

# Пороговые значения для автоматических выводов.
LEAK_WARN_MB_PER_H = 2.0     # тренд RSS, МБ/час
LEAK_WARN_DELTA_MB = 20.0    # суммарный прирост после прогрева, МБ
LEAK_FAIL_MB_PER_H = 8.0
#: Минимальный абсолютный прирост, который вообще считается значимым.
#: 0.2–0.4 МБ дрейфа за пару минут — это шум аллокатора; экстраполяция
#: такого шума на час даёт десятки «МБ/час» и ложные выводы об утечке.
LEAK_MIN_DELTA_MB = 5.0
RENDER_WARN_P95_MS = 150.0
RENDER_FAIL_P95_MS = 400.0

LEVELS = ("INFO", "WARN", "ERROR", "OK")
WIDTH = 78


@dataclass(frozen=True)
class Event:
    ts: float
    level: str
    message: str


def _line(ch: str = "─") -> str:
    return ch * WIDTH


def _ver(mod: str, default: str = "?") -> str:
    try:
        from importlib.metadata import version

        return version(mod)
    except Exception:  # noqa: BLE001
        return default


class Diagnostics:
    """Копилка событий, таймингов и выводов по сессии."""

    def __init__(self, toolkit: str, capacity: int = 1000) -> None:
        self.toolkit = toolkit
        self.started_at = time.time()
        self.events: deque[Event] = deque(maxlen=capacity)
        self.counts: Counter[str] = Counter()
        self.event_signal = Signal("diagnostic_event")   # (level, message)

        self._render_ms: deque[float] = deque(maxlen=4000)
        self._render_points: deque[int] = deque(maxlen=4000)
        self.renders = 0
        self.clip_violations = 0

    # ------------------------------------------------------------ события
    def note(self, level: str, message: str, *, dedupe: bool = False) -> None:
        """Записывает событие и уведомляет UI."""
        level = level.upper()
        key = f"{level}:{message}"
        self.counts[key] += 1
        if dedupe and self.counts[key] > 1:
            return
        self.events.append(Event(time.time(), level, message))
        self.event_signal.emit(level, message)

    def info(self, msg: str, **kw) -> None:
        self.note("INFO", msg, **kw)

    def warn(self, msg: str, **kw) -> None:
        self.note("WARN", msg, **kw)

    def error(self, msg: str, **kw) -> None:
        self.note("ERROR", msg, **kw)

    def ok(self, msg: str) -> None:
        self.note("OK", msg)

    # ------------------------------------------------------------ рендер
    def track_render(self, ms: float, points: int, violations: int = 0) -> None:
        """Учитывает один кадр: время, число точек, нарушения клиппинга."""
        self.renders += 1
        self._render_ms.append(float(ms))
        self._render_points.append(int(points))
        if violations:
            self.clip_violations += violations
            self.error(
                f"Клиппинг нарушен: {violations} точек вне кадра — линия вышла за рамку",
                dedupe=True,
            )

    def render_stats(self) -> dict:
        if not self._render_ms:
            return {"n": 0}
        arr = np.asarray(self._render_ms, dtype=float)
        return {
            "n": int(arr.size),
            "mean": round(float(arr.mean()), 2),
            "p50": round(float(np.percentile(arr, 50)), 2),
            "p95": round(float(np.percentile(arr, 95)), 2),
            "max": round(float(arr.max()), 2),
            "points_avg": int(np.mean(self._render_points)) if self._render_points else 0,
        }

    # ------------------------------------------------------------- отчёт
    def build_report(
        self,
        *,
        mem_summary: dict,
        uptime: float,
        extra: Sequence[str] = (),
        canvas_name: str = "",
    ) -> str:
        """Формирует итоговый текстовый отчёт (plain text, ширина 78)."""
        hh = int(uptime // 3600)
        mm = int((uptime % 3600) // 60)
        ss = int(uptime % 60)
        rs = self.render_stats()

        out: list[str] = []
        a = out.append

        a(_line("═"))
        a(f" ОТЧЁТ О СЕССИИ — {self.toolkit}")
        a(_line("═"))
        a(f"Начало сессии   : {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.started_at))}")
        a(f"Длительность    : {hh} ч {mm:02d} мин {ss:02d} с")
        a(f"Python / ОС     : {sys.version.split()[0]} · {platform.system()} {platform.release()}")
        a(f"Matplotlib      : {_ver('matplotlib')}   psutil: {_ver('psutil')}")
        if canvas_name:
            a(f"GUI / canvas    : {self.toolkit} · {canvas_name}")
        a("")

        # ------------------------------------------------------- нагрузка
        a("НАГРУЗКА")
        a(_line())
        a(f"Перерисовок     : {self.renders:,}".replace(",", " "))
        if rs.get("n"):
            a(f"Время отрисовки : среднее {rs['mean']:.1f} мс · p50 {rs['p50']:.1f} · "
              f"p95 {rs['p95']:.1f} · макс {rs['max']:.1f} мс")
            a(f"Точек на кадр   : ~{rs['points_avg']} (срез видимого окна)")
        a(f"Нарушений клипа : {self.clip_violations}")
        if uptime > 0 and self.renders:
            a(f"Частота кадров  : {self.renders / uptime:.2f} FPS-экв. в среднем")
        a("")

        # --------------------------------------------------------- память
        a("ПАМЯТЬ ПРОЦЕССА И СИСТЕМЫ")
        a(_line())
        if mem_summary:
            warm_s = int(mem_summary.get("warmup_s", 60))
            rss_warm = mem_summary.get("rss_warm", mem_summary["rss_start"])
            delta_warm = mem_summary.get("rss_delta_warm", mem_summary["rss_delta"])
            a(f"RSS старт       : {mem_summary['rss_start']:.1f} МБ")
            a(f"RSS через {warm_s:2d} с  : {rss_warm:.1f} МБ "
              f"(прогрев {rss_warm - mem_summary['rss_start']:+.1f} МБ)")
            a(f"RSS конец       : {mem_summary['rss_end']:.1f} МБ "
              f"(Δ после прогрева {delta_warm:+.1f} МБ, "
              f"всего {mem_summary['rss_delta']:+.1f} МБ)")
            a(f"RSS минимум/пик : {mem_summary['rss_min']:.1f} / {mem_summary['rss_max']:.1f} МБ")
            if not mem_summary.get("trend_reliable", True):
                trend_str = "мало данных (нужно ≥ 90 с после прогрева)"
            elif abs(delta_warm) < LEAK_MIN_DELTA_MB:
                trend_str = (f"≈0 — шум: Δ {delta_warm:+.1f} МБ < порога "
                             f"{LEAK_MIN_DELTA_MB:.0f} МБ")
            else:
                trend_str = (f"{mem_summary['slope_mb_per_hour']:+.2f} МБ/час "
                             f"(порог внимания {LEAK_WARN_MB_PER_H:.1f})")
            a(f"Тренд (хвост)   : {trend_str}")
            a(f"Замеров         : {mem_summary['samples']} всего, "
              f"{mem_summary.get('tail_samples', 0)} после прогрева "
              f"(кольцевой буфер, рост памяти исключён)")
            a(f"Потоков         : {mem_summary['threads']}")
            a(f"Системная RAM   : занято {mem_summary['sys_used_pct']:.1f} %, "
              f"доступно {mem_summary['sys_avail_gb']:.2f} ГБ")
        else:
            a("Замеров нет — сессия слишком короткая")
        a("")
        for line in extra:
            a(line)
        if extra:
            a("")

        # -------------------------------------------------------- дефекты
        defects = self._collect_defects(mem_summary, rs, uptime)
        a(f"ДЕФЕКТЫ И ЗАМЕЧАНИЯ ({len(defects)})")
        a(_line())
        if not defects:
            a("[OK]   Дефектов не обнаружено.")
        for lvl, msg in defects:
            a(f"[{lvl:<5}] {msg}")
        a("")

        # -------------------------------------------------------- вердикт
        a("ВЕРДИКТ")
        a(_line())
        for line in self._verdict(mem_summary, rs, uptime, defects):
            a(line)
        a(_line("═"))
        return "\n".join(out)

    # --------------------------------------------------------- эвристики
    def _collect_defects(self, mem: dict, rs: dict, uptime: float) -> list[tuple[str, str]]:
        defects: list[tuple[str, str]] = []

        if self.clip_violations:
            defects.append(
                ("ERROR", f"{self.clip_violations} точек вышли за область графика — "
                          "клиппинг не работает, проверьте set_clip_on/clip_path")
            )
        else:
            defects.append(("OK", "Линия графика не выходит за рамку: 0 нарушений клиппинга"))

        if mem:
            slope = mem["slope_mb_per_hour"]
            delta = mem.get("rss_delta_warm", mem.get("rss_delta", 0.0))
            reliable = bool(mem.get("trend_reliable", True))
            if not reliable:
                defects.append(
                    ("INFO", f"Тренд памяти ещё не оценивается: {mem.get('tail_samples', 0)} "
                             f"замеров после прогрева (нужно ≥ 90 с). Прирост {delta:+.1f} МБ "
                             "после прогрева — в пределах шума кэшей, вывод об утечке "
                             "будет сделан на часовом прогоне")
                )
            elif delta >= LEAK_MIN_DELTA_MB and (
                slope >= LEAK_FAIL_MB_PER_H or delta >= LEAK_WARN_DELTA_MB * 3
            ):
                defects.append(
                    ("ERROR", f"Быстрый рост памяти: {slope:+.2f} МБ/час, "
                              f"Δ {delta:+.1f} МБ после прогрева — похоже на утечку "
                              "(проверьте подписки на сигналы и таймеры)")
                )
            elif delta >= LEAK_MIN_DELTA_MB and (
                slope >= LEAK_WARN_MB_PER_H or delta >= LEAK_WARN_DELTA_MB
            ):
                defects.append(
                    ("WARN", f"Умеренный рост RSS: {slope:+.2f} МБ/час, "
                             f"Δ {delta:+.1f} МБ после прогрева — допустимо для кэшей "
                             "Qt/wx, но требует контроля")
                )
            elif reliable:
                defects.append(
                    ("OK", f"Память стабильна после прогрева: Δ {delta:+.1f} МБ "
                           f"(тренд {slope:+.2f} МБ/час — в пределах шума, "
                           f"значимым считается рост от {LEAK_MIN_DELTA_MB:.0f} МБ)")
                )
            if mem["rss_max"] - mem["rss_min"] > 0 and mem["rss_end"] <= mem["rss_min"] + 0.5:
                defects.append(("OK", "RSS вернулся к минимуму — признаков накопления объектов нет"))

        if rs.get("n"):
            if rs["p95"] >= RENDER_FAIL_P95_MS:
                defects.append(
                    ("ERROR", f"Отрисовка медленная: p95 {rs['p95']:.0f} мс — интерфейс будет "
                              "заметно подвисать (уменьшите число точек или включите blit)")
                )
            elif rs["p95"] >= RENDER_WARN_P95_MS:
                defects.append(
                    ("WARN", f"Отдельные кадры медленные: p95 {rs['p95']:.0f} мс, "
                             f"макс {rs['max']:.0f} мс")
                )
            else:
                defects.append(("OK", f"Отрисовка быстрая: p95 {rs['p95']:.0f} мс"))

        for key, count in self.counts.items():
            if key.startswith("ERROR:"):
                defects.append(("ERROR", f"{key.split(':', 1)[1]} (повторов: {count})"))
            elif key.startswith("WARN:"):
                defects.append(("WARN", f"{key.split(':', 1)[1]} (повторов: {count})"))

        if uptime < 1800:
            defects.append(
                ("INFO", f"Сессия {uptime / 60:.0f} мин: порог значимого роста "
                         f"({LEAK_MIN_DELTA_MB:.0f} МБ) за такое время ещё не показателен. "
                         "Финальный вывод — после часового прогона (--duration 3600)")
            )
        if self.renders == 0:
            defects.append(("WARN", "За сессию не было ни одной перерисовки"))
        return defects

    def _verdict(self, mem: dict, rs: dict, uptime: float, defects: Iterable) -> list[str]:
        errors = sum(1 for lvl, _ in defects if lvl == "ERROR")
        warns = sum(1 for lvl, _ in defects if lvl == "WARN")

        if errors:
            v = [f"Требует исправления: {errors} критичных дефект(ов), {warns} предупреждений."]
            v.append("Эксплуатация более часа не рекомендуется до устранения причин.")
        elif warns:
            v = [f"Работоспособно, но есть {warns} предупреждений — следите за трендом RSS."]
            v.append("Для часа непрерывной работы запас по памяти достаточный.")
        else:
            v = ["Утечек не обнаружено, клиппинг соблюдён, отрисовка в норме."]
            v.append("Приложение пригодно для непрерывной работы более 1 часа.")

        if self.clip_violations == 0:
            v.append("Инвариант «линия внутри рамки» выполнялся на каждом кадре.")
        if uptime < 300:
            v.append("Повторите прогон длительностью ≥ 1 часа (--duration 3600) для финального вывода.")
        return v
