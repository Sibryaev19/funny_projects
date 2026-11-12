"""Тест-харнесс метрик-компонентов.

Запуск (из корня проекта):
    python main.py --check-metrics                     # синтетические таблицы
    python main.py --check-metrics --project CURATOR   # реальная выгрузка, если есть

Проверки: resolve_params (дефолты/типы/required/неизвестные — юнит-тесты ниже);
для каждой зарегистрированной метрики — декларация params валидна, build() не
падает на данных (с дефолтными параметрами), блоки валидны по схеме pydantic,
таблицы прямоугольны, длины серий сходятся с осями диаграмм. Код возврата 1 —
если есть failures или файлы metrics/, не загрузившиеся при импорте.
"""
from __future__ import annotations

import logging
import random
from datetime import timedelta

import pandas as pd

from src.core.config import PROJECT_ROOT
from .base import MetricOpts
from .blocks import MetricResult, sanitize_result
from .params import Param, params_label, resolve_params, validate_decl
from .registry import METRICS, ensure_discovered, load_errors

log = logging.getLogger(__name__)


# ---------------- синтетические данные ----------------

def synthetic_tables() -> dict[str, pd.DataFrame]:
    """Детерминированная мини-выгрузка, на которой осмысленно работают все
    встроенные метрики: циклы, зависшие, переоткрытия, дисциплина."""
    rnd = random.Random(42)
    types = ["Bug", "Task", "Improvement", "New Feature", "Sub-task", "Question"]
    people = ["Аня", "Борис", "Вера", "Гоша", "Даша", "Ева", "Жора", "Инна"]
    statuses = [("Open", "To Do"), ("In Progress", "In Progress"),
                ("Reopened", "In Progress"), ("Resolved", "Done"), ("Closed", "Done")]
    now = pd.Timestamp.now(tz="UTC")

    issues, events, comments = [], [], []
    for i in range(60):
        key = f"TEST-{i + 1}"
        st_name, st_cat = statuses[i % len(statuses)]
        created = now - timedelta(days=120 - 2 * i, hours=rnd.randint(0, 20))
        reporter = people[i % len(people)]
        assignee = reporter if i % 8 == 0 else rnd.choice(people)
        resolved = created + timedelta(days=rnd.randint(1, 40)) if st_cat == "Done" else None
        updated = resolved or created + timedelta(days=rnd.randint(0, 30))
        issues.append({
            "key": key, "summary": f"Синтетическая задача {i + 1}",
            "type": types[i % len(types)], "status": st_name,
            "status_category": st_cat,
            "priority": None if i % 7 == 0 else ["High", "Medium", "Low"][i % 3],
            "assignee": None if i % 9 == 0 else assignee, "reporter": reporter,
            "created": created, "updated": updated, "resolved_at": resolved,
            "resolution": "Done" if resolved else None,
            "duedate": created + timedelta(days=14) if i % 15 == 0 else None,
            "components": "" if i % 6 == 0 else "Core",
            "parent": None, "n_subtasks": rnd.randint(0, 3),
            "timespent_h": round(rnd.uniform(0, 20), 1),
        })

        flow = [("Open", created)]
        if st_cat != "To Do" and i % 13 != 0:   # i%13: часть закрыта минуя работу
            flow.append(("In Progress", created + timedelta(days=rnd.randint(1, 10))))
        if resolved:
            flow.append(("Resolved", resolved))
            if i % 10 == 0:                     # переоткрытие
                flow.append(("Reopened", resolved + timedelta(days=3)))
                flow.append(("Resolved", resolved + timedelta(days=6)))
        prev = ""
        for name, when in flow:
            events.append({"key": key, "time": when, "author": rnd.choice(people),
                           "field": "status", "from": prev, "to": name})
            prev = name
        if i % 5 == 0:
            comments.append({"key": key, "author": rnd.choice(people),
                             "created": created + timedelta(days=1)})

    return {"issues": pd.DataFrame(issues),
            "events": pd.DataFrame(events, columns=["key", "time", "author",
                                                    "field", "from", "to"]),
            "comments": pd.DataFrame(comments, columns=["key", "author", "created"])}


# ---------------- проверки ----------------

def _check_block(b: dict) -> str | None:
    """Структурные проверки одного блока (сверх pydantic-валидации типа)."""
    t = b["type"]
    if t == "table":
        w = len(b["columns"])
        if b.get("align") and len(b["align"]) != w:
            return "align не совпадает с columns по длине"
        for i, row in enumerate(b["rows"]):
            if len(row) != w:
                return f"строка {i}: {len(row)} значений при {w} колонках"
    elif t in ("bar", "line"):
        axis = b["labels"] if t == "bar" else b["x"]
        for s in b["series"]:
            if len(s["values"]) != len(axis):
                return (f"серия '{s['name']}': {len(s['values'])} значений "
                        f"против {len(axis)} подписей оси")
    elif t == "pie":
        for it in b["items"]:
            if not isinstance(it["name"], str):
                return "имя сектора не строка"
    return None


