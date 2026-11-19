# ═══ gcompare/core/metrics.py ══════════════════════════════════════════
# РОЛЬ        : замеры RSS / системной RAM и тренд МБ/час (окно прогрева 60 с)
# ИСПОЛЬЗУЮТ  : session.py (раз в секунду), core/report.py
# СМОТРИ ТАКЖЕ: ARCHITECTURE.md, §6
# ──────────────────────────────────────────────────────────────────────
"""Замер памяти процесса и системы.

Требование «1 час без утечек» проверяется по тренду RSS процесса:
линейная регрессия по точкам ``(uptime, rss)`` даёт МБ/час. История
хранится в ``deque(maxlen=...)`` — сама телеметрия не растёт в памяти
(при интервале 1 с и maxlen 7200 это ровно 2 часа).
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import asdict, dataclass

import psutil

MB = 1024.0 * 1024.0

#: Окно прогрева, исключаемое из оценки тренда.
#: За первые ~20–60 с процесс набирает RSS: импорт модулей, кэш шрифтов
#: Matplotlib, буферы canvas, пулы аллокатора. Если считать МНК по всей
#: сессии, этот «ступенчатый» рост превращается в ложные десятки МБ/час.
#: Утечка — это рост ПОСЛЕ прогрева, поэтому тренд считается по хвосту.
WARMUP_S = 60.0

#: Минимум данных в хвосте, чтобы тренд считался надёжным.
MIN_TAIL_SAMPLES = 10
MIN_TAIL_SPAN_S = 60.0


@dataclass
class MemSample:
    """Один замер. Плоская структура — удобно и в UI, и в CSV."""

    uptime: float          # секунды с начала сессии
    rss_mb: float          # резидентная память процесса
    vms_mb: float          # виртуальная память
    threads: int           # потоки процесса
    sys_used_pct: float    # занято системной RAM, %
    sys_avail_gb: float    # доступно системной RAM, ГБ


class MemoryProbe:
    """Источник замеров + агрегаты для отчёта."""

    def __init__(self, maxlen: int = 7200) -> None:
        self._proc = psutil.Process()
        self.history: deque[MemSample] = deque(maxlen=maxlen)
        self._t0 = time.perf_counter()

    # ------------------------------------------------------------- замер
    def uptime(self) -> float:
        return time.perf_counter() - self._t0

    def sample(self) -> MemSample:
        info = self._proc.memory_info()
        vm = psutil.virtual_memory()
        s = MemSample(
            uptime=round(self.uptime(), 3),
            rss_mb=round(info.rss / MB, 2),
            vms_mb=round(info.vms / MB, 2),
            threads=self._proc.num_threads(),
            sys_used_pct=round(vm.percent, 1),
            sys_avail_gb=round(vm.available / MB / 1024.0, 2),
        )
        self.history.append(s)
        return s

    # ---------------------------------------------------------- агрегаты
    def summary(self) -> dict:
        """Статистика по накопленной истории (без роста памяти).

        Разделяет два разных явления:

        * ``rss_delta`` — общий прирост с момента старта (включая прогрев);
        * ``rss_delta_warm`` и ``slope_mb_per_hour`` — рост ПОСЛЕ прогрева,
          именно они отвечают на вопрос «есть ли утечка».
        """
        if not self.history:
            return {}
        samples = list(self.history)
        t = [s.uptime for s in samples]
        rss = [s.rss_mb for s in samples]
        first, last = rss[0], rss[-1]
        span = max(1e-6, t[-1] - t[0])

        # Хвост сессии без окна прогрева.
        tail = [s for s in samples if s.uptime >= WARMUP_S]
        if not tail:                      # сессия короче прогрева
            tail = samples[-1:]
        rss_warm = tail[0].rss_mb
        tail_span = tail[-1].uptime - tail[0].uptime if len(tail) > 1 else 0.0
        reliable = (len(tail) >= MIN_TAIL_SAMPLES and tail_span >= MIN_TAIL_SPAN_S)

        # МНК-наклон по хвосту: устойчивее, чем «последний минус первый».
        if reliable:
            n = len(tail)
            mt = sum(s.uptime for s in tail) / n
            mr = sum(s.rss_mb for s in tail) / n
            num = sum((s.uptime - mt) * (s.rss_mb - mr) for s in tail)
            den = sum((s.uptime - mt) ** 2 for s in tail) or 1e-9
            slope_mb_h = (num / den) * 3600.0
        else:
            slope_mb_h = 0.0

        return {
            "samples": len(samples),
            "span_s": round(span, 1),
            "rss_start": round(first, 2),
            "rss_warm": round(rss_warm, 2),
            "rss_end": round(last, 2),
            "rss_min": round(min(rss), 2),
            "rss_max": round(max(rss), 2),
            "rss_delta": round(last - first, 2),
            "rss_delta_pct": round((last - first) / first * 100.0, 2) if first else 0.0,
            "rss_delta_warm": round(last - rss_warm, 2),
            "warmup_s": WARMUP_S,
            "tail_samples": len(tail),
            "slope_mb_per_hour": round(slope_mb_h, 3),
            "trend_reliable": reliable,
            "threads": samples[-1].threads,
            "sys_used_pct": samples[-1].sys_used_pct,
            "sys_avail_gb": samples[-1].sys_avail_gb,
        }

    def latest(self) -> MemSample | None:
        return self.history[-1] if self.history else None

    @staticmethod
    def as_dict(s: MemSample) -> dict:
        return asdict(s)
