"""Спецификации блоков: язык, на котором метрика-компонент описывает результат.

Метрика НЕ рисует — она декларирует блоки (тип + данные), а рендерят их
web-интерфейс и HTML-отчёт одним и тем же кодом (static/blocks.js + echarts).

Типы блоков (дискриминатор — поле type):
  table — {columns, rows}                        таблица
  bar   — {labels, series, horizontal, stacked}  столбчатая/линейчатая
  line  — {x, series}                            линейная
  pie   — {items: [{name, value}]}               круговая
  list  — {items}                                список

Общие поля: title, note (пояснение мелким шрифтом), collapsed (стартовать
свёрнутым — для очень длинных таблиц).
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Annotated, Any, Literal, Union

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

_FMT = "%Y-%m-%d %H:%M"


def json_safe(v: Any) -> Any:
    """Значение -> JSON-safe тип: даты -> строки, numpy -> python, NaN/NaT -> None."""
    if v is None or isinstance(v, (str, bool, int)):
        return v
    if v is pd.NaT:
        return None
    if isinstance(v, (pd.Timestamp, dt.datetime)):
        return v.strftime(_FMT)
    if isinstance(v, dt.date):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, dt.timedelta):
        return round(v.total_seconds() / 3600, 2)
    if isinstance(v, np.bool_):
        return bool(v)
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, (np.floating, float)):
        f = float(v)
        return f if math.isfinite(f) else None
    return v


class Series(BaseModel):
    """Набор значений одного графика (одна линия/столбцы одного цвета)."""
    name: str
    values: list[Any]


class PieItem(BaseModel):
    name: str
    value: float


class _Block(BaseModel):
    title: str = ""
    note: str = ""
    collapsed: bool = False


class TableBlock(_Block):
    type: Literal["table"] = "table"
    columns: list[str]
    rows: list[list[Any]]
    align: list[Literal["left", "center", "right"]] | None = None  # по колонкам


class BarBlock(_Block):
    type: Literal["bar"] = "bar"
    labels: list[str]
    series: list[Series]
    horizontal: bool = False
    stacked: bool = False


class LineBlock(_Block):
    type: Literal["line"] = "line"
    x: list[str]
    series: list[Series]


class PieBlock(_Block):
    type: Literal["pie"] = "pie"
    items: list[PieItem]


class ListBlock(_Block):
    type: Literal["list"] = "list"
    items: list[str]


Block = Annotated[Union[TableBlock, BarBlock, LineBlock, PieBlock, ListBlock],
                  Field(discriminator="type")]


class MetricResult(BaseModel):
    """Результат одной метрики: её паспорт + список блоков для рендеринга.
    params_label — краткая сводка значений параметров экземпляра
    («порог, дней: 45»), различает повторные добавления одной метрики."""
    metric_id: str
    label: str
    description: str = ""
    params_label: str = ""
    blocks: list[Block]


def sanitize_result(res: MetricResult) -> MetricResult:
    """Финальная страховка: все значения блоков -> JSON-safe (вне зависимости от
    того, как метрика их получила). Вызывается конвейером после build()."""
    data = res.model_dump()
    for b in data["blocks"]:
        if b["type"] == "table":
            b["rows"] = [[json_safe(c) for c in row] for row in b["rows"]]
        if b["type"] == "bar":
            b["labels"] = [json_safe(v) for v in b["labels"]]
        if b["type"] == "line":
            b["x"] = [json_safe(v) for v in b["x"]]
        for s in b.get("series") or []:
            s["values"] = [json_safe(v) for v in s["values"]]
    return MetricResult.model_validate(data)
