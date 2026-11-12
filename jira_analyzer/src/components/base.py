"""BaseMetric — базовый класс метрики-компонента.

Наследник описывает поля id, label, description и один метод build(),
возвращающий список блоков. Опционально — params: список Param(...)
(см. src/components/params.py), параметры, которые пользователь задаёт
в web-форме или в CLI (`--metrics 'stale_issues(stale_days=45)'`); значения
приходят в build() уже приведёнными к типам — читаются через opts.get(name).
Рисовать/сериализовать ничего не нужно: блоки строятся хелперами
self.table / self.bar / self.line / self.pie / self.list_block (значения сразу
приводятся к JSON-safe), дальше их рендерят web и отчёт сами.

    from src.components.base import BaseMetric
    from src.components.params import Param

    class ByType(BaseMetric):
        id = "by_type"
        label = "Задачи по типам"
        description = "распределение задач по типам"

        def build(self, tables, opts):
            counts = tables["issues"]["type"].value_counts()
            return [self.pie_from_counts(counts, title="По типам"),
                    self.bar_from_counts(counts)]

Файл кладётся в metrics/ — регистрация происходит автоматически при старте
(см. src/components/registry.py и metrics/README.md).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from .blocks import (ListBlock, PieBlock, PieItem, Series, TableBlock,
                     BarBlock, LineBlock, json_safe)
from .params import Param


@dataclass
class MetricOpts:
    """Параметры экземпляра метрики — результат resolve_params() по декларации
    (дефолты подставлены, типы приведены). Ключи = имена Param метрики."""
    params: dict = field(default_factory=dict)

    def get(self, name: str, default: Any = None) -> Any:
        """Значение параметра по имени (None, если метрика его не объявляла)."""
        return self.params.get(name, default)


class BaseMetric(ABC):
    id: str = ""            # уникальный, латиница/цифры/подчёркивания
    label: str = ""         # человекочитаемое имя (заголовок блока)
    description: str = ""   # пояснение, показывается под заголовком
    params: list = []       # декларация Param(...), запрашиваемых у пользователя

    @abstractmethod
    def build(self, tables: dict[str, pd.DataFrame], opts: MetricOpts) -> list:
        """Вернуть список блоков (1-5). tables: {"issues", "events", "comments"}
        — DataFrame из выгрузки (comments может быть пустым, если выгрузка шла
        без комментариев). Параметры пользователя — opts.get("имя")."""

    # ---------------- конструкторы блоков ----------------

    def table(self, columns: Sequence[str], rows: Sequence[Sequence], *,
              title: str = "", note: str = "", align: list[str] | None = None,
              collapsed: bool = False) -> TableBlock:
        """Таблица из готовых колонок и строк."""
        return TableBlock(title=title, note=note, align=align, collapsed=collapsed,
                          columns=[str(c) for c in columns],
                          rows=[[json_safe(c) for c in row] for row in rows])

    def table_from_df(self, df: pd.DataFrame, *, title: str = "", note: str = "",
                      max_rows: int | None = None, align: list[str] | None = None,
                      collapsed: bool = False) -> TableBlock:
        """Таблица из DataFrame (даты -> строки, NaN -> null автоматически)."""
        rows = [[json_safe(v) for v in row]
                for row in df.itertuples(index=False, name=None)]
        if max_rows:
            rows = rows[:max_rows]
        return TableBlock(title=title, note=note, align=align, collapsed=collapsed,
                          columns=[str(c) for c in df.columns], rows=rows)

    def bar(self, labels: Sequence, series: Mapping[str, Sequence] | list[Series], *,
            title: str = "", horizontal: bool = False, stacked: bool = False,
            note: str = "", collapsed: bool = False) -> BarBlock:
        """Столбчатая диаграмма. series — {имя_серии: значения} или список Series."""
        return BarBlock(title=title, note=note, collapsed=collapsed,
                        labels=[json_safe(v) for v in labels],
                        series=_series(series), horizontal=horizontal, stacked=stacked)

    def bar_from_counts(self, counts: pd.Series, *, title: str = "",
                        horizontal: bool = True, stacked: bool = False,
                        note: str = "", collapsed: bool = False) -> BarBlock:
        """Столбчатая из объектов value_counts() и подобных: индекс -> подписи."""
        return self.bar(list(counts.index), [counts.tolist()], title=title,
                        horizontal=horizontal, stacked=stacked, note=note,
                        collapsed=collapsed)

    def line(self, x: Sequence, series: Mapping[str, Sequence] | list[Series], *,
             title: str = "", note: str = "", collapsed: bool = False) -> LineBlock:
        """Линейная диаграмма: x — подписи оси, series — {имя: значения}."""
        return LineBlock(title=title, note=note, collapsed=collapsed,
                         x=[json_safe(v) for v in x], series=_series(series))

    def pie(self, items: Mapping[str, float] | Sequence[tuple[str, float]], *,
            title: str = "", note: str = "", collapsed: bool = False) -> PieBlock:
        """Круговая: {имя: значение} или список пар."""
        pairs = items.items() if isinstance(items, Mapping) else items
        return PieBlock(title=title, note=note, collapsed=collapsed,
                        items=[PieItem(name=str(n), value=json_safe(v))
                               for n, v in pairs])

    def pie_from_counts(self, counts: pd.Series, *, title: str = "", top: int | None = None,
                        note: str = "", collapsed: bool = False) -> PieBlock:
        """Круговая из value_counts(); top=N — остальное суммируется в «остальные»."""
        if top and len(counts) > top:
            head, tail = counts.head(top), counts.iloc[top:].sum()
            pairs = list(head.items()) + ([("остальные", tail)] if tail else [])
        else:
            pairs = list(counts.items())
        return self.pie(pairs, title=title, note=note, collapsed=collapsed)

    def list_block(self, items: Sequence, *, title: str = "", note: str = "",
                   collapsed: bool = False) -> ListBlock:
        """Простой список строк."""
        return ListBlock(title=title, note=note, collapsed=collapsed,
                         items=[str(v) for v in items])


def _series(series: Mapping[str, Sequence] | list[Series]) -> list[Series]:
    if isinstance(series, Mapping):
        return [Series(name=name, values=list(vals)) for name, vals in series.items()]
    return list(series)