def check_metric(metric, tables: dict[str, pd.DataFrame]) -> str | None:
    """Прогнать метрику с дефолтными параметрами; вернуть текст первой
    найденной проблемы (или None)."""
    try:
        problems = []
        decl_err = validate_decl(metric.params)
        if decl_err:
            return f"декларация params: {decl_err}"
        opts = MetricOpts(params=resolve_params(metric.params, {}))
        blocks = metric.build(tables, opts)
        res = sanitize_result(MetricResult(
            metric_id=metric.id, label=metric.label or metric.id,
            description=metric.description,
            params_label=params_label(metric.params, opts.params), blocks=blocks))
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"
    problems = [err for b in res.model_dump()["blocks"]
                if (err := _check_block(b))]
    return "; ".join(problems) or None


# ---------------- юнит-проверки resolve_params ----------------

def selftest_params() -> list[str]:
    """Юнит-проверки resolve_params/params_label: дефолты, приведение типов,
    required, неизвестные ключи, select-опции, сводка для карточки."""
    decl = [
        Param(name="threshold", label="Порог, дней", type="number", default=14),
        Param(name="date_from", label="Дата с", type="date", required=True),
        Param(name="mode", label="Режим", type="select", options=["a", "b"]),
        Param(name="title", label="Заголовок", type="text"),
    ]
    fails: list[str] = []

    def expect(cond: bool, what: str) -> None:
        if not cond:
            fails.append(what)

    def expect_err(fn, what: str) -> None:
        try:
            fn()
        except ValueError:
            return
        fails.append(what)

    r = resolve_params(decl, {"threshold": "45", "date_from": "2025-01-01"})
    expect(r["threshold"] == 45 and isinstance(r["threshold"], int),
           f"number '45' -> int, получено {r['threshold']!r}")
    expect(r["date_from"] == "2025-01-01", "date остаётся строкой YYYY-MM-DD")
    expect(r["mode"] is None and r["title"] is None, "пустые необязательные -> None")

    r2 = resolve_params(decl, {"threshold": "4,5", "date_from": "2025-02-03"})
    expect(r2["threshold"] == 4.5, "число с запятой -> float 4.5")
    r3 = resolve_params(decl, {"date_from": "2025-06-30"})
    expect(r3["threshold"] == 14, "дефолт подставляется при пустом поле")

    expect_err(lambda: resolve_params(decl, {"date_from": "01.02.2025"}),
               "плохая дата должна давать ValueError")
    expect_err(lambda: resolve_params(decl, {"threshold": "много"}),
               "нечисловое значение number должно давать ValueError")
    expect_err(lambda: resolve_params(decl, {}),
               "пустой required без дефолта должен давать ValueError")
    expect_err(lambda: resolve_params(decl, {"date_from": "2025-01-01", "nonsense": 1}),
               "неизвестный ключ должен давать ValueError")
    expect_err(lambda: resolve_params(
        [Param(name="m", label="М", type="select", options=["x"])],
        {"m": "y"}), "select вне options должен давать ValueError")

    expect(params_label(decl, r) == "порог, дней: 45, дата с: 2025-01-01",
           f"params_label, получено {params_label(decl, r)!r}")
    expect(params_label(decl, {"threshold": 4.5}) == "порог, дней: 4.5",
           "params_label: дробное сохраняет вид 4.5")
    expect(params_label([], {}) == "", "пустая декларация -> пустая сводка")
    return fails


def run_check(project: str | None = None) -> int:
    """Полный прогон: юнит-тесты params, загрузка metrics/, проверка метрик.
    -> код возврата."""
    ensure_discovered()

    fails = 0
    param_errs = selftest_params()
    if param_errs:
        fails += len(param_errs)
        print("Юнит-проверки resolve_params:")
        for e in param_errs:
            print(f"  FAIL {e}")
    else:
        print("Юнит-проверки resolve_params: PASS")

    load_errs = load_errors()
    if load_errs:
        print("Файлы метрик, не загрузившиеся при импорте:")
        for f, e in load_errs.items():
            print(f"  {f}: {e}")
    if not METRICS:
        print("Метрики не найдены — metrics/ пуста?")
        return 1

    raw = PROJECT_ROOT / "data" / "raw" / (project or "")
    if project and raw.joinpath("issues.jsonl").exists():
        from src.core.tables import load_tables
        tables, src = load_tables(raw), f"реальная выгрузка {raw}"
    else:
        tables, src = synthetic_tables(), "синтетические данные"

    print(f"Тест-харнесс метрик · таблицы: {src}")
    print(f"  задач={len(tables['issues'])}, событий={len(tables['events'])}, "
          f"комментариев={len(tables['comments'])}")
    print(f"Метрик к проверке: {len(METRICS)}")

    for mid, metric in METRICS.items():
        err = check_metric(metric, tables)
        if err:
            fails += 1
            print(f"  FAIL {mid}: {err}")
        else:
            print(f"  PASS {mid}  ({metric.label})")

    if fails or load_errs:
        print(f"ИТОГ: НЕ ПРОЙДЕНО (failures: {fails}, ошибок загрузки: {len(load_errs)})")
        return 1
    print("ИТОГ: все метрики прошли проверку")
    return 0
