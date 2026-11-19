# ═══ gcompare/launcher_tk.py ═══════════════════════════════════════════
# РОЛЬ        : окно лаунчера: запуск приложений и сборка сводного отчёта
# ИСПОЛЬЗУЮТ  : run.py (ленивый импорт, нужен tkinter)
# СМОТРИ ТАКЖЕ: ARCHITECTURE.md, §2
# ──────────────────────────────────────────────────────────────────────
"""Лаунчер на Tkinter: запуск трёх приложений и сборка сравнения.

Импортируется лениво из ``run.py`` — если в интерпретаторе нет tkinter,
CLI-режим (``--app``/``--compare``) продолжает работать.
"""

from __future__ import annotations

import os
import subprocess
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from .core import theme as th
from .core.data import N_POINTS
from .core.report import (
    best_per_toolkit, format_conclusions, format_table, load_summaries, write_markdown,
)
from .registry import APPS, ORDER, ROOT, child_cmd


class Launcher(tk.Tk):
    """Окно запуска трёх приложений и сборки сводного отчёта."""

    def __init__(self) -> None:
        super().__init__()
        self.title(th.APP_TITLE + " — лаунчер")
        self.configure(bg=th.BG)
        self.geometry("1060x740")
        self.minsize(960, 660)

        self.procs: dict[str, subprocess.Popen] = {}
        self.var_duration = tk.StringVar(value="3600")
        self.var_points = tk.StringVar(value=str(N_POINTS))
        self.var_stress = tk.BooleanVar(value=True)
        self.status_labels: dict[str, tk.StringVar] = {}

        self._build_styles()
        self._build()
        self.after(1000, self._poll)

    # ------------------------------------------------------------- оформление
    def _build_styles(self) -> None:
        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure(".", background=th.BG, foreground=th.FG, font=("Segoe UI", 9))
        st.configure("TFrame", background=th.BG)
        st.configure("Card.TFrame", background=th.PANEL, relief="flat")
        st.configure("TLabel", background=th.BG, foreground=th.FG)
        st.configure("Title.TLabel", background=th.BG, foreground=th.FG,
                     font=("Segoe UI", 16, "bold"))
        st.configure("Sub.TLabel", background=th.BG, foreground=th.MUTED, font=("Segoe UI", 9))
        st.configure("CardTitle.TLabel", background=th.PANEL, foreground=th.ACCENT,
                     font=("Segoe UI", 11, "bold"))
        st.configure("CardText.TLabel", background=th.PANEL, foreground=th.MUTED,
                     font=("Segoe UI", 8))
        st.configure("Status.TLabel", background=th.PANEL, foreground=th.OK,
                     font=("Segoe UI", 8, "bold"))
        st.configure("TButton", background=th.PANEL_ALT, foreground=th.FG,
                     borderwidth=0, padding=(8, 5), focusthickness=0)
        st.map("TButton", background=[("active", "#2b3242")])
        st.configure("Accent.TButton", background=th.ACCENT, foreground="#08111c",
                     font=("Segoe UI", 9, "bold"), padding=(8, 6))
        st.map("Accent.TButton", background=[("active", "#74bcff")])
        st.configure("TCheckbutton", background=th.BG, foreground=th.FG)
        st.configure("TEntry", fieldbackground=th.PANEL_ALT, foreground=th.FG,
                     insertcolor=th.FG, borderwidth=0)

    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        head = ttk.Frame(self)
        head.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 6))
        ttk.Label(head, text=th.APP_TITLE, style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            head,
            text="Три GUI-библиотеки · одинаковый функционал · жёсткая рамка графика · "
                 "часовой тест памяти",
            style="Sub.TLabel",
        ).pack(anchor="w", pady=(2, 0))

        cards = ttk.Frame(self)
        cards.grid(row=1, column=0, sticky="ew", padx=16, pady=8)
        for i, key in enumerate(ORDER):
            cards.grid_columnconfigure(i, weight=1, uniform="cards")
            self._build_card(cards, key).grid(row=0, column=i, sticky="nsew",
                                              padx=(0 if i == 0 else 8, 0))

        controls = ttk.Frame(self)
        controls.grid(row=2, column=0, sticky="ew", padx=16, pady=(4, 8))
        ttk.Label(controls, text="Длительность, с:").pack(side="left")
        ttk.Entry(controls, textvariable=self.var_duration, width=7).pack(side="left", padx=(6, 14))
        ttk.Label(controls, text="Точек на кадр:").pack(side="left")
        ttk.Entry(controls, textvariable=self.var_points, width=7).pack(side="left", padx=(6, 14))
        ttk.Checkbutton(controls, text="Стресс-тест (авто-перерисовка)",
                        variable=self.var_stress).pack(side="left", padx=(0, 14))
        ttk.Button(controls, text="Запустить все три", style="Accent.TButton",
                   command=self.start_all).pack(side="left")
        ttk.Button(controls, text="Остановить все", command=self.stop_all).pack(side="left", padx=6)
        ttk.Button(controls, text="Собрать сравнение", command=self.compare).pack(side="left")
        ttk.Button(controls, text="Открыть логи", command=self.open_logs).pack(side="left", padx=6)

        self.txt = tk.Text(self, height=14, wrap="none", bd=0, highlightthickness=1,
                           highlightbackground=th.BORDER, bg=th.PLOT_BG, fg=th.MUTED,
                           insertbackground=th.FG, font=("Consolas", 9), padx=8, pady=6,
                           state="disabled")
        self.txt.grid(row=3, column=0, sticky="nsew", padx=16, pady=(0, 14))

        self._log("Готово. Выберите библиотеку или «Запустить все три».")
        self._log("Часовой тест: длительность 3600 + стресс-тест. Отчёт печатается в консоль "
                  "приложения (при закрытии окна) и сохраняется в logs/.")

    def _build_card(self, parent: ttk.Frame, key: str) -> ttk.Frame:
        card = ttk.Frame(parent, style="Card.TFrame", padding=12)
        ttk.Label(card, text=APPS[key]["title"], style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(card, text=APPS[key]["canvas"], style="CardText.TLabel").pack(anchor="w")
        ttk.Label(card, text=APPS[key]["note"], style="CardText.TLabel",
                  justify="left", wraplength=232).pack(anchor="w", pady=(6, 8))
        var = tk.StringVar(value="○ остановлено")
        self.status_labels[key] = var
        ttk.Label(card, textvariable=var, style="Status.TLabel").pack(anchor="w", pady=(0, 8))
        row = ttk.Frame(card, style="Card.TFrame")
        row.pack(fill="x")
        ttk.Button(row, text="Запустить", style="Accent.TButton",
                   command=lambda k=key: self.start(k)).pack(side="left", expand=True, fill="x")
        ttk.Button(row, text="Стоп",
                   command=lambda k=key: self.stop(k)).pack(side="left", padx=(6, 0))
        return card

    # --------------------------------------------------------------- действия
    def _duration(self) -> float:
        try:
            return max(0.0, float(self.var_duration.get().strip() or 0))
        except ValueError:
            return 0.0

    def _points(self) -> int:
        try:
            return max(64, int(self.var_points.get().strip() or N_POINTS))
        except ValueError:
            return N_POINTS

    def start(self, key: str) -> None:
        proc = self.procs.get(key)
        if proc is not None and proc.poll() is None:
            self._log(f"{APPS[key]['title']}: уже запущено (pid {proc.pid})")
            return
        cmd = child_cmd(key, self._duration(), self.var_stress.get(), self._points())
        try:
            self.procs[key] = subprocess.Popen(cmd, cwd=str(ROOT))
            self._log(f"{APPS[key]['title']}: запуск → {' '.join(cmd[1:])}")
        except Exception as exc:  # noqa: BLE001
            self._log(f"{APPS[key]['title']}: ОШИБКА запуска — {exc}")

    def start_all(self) -> None:
        for key in ORDER:
            self.start(key)

    def stop(self, key: str) -> None:
        proc = self.procs.get(key)
        if proc is None or proc.poll() is not None:
            self._log(f"{APPS[key]['title']}: не запущено")
            return
        proc.terminate()
        self._log(f"{APPS[key]['title']}: остановка (закрытие окна сохранит отчёт)")

    def stop_all(self) -> None:
        for key in ORDER:
            self.stop(key)

    def compare(self) -> None:
        best = best_per_toolkit(load_summaries(ROOT / "logs"))
        self._log("")
        self._log("── СВОДКА " + "─" * 50)
        for line in format_table(best).splitlines():
            self._log(line)
        self._log("")
        for line in format_conclusions(best).splitlines():
            self._log(line)
        if best:
            path = write_markdown(best, ROOT / "logs" / "comparison.md")
            self._log(f"\nMarkdown сохранён: {path}")

    def open_logs(self) -> None:
        path: Path = ROOT / "logs"
        path.mkdir(exist_ok=True)
        try:
            os.startfile(str(path))  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            self._log(f"Каталог: {path}")

    def _log(self, message: str) -> None:
        self.txt.configure(state="normal")
        self.txt.insert("end", message + "\n")
        if int(self.txt.index("end-1c").split(".")[0]) > 400:
            self.txt.delete("1.0", "100.0")
        self.txt.see("end")
        self.txt.configure(state="disabled")

    def _poll(self) -> None:
        for key, var in self.status_labels.items():
            proc = self.procs.get(key)
            if proc is None:
                var.set("○ остановлено")
            elif proc.poll() is None:
                var.set(f"● работает · pid {proc.pid}")
            else:
                var.set(f"○ завершено · код {proc.returncode}")
        self.after(1000, self._poll)


def main() -> int:
    th.enable_windows_dpi_awareness()
    Launcher().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
