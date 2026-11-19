# ═══ gcompare/core/session_log.py ══════════════════════════════════════
# РОЛЬ        : запись телеметрии на диск: CSV по секундам, JSON-сводка, TXT-отчёт
# ИСПОЛЬЗУЮТ  : session.py (finish)
# СМОТРИ ТАКЖЕ: ARCHITECTURE.md, §6
# ──────────────────────────────────────────────────────────────────────
"""Запись телеметрии сессии на диск.

Файлы пишутся построчно и сразу сбрасываются на диск, поэтому даже аварийное
закрытие окна не теряет данные. Формат намеренно простой:

    logs/<toolkit>_<stamp>.csv            — замеры (1 строка/сек)
    logs/<toolkit>_<stamp>_report.txt     — итоговый отчёт
    logs/<toolkit>_<stamp>_summary.json   — машинно-читаемая сводка
"""

from __future__ import annotations

import csv
import json
import time
from pathlib import Path

from .metrics import MemSample

CSV_HEADER = ["uptime_s", "rss_mb", "vms_mb", "threads", "sys_used_pct", "sys_avail_gb", "render_ms"]


class SessionLog:
    """Писатель телеметрии одной сессии."""

    def __init__(self, toolkit: str, log_dir: Path | str = "logs") -> None:
        self.dir = Path(log_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in toolkit).lower()
        self.base = self.dir / f"{safe}_{stamp}"

        self.csv_path = self.base.with_suffix(".csv")
        self.report_path = self.base.parent / f"{self.base.name}_report.txt"
        self.summary_path = self.base.parent / f"{self.base.name}_summary.json"

        self._fh = open(self.csv_path, "w", newline="", encoding="utf-8")
        self._writer = csv.writer(self._fh)
        self._writer.writerow(CSV_HEADER)
        self._fh.flush()
        self._closed = False

    # ------------------------------------------------------------- запись
    def row(self, s: MemSample, render_ms: float | None = None) -> None:
        if self._closed:
            return
        self._writer.writerow([
            s.uptime, s.rss_mb, s.vms_mb, s.threads,
            s.sys_used_pct, s.sys_avail_gb,
            "" if render_ms is None else round(render_ms, 2),
        ])
        self._fh.flush()  # 1 строка/сек — стоимость пренебрежима

    def write_summary(self, summary: dict, report: str) -> None:
        """Сохраняет сводку и отчёт. Идемпотентно."""
        self.summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self.report_path.write_text(report, encoding="utf-8")

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            try:
                self._fh.close()
            except Exception:  # noqa: BLE001
                pass

    def __enter__(self) -> "SessionLog":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
