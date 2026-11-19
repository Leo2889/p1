# ═══ gcompare/core/report.py ═══════════════════════════════════════════
# РОЛЬ        : сводное сравнение трёх сессий: таблица, выводы, comparison.md
# ИСПОЛЬЗУЮТ  : run.py --compare, launcher_tk.py
# СМОТРИ ТАКЖЕ: ARCHITECTURE.md, §6
# ──────────────────────────────────────────────────────────────────────
"""Сводный отчёт по трём сессиям: консольная таблица + comparison.md."""

from __future__ import annotations

import json
import time
from pathlib import Path

VERDICT_OK = "норма"
VERDICT_WARN = "внимание"
VERDICT_LEAK = "утечка?"
VERDICT_CLIP = "клиппинг!"
VERDICT_SHORT = "мало данных"

#: Приложения, которые участвуют в сравнении (headless-прогоны — нет).
KNOWN_TOOLKITS = ("Tkinter", "PyQt6", "wxPython")

COLUMNS = (
    ("Библиотека", 20),
    ("Canvas", 20),
    ("Время", 9),
    ("Кадров", 8),
    ("p95 мс", 8),
    ("RSS старт", 10),
    ("RSS конец", 10),
    ("Δ всего", 9),
    ("Δ прогр.", 9),
    ("МБ/ч", 8),
    ("Клип", 6),
    ("Вердикт", 12),
)


def verdict_for(summary: dict) -> str:
    """Короткий вердикт по сводке одной сессии.

    Тренд памяти учитывается только если он надёжен (≥ 90 с наблюдения):
    иначе прирост RSS — это прогрев интерпретатора и кэшей canvas.
    """
    mem = summary.get("memory") or {}
    slope = abs(float(mem.get("slope_mb_per_hour", 0.0)))
    # Судим по росту ПОСЛЕ прогрева: стартовый скачок RSS — не утечка.
    delta = abs(float(mem.get("rss_delta_warm", mem.get("rss_delta", 0.0))))
    if int(summary.get("clip_violations", 0)) > 0:
        return VERDICT_CLIP
    if not mem.get("trend_reliable", False):
        return VERDICT_SHORT
    # Сначала абсолютная значимость: доли мегабайта — шум аллокатора,
    # экстраполировать их на час нельзя.
    if delta < 5.0:
        return VERDICT_OK
    if slope >= 8.0 or delta >= 60.0:
        return VERDICT_LEAK
    if slope >= 2.0 or delta >= 20.0:
        return VERDICT_WARN
    return VERDICT_OK


def load_summaries(log_dir: str | Path = "logs") -> list[dict]:
    items: list[dict] = []
    for path in sorted(Path(log_dir).glob("*_summary.json")):
        try:
            items.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001 — битый файл не должен ломать сводку
            continue
    return items


def best_per_toolkit(summaries: list[dict], *, known_only: bool = True) -> dict[str, dict]:
    """Для каждой библиотеки берём самую длинную сессию."""
    best: dict[str, dict] = {}
    for s in summaries:
        name = s.get("toolkit", "?")
        if known_only and name not in KNOWN_TOOLKITS:
            continue
        if name not in best or float(s.get("uptime_s", 0)) > float(best[name].get("uptime_s", 0)):
            best[name] = s
    return best


def slope_text(mem: dict) -> str:
    """Тренд для таблицы: шум не показываем как «+25.57 МБ/час»."""
    if not mem.get("trend_reliable", False):
        return "—"
    if abs(float(mem.get("rss_delta_warm", 0.0))) < 5.0:
        return "≈0"
    return f"{mem.get('slope_mb_per_hour', 0.0):+.2f}"


def _row(values: list[str]) -> str:
    cells = [str(v)[:w].ljust(w) for v, (_, w) in zip(values, COLUMNS)]
    return " | ".join(cells)


def format_table(best: dict[str, dict]) -> str:
    """Текстовая таблица для консоли и панели лаунчера."""
    if not best:
        return "Сводок пока нет. Запустите приложение и дайте ему поработать."

    lines = [_row([c for c, _ in COLUMNS]), "-" * (sum(w for _, w in COLUMNS) + 3 * len(COLUMNS))]
    for name in ("Tkinter", "PyQt6", "wxPython"):
        s = best.get(name)
        if not s:
            continue
        mem = s.get("memory") or {}
        rs = s.get("render_ms") or {}
        uptime = float(s.get("uptime_s", 0))
        lines.append(_row([
            name,
            s.get("canvas", "?"),
            f"{uptime / 3600:.1f} ч" if uptime >= 3600 else f"{uptime / 60:.1f} мин",
            f"{s.get('renders', 0):,}".replace(",", " "),
            f"{rs.get('p95', 0):.1f}",
            f"{mem.get('rss_start', 0):.1f}",
            f"{mem.get('rss_end', 0):.1f}",
            f"{mem.get('rss_delta', 0):+.1f}",
            f"{mem.get('rss_delta_warm', 0):+.1f}",
            slope_text(mem),
            str(s.get("clip_violations", 0)),
            verdict_for(s),
        ]))
    return "\n".join(lines)


