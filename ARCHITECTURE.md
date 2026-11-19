# Карта проекта

Документ отвечает на вопрос «что за что отвечает». Если открыть любой `.py`,
в первых строках будет такая же шапка: **РОЛЬ → ИСПОЛЬЗУЮТ → СМОТРИ ТАКЖЕ**.

---

## 1. Три слоя

```
┌──────────────────────────────────────────────────────────────┐
│  apps/            ГРАНИЦА С ПОЛЬЗОВАТЕЛЕМ                    │
│  tk_app.py   qt_app.py   wx_app.py                           │
│  только виджеты, раскладка, canvas и подписи                 │
└───────────────▲──────────────────────────┬───────────────────┘
                │ сигналы (кадр/метрики)   │ команды (кнопки)
┌───────────────┴──────────────────────────▼───────────────────┐
│  core/session.py   SessionController — шина и учёт сессии    │
│  ─────────────────────────────────────────────────────────   │
│  core/model.py     данные + зум + пределы осей               │
│  core/metrics.py   RSS / RAM / тренд                         │
│  core/diagnostics.py  события + текст отчёта                 │
│  core/session_log.py  CSV / JSON / TXT                       │
└───────────────▲──────────────────────────┬───────────────────┘
                │                          │
        core/data.py, core/signals.py, core/view_utils.py,
        core/theme.py, core/cli.py, core/report.py
```

Правило: **`core/` не импортирует ни Tkinter, ни PyQt6, ни wxPython.**
Поэтому логика тестируется без окон (`tools/smoke_test.py`), а три приложения
отличаются только виджетами.

---

## 2. Файлы

| Файл | Роль | Ключевые сущности |
|---|---|---|
| `run.py` | точка входа: лаунчер и CLI | `main()`, `cli_mode()`, `compare_mode()` |
| `start.cmd` | запуск в обход «сломанного» `python` в PATH | поиск интерпретатора по цепочке |
| `gcompare/registry.py` | метаданные приложений и команда запуска | `APPS`, `child_cmd()` |
| `gcompare/launcher_tk.py` | окно лаунчера | `Launcher` |
| `core/signals.py` | Signal/Slot без Qt | `Signal`, `SignalGroup` |
| `core/data.py` | генераторы рядов | `generate()`, `GRAPH_TYPES`, `N_POINTS` |
| `core/model.py` | данные + окно зума + пределы осей | `PlotModel`, `ViewPayload` |
| `core/view_utils.py` | клиппинг, проверка кадра, стиль осей | `enforce_clipping()`, `check_bounds()`, `configure_matplotlib()` |
| `core/metrics.py` | замеры памяти и тренд | `MemoryProbe`, `MemSample`, `WARMUP_S` |
| `core/diagnostics.py` | события, тайминги, отчёт | `Diagnostics.build_report()` |
| `core/session.py` | контроллер сессии | `SessionController`, `SessionOptions` |
| `core/session_log.py` | запись результатов | `SessionLog` |
| `core/theme.py` | палитра, подписи, `AXES_RECT` | константы |
| `core/cli.py` | общие флаги CLI | `build_parser()` |
| `core/report.py` | сводное сравнение трёх сессий | `format_table()`, `write_markdown()` |
| `apps/tk_app.py` | приложение №1 | `TkCompareApp`, `AppSignals` |
| `apps/qt_app.py` | приложение №2 | `QtCompareWindow`, `Bridge`, `ZoomCanvas` |
| `apps/wx_app.py` | приложение №3 | `WxCompareFrame` |
| `tools/smoke_test.py` | 7 проверок ядра (+ `--gui`) | `test_*` |
| `tools/soak_test.py` | нагрузка без окон | `HeadlessCanvas` |

Порядок чтения для новичка:
`README.md` → `core/model.py` → `core/session.py` → `apps/qt_app.py`.

---

## 3. Путь одного кадра

```
пользователь нажал «Показать график»
   └─ tk/qt/wx: кнопка → сигнал → SessionController.show_graph(kind)   [core/session.py]
        └─ PlotModel.generate(kind)                                    [core/model.py]
             └─ data.generate()  → новые x, y (фиксированная длина)    [core/data.py]
        └─ model.data_changed.emit(payload)  → SessionController._on_payload
             └─ controller.render_request.emit(ViewPayload)
                  └─ приложение: _render(payload)                      [apps/*]
                       ├─ line.set_data(x, y)        одна и та же линия
                       ├─ ax.set_xlim / set_ylim     из видимого среза
                       ├─ enforce_clipping(...)      clip_on + clip_path(ax.patch)
                       ├─ check_bounds(...)          сколько точек вне кадра (должно 0)
                       └─ canvas.draw()
                  └─ controller.record_render(ms, points, violations)
                       └─ Diagnostics.track_render → тайминги и дефекты
```

