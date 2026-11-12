"""Реестр метрик + автоподхват из metrics/.

Пользователь (или нейросеть) кладёт в metrics/*.py класс-наследник BaseMetric —
при старте сервера/CLI файлы импортируются, классы регистрируются сами,
декораторы не нужны. Файлы и папки, начинающиеся с "_" или ".", игнорируются.
Ошибка в одном файле не мешает остальным (подробности — load_errors()).
"""
from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path

from .base import BaseMetric

log = logging.getLogger(__name__)

METRICS: dict[str, BaseMetric] = {}
_LOAD_ERRORS: dict[str, str] = {}   # путь файла -> причина
_loaded = False


def register(metric: BaseMetric | type) -> BaseMetric:
    """Зарегистрировать метрику (экземпляр или класс). Возвращает экземпляр."""
    m = metric() if isinstance(metric, type) else metric
    if not getattr(m, "id", ""):
        raise ValueError(f"{type(m).__name__}: пустое поле id")
    existing = METRICS.get(m.id)
    if existing is not None and type(existing) is not type(m):
        log.warning("Метрика %r: класс %s перезаписывает %s",
                    m.id, type(m).__name__, type(existing).__name__)
    METRICS[m.id] = m
    return m


def discover(folder: Path, *, force: bool = False) -> None:
    """Импортировать все *.py в folder (рекурсивно) и зарегистрировать
    найденных наследников BaseMetric, определённых в этих файлах."""
    global _loaded
    if _loaded and not force:
        return
    _loaded = True
    if not folder.is_dir():
        log.warning("Папка метрик %s не существует — метрик нет", folder)
        return
    for path in sorted(folder.rglob("*.py")):
        rel = path.relative_to(folder)
        if path.name.startswith("_") or any(
                p.startswith((".", "_")) for p in rel.parts[:-1]):
            continue
        modname = "metric_" + "_".join(rel.with_suffix("").parts)
        try:
            spec = importlib.util.spec_from_file_location(modname, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[modname] = module
            spec.loader.exec_module(module)
            found = [obj for obj in vars(module).values()
                     if isinstance(obj, type) and issubclass(obj, BaseMetric)
                     and obj.__module__ == modname]
            for cls in found:
                register(cls)
            log.debug("metrics: %s -> %s", rel, [c.__name__ for c in found] or "-")
        except Exception as exc:  # битый файл не должен валить остальные
            _LOAD_ERRORS[str(rel)] = f"{type(exc).__name__}: {exc}"
            log.error("Метрика не загрузилась (%s): %s", rel, exc)


def ensure_discovered() -> None:
    """Однократный автоподхват из metrics/ в корне проекта."""
    if _loaded:
        return
    from src.core.config import PROJECT_ROOT
    discover(PROJECT_ROOT / "metrics")


def load_errors() -> dict[str, str]:
    """Ошибки загрузки файлов метрик (для тест-харнесса и лога)."""
    return dict(_LOAD_ERRORS)
