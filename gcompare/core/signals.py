# ═══ gcompare/core/signals.py ══════════════════════════════════════════
# РОЛЬ        : Signal/Slot: connect / emit / disconnect / clear — аналог сигналов Qt
# ИСПОЛЬЗУЮТ  : session.py, model.py, diagnostics.py, tk_app.py, wx_app.py
# СМОТРИ ТАКЖЕ: ARCHITECTURE.md, §5
# ──────────────────────────────────────────────────────────────────────
"""Минимальная реализация архитектуры Signal/Slot (наблюдатель).

Зачем свой класс, если в Qt есть ``pyqtSignal``?
    Tkinter и wxPython нативных сигналов не имеют. Чтобы все три приложения
    имели ОДИН и тот же контракт обмена событиями, используется этот класс.
    Qt-приложение дополнительно использует ``pyqtSignal``/``pyqtSlot`` для
    нативных кнопок, но шина контроллера везде одна и та же.

Правила:
    * ``emit`` никогда не пробрасывает исключение слота наружу — падение
      одного обработчика не должно ломать GUI-цикл;
    * повторный ``connect`` одного и того же метода игнорируется;
    * ``disconnect``/``clear`` обязательны при закрытии окна, иначе
      уничтоженные виджеты останутся в списке подписчиков (классическая
      утечка в долгоживущих GUI-приложениях).
"""

from __future__ import annotations

import logging
from typing import Any, Callable, List, Optional, Tuple

log = logging.getLogger(__name__)

Slot = Callable[..., Any]


def _key(slot: Slot) -> Tuple[Any, Any]:
    """Нормализует слот для сравнения: bound method сравнивается по (obj, func)."""
    owner = getattr(slot, "__self__", None)
    func = getattr(slot, "__func__", None)
    if owner is not None and func is not None:
        return (id(owner), id(func))
    return (None, id(slot))


class Signal:
    """Один канал событий: connect / disconnect / emit."""

    __slots__ = ("name", "_slots")

    def __init__(self, name: str = "signal") -> None:
        self.name = name
        self._slots: List[Tuple[Tuple[Any, Any], Slot]] = []

    # ------------------------------------------------------------------ API
    def connect(self, slot: Slot) -> Slot:
        if slot is None:
            raise ValueError("slot не может быть None")
        key = _key(slot)
        if all(k != key for k, _ in self._slots):
            self._slots.append((key, slot))
        return slot

    def disconnect(self, slot: Optional[Slot] = None) -> None:
        if slot is None:
            self.clear()
            return
        key = _key(slot)
        self._slots = [(k, s) for k, s in self._slots if k != key]

    def clear(self) -> None:
        self._slots.clear()

    def emit(self, *args: Any, **kwargs: Any) -> int:
        """Вызывает все слоты. Возвращает число успешно отработавших."""
        ok = 0
        # Копия списка: слот вправе отключиться во время обработки.
        for _key_, slot in list(self._slots):
            try:
                slot(*args, **kwargs)
                ok += 1
            except Exception:  # noqa: BLE001 — GUI не должен падать из-за слота
                log.exception("Ошибка в слоте сигнала %r", self.name)
        return ok

    def __len__(self) -> int:
        return len(self._slots)


class SignalGroup:
    """Удобный доступ к набору именованных сигналов: ``group.data_ready``."""

    def __init__(self, **signals: Signal) -> None:
        self._signals = signals
        for name, sig in signals.items():
            setattr(self, name, sig)

    def clear(self) -> None:
        for sig in self._signals.values():
            sig.clear()