Зум — тот же путь, только через `zoom_in()/zoom_out()` → `view_changed`.

Метрики — отдельный таймер раз в секунду: `controller.tick()` →
`MemoryProbe.sample()` → сигнал `memory_update` → подписи в панели.

---

## 4. «Хочу изменить…»

| Задача | Файл |
|---|---|
| другой набор тестовых рядов | `core/data.py` (функция `generate`) |
| сколько точек на кадр | флаг `--points` или `core/data.py: N_POINTS` |
| правила зума (шаг, минимум окна) | `core/model.py`: `ZOOM_FACTOR`, `MIN_WINDOW_POINTS` |
| пороги «утечка / внимание» | `core/diagnostics.py`: `LEAK_*`, `RENDER_*` |
| окно прогрева памяти | `core/metrics.py`: `WARMUP_S` |
| текст отчёта | `core/diagnostics.py: build_report()` |
| цвета и подписи | `core/theme.py` |
| добавить кнопку/поле в окно | соответствующий `apps/*_app.py`, метод `_build_*` |
| скорость отрисовки | `core/view_utils.py: configure_matplotlib()` |
| формат логов | `core/session_log.py` |
| сводная таблица | `core/report.py` |

---

## 5. Инварианты

1. **Линия не выходит за рамку** — четыре уровня: `add_axes(theme.AXES_RECT)`,
   геометрия контейнера (place / layout / sizer), `enforce_clipping()` на каждом
   кадре, `check_bounds()` → счётчик в отчёте.
2. **Нет утечек** — одна `Line2D` на всё время жизни, `set_data` вместо `plot`;
   `deque(maxlen=...)` для истории; журнал ограничен; таймеры отменяются,
   сигналы чистятся, `Figure` освобождается.
3. **Одинаковое поведение** — все три окна работают через один
   `SessionController`; различия только в виджетах.

---

## 6. Что где в окне

```
┌────────────────────────────────────────────┬──────────────────────┐
│ заголовок: тип · точек · зум · время кадра │ ПАМЯТЬ ПРОЦЕССА      │
│ ┌────────────────────────────────────────┐ │  RSS, Δ, тренд, потоки│
│ │ canvas в рамке (единственная область   │ ├──────────────────────┤
│ │ рисования, clip = ON)                  │ │ СИСТЕМНАЯ RAM        │
│ │                                        │ ├──────────────────────┤
│ └────────────────────────────────────────┘ │ ГРАФИК (тип + кнопка)│
│ подпись: clip = ON · линия в рамке axes    ├──────────────────────┤
│                                            │ МАСШТАБ (In/Out/Сброс)│
│                                            ├──────────────────────┤
│                                            │ СЕССИЯ (uptime, кадры)│
│                                            ├──────────────────────┤
│                                            │ стресс-тест, отчёт    │
│                                            ├──────────────────────┤
│                                            │ ДИАГНОСТИКА (журнал)  │
└────────────────────────────────────────────┴──────────────────────┘
```

---

## 7. Файлы, которые создаются во время работы

| Что | Где | Кто пишет |
|---|---|---|
| замеры (1 строка/сек) | `logs/<toolkit>_<дата>.csv` | `core/session_log.py` |
| итоговый отчёт | `logs/<toolkit>_<дата>_report.txt` | `core/diagnostics.py` → `session_log` |
| сводка для сравнения | `logs/<toolkit>_<дата>_summary.json` | `core/session.py: finish()` |
| сравнение трёх | `logs/comparison.md` | `core/report.py` |

---

## 8. Проверки

```powershell
python tools/smoke_test.py          # 7 проверок ядра
python tools/smoke_test.py --gui    # + пробный запуск окон по 4 с
python tools/soak_test.py --duration 60 --fps 10   # нагрузка без GUI
npx pyright --level error gcompare tools run.py     # типы (0 ошибок)
python -m ruff check --select F,E9 .                # мёртвый код
```
