# ═══ run.py ════════════════════════════════════════════════════════════
# РОЛЬ        : точка входа: GUI-лаунчер и CLI (--app / --compare)
# ИСПОЛЬЗУЮТ  : вызывается пользователем; использует registry, launcher_tk, core/report
# СМОТРИ ТАКЖЕ: ARCHITECTURE.md, §2
# ──────────────────────────────────────────────────────────────────────
"""Точка входа проекта: лаунчер и CLI.

GUI-режим (нужен tkinter — он есть в стандартной сборке python.org)::

    python run.py

CLI-режим (работает даже без tkinter)::

    python run.py --app tk   --duration 3600 --stress
    python run.py --app qt   --duration 600
    python run.py --app wx
    python run.py --app all  --duration 3600
    python run.py --compare
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gcompare.core.data import N_POINTS                             # noqa: E402
from gcompare.core.report import (                                  # noqa: E402
    best_per_toolkit, format_conclusions, format_table, load_summaries, write_markdown,
)
from gcompare.registry import APPS, ORDER, child_cmd                # noqa: E402


# ============================================================ CLI-режим
def cli_mode(app: str, duration: float, stress: bool, points: int) -> int:
    keys = ORDER if app == "all" else (app,)
    procs: list[subprocess.Popen] = []
    for key in keys:
        cmd = child_cmd(key, duration, stress, points)
        print(f"[launcher] {APPS[key]['title']}: {' '.join(cmd)}")
        procs.append(subprocess.Popen(cmd, cwd=str(ROOT)))
    codes = [p.wait() for p in procs]
    print(f"[launcher] процессы завершены, коды: {codes}")
    return 0 if all(c == 0 for c in codes) else 1


def compare_mode() -> int:
    best = best_per_toolkit(load_summaries(ROOT / "logs"))
    print(format_table(best))
    print()
    print(format_conclusions(best))
    if best:
        path = write_markdown(best, ROOT / "logs" / "comparison.md")
        print(f"\nMarkdown: {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Сравнение GUI-библиотек Python + Matplotlib")
    p.add_argument("--app", choices=[*ORDER, "all"], help="запустить без GUI-лаунчера")
    p.add_argument("--duration", type=float, default=0.0,
                   help="автостоп через N секунд (3600 = часовой прогон)")
    p.add_argument("--stress", action="store_true", help="включить авто-перерисовку")
    p.add_argument("--points", type=int, default=N_POINTS, help="точек на кадр")
    p.add_argument("--compare", action="store_true", help="только сводка по logs/")
    args = p.parse_args(argv)

    if args.compare:
        return compare_mode()
    if args.app:
        return cli_mode(args.app, args.duration, args.stress, args.points)

    try:
        from gcompare.launcher_tk import main as launcher_main
    except ModuleNotFoundError as exc:
        print("GUI-лаунчер требует tkinter, которого нет в этом интерпретаторе.\n"
              f"{exc}\n"
              "Запустите приложение напрямую, например:\n"
              "  python run.py --app qt --duration 3600 --stress\n"
              "  python run.py --compare\n"
              "или используйте Python с сайта python.org (в нём tkinter есть).")
        return 2
    return launcher_main()


if __name__ == "__main__":
    raise SystemExit(main())