def format_conclusions(best: dict[str, dict]) -> str:
    """Короткие выводы: скорость, память, надёжность клиппинга."""
    if not best:
        return ""
    out: list[str] = []
    fastest = min(best.values(), key=lambda s: (s.get("render_ms") or {}).get("p95", 1e9))
    lightest = min(best.values(), key=lambda s: (s.get("memory") or {}).get("rss_end", 1e9))
    leaks = [n for n, s in best.items() if verdict_for(s) in (VERDICT_LEAK, VERDICT_WARN)]
    short = [n for n, s in best.items() if verdict_for(s) == VERDICT_SHORT]

    out.append(f"· Самый быстрый кадр: {fastest['toolkit']} "
               f"(p95 {(fastest.get('render_ms') or {}).get('p95', 0):.1f} мс)")
    out.append(f"· Наименьший RSS в конце: {lightest['toolkit']} "
               f"({(lightest.get('memory') or {}).get('rss_end', 0):.1f} МБ)")
    out.append("· Память без замечаний: " + (", ".join(
        n for n, s in best.items() if verdict_for(s) == VERDICT_OK) or "нет"))
    if leaks:
        out.append("· Требуют контроля по памяти: " + ", ".join(leaks))
    if short:
        out.append("· Мало данных для вывода об утечке (нужен прогон ≥ 5 мин): " + ", ".join(short))
    clip_bad = [n for n, s in best.items() if int(s.get("clip_violations", 0)) > 0]
    out.append("· Клиппинг: " + ("нарушен у " + ", ".join(clip_bad) if clip_bad
                                 else "у всех трёх приложений линия остаётся внутри рамки"))
    return "\n".join(out)


def write_markdown(best: dict[str, dict], path: str | Path = "logs/comparison.md") -> Path:
    """Сохраняет сравнение в Markdown."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    header = ["Библиотека", "Canvas", "Время", "Кадров", "p95, мс", "RSS старт, МБ",
              "RSS конец, МБ", "Δ всего, МБ", "Δ после 60 с, МБ", "Тренд, МБ/ч",
              "Клип", "Вердикт"]
    rows = ["| " + " | ".join(header) + " |",
            "|" + "|".join(["---"] * len(header)) + "|"]
    ordered = [n for n in KNOWN_TOOLKITS if n in best] + \
              [n for n in best if n not in KNOWN_TOOLKITS]
    for name in ordered:
        s = best[name]
        mem = s.get("memory") or {}
        rs = s.get("render_ms") or {}
        rows.append("| " + " | ".join([
            name,
            str(s.get("canvas", "?")),
            f"{float(s.get('uptime_s', 0)) / 60:.1f} мин",
            str(s.get("renders", 0)),
            f"{rs.get('p95', 0):.1f}",
            f"{mem.get('rss_start', 0):.1f}",
            f"{mem.get('rss_end', 0):.1f}",
            f"{mem.get('rss_delta', 0):+.1f}",
            f"{mem.get('rss_delta_warm', 0):+.1f}",
            slope_text(mem),
            str(s.get("clip_violations", 0)),
            verdict_for(s),
        ]) + " |")

    text = "\n".join([
        "# Сравнение GUI-библиотек (Matplotlib, одинаковый функционал)",
        "",
        f"Сформировано: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        *rows,
        "",
        "## Выводы",
        "",
        format_conclusions(best),
        "",
        "## Методика",
        "",
        "Одинаковые данные и оформление, один и тот же контроллер сессии. "
        "Замер RSS — psutil раз в секунду. Тренд считается МНК по хвосту сессии "
        "после 60-секундного прогрева: стартовый скачок RSS (импорт модулей, "
        "кэш шрифтов, буферы canvas) — не утечка. Клиппинг проверяется численно "
        "на каждом кадре.",
        "",
    ])
    p.write_text(text, encoding="utf-8")
    return p
