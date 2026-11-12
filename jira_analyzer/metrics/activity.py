"""Активность: поток задач по неделям и вклад исполнителей.

Обе метрики принимают необязательный диапазон дат (date_from/date_to) —
фильтр задач по дате создания (tz-aware UTC, дата «по» включается).
"""
from __future__ import annotations

import pandas as pd

from src.components.base import BaseMetric
from src.components.params import Param

_RANGE_PARAMS = [
    Param(name="date_from", label="Дата с", type="date",
          placeholder="пример: 2025-01-01",
          hint="учитывать только задачи, созданные с этой даты (включительно)"),
    Param(name="date_to", label="Дата по", type="date",
          placeholder="пример: 2025-12-31",
          hint="учитывать только задачи, созданные до этой даты (включительно)"),
]


def _range_note(opts) -> str:
    """Пометка о применённом диапазоне для note блока (пусто, если фильтра нет)."""
    frm, to = opts.get("date_from"), opts.get("date_to")
    if frm and to:
        return f"фильтр по created: {frm} … {to}"
    if frm or to:
        return f"фильтр по created: {frm or '…'}{to or '…'}"
    return ""


def _by_created(iss: pd.DataFrame, opts) -> pd.DataFrame:
    """Отфильтровать задачи по created согласно параметрам диапазона."""
    frm, to = opts.get("date_from"), opts.get("date_to")
    if frm:
        iss = iss[iss["created"] >= pd.Timestamp(frm, tz="UTC")]
    if to:
        iss = iss[iss["created"] < pd.Timestamp(to, tz="UTC") + pd.Timedelta(days=1)]
    return iss


def _week_of(s: pd.Series) -> pd.Series:
    naive = s.dt.tz_convert("UTC").dt.tz_localize(None)
    return naive.dt.to_period("W").dt.start_time


class ActivityWeekly(BaseMetric):
    id = "activity_weekly"
    label = "Активность по неделям"
    description = "сколько задач создавалось и закрывалось по неделям"
    params = _RANGE_PARAMS

    def build(self, tables, opts):
        iss = _by_created(tables["issues"], opts)
        created = (iss.dropna(subset=["created"])
                   .assign(week=lambda d: _week_of(d["created"]))
                   .groupby("week").size().rename("created"))
        resolved = (iss.dropna(subset=["resolved_at"])
                    .assign(week=lambda d: _week_of(d["resolved_at"]))
                    .groupby("week").size().rename("resolved"))
        df = pd.concat([created, resolved], axis=1).fillna(0).astype(int)
        if df.empty:
            return [self.list_block(["нет данных" + (f" ({_range_note(opts)})" if
                                                     _range_note(opts) else "")],
                                    title="Активность")]
        # непрерывная сетка недель без дыр
        full = pd.date_range(df.index.min(), df.index.max(), freq="7D")
        df = df.reindex(full, fill_value=0).rename_axis("week").reset_index()
        df["week"] = df["week"].dt.strftime("%Y-%m-%d")
        return [
            self.line(df["week"].tolist(),
                      {"создано": df["created"].tolist(),
                       "закрыто": df["resolved"].tolist()},
                      title="Создание и закрытие задач по неделям",
                      note=_range_note(opts)),
            self.table_from_df(df, title="По неделям", collapsed=True),
        ]


class ActivityUsers(BaseMetric):
    id = "activity_users"
    label = "Активность по исполнителям"
    description = "кому назначены задачи, сколько закрыто/открыто, последняя активность"
    params = _RANGE_PARAMS

    def build(self, tables, opts):
        iss = _by_created(tables["issues"], opts)
        assigned = iss[iss["assignee"].notna()].copy()
        g = assigned.groupby("assignee")
        df = pd.DataFrame({
            "всего_задач": g.size(),
            "закрыто": assigned[assigned["status_category"] == "Done"]
                        .groupby("assignee").size(),
            "открыто": assigned[assigned["status_category"] != "Done"]
                       .groupby("assignee").size(),
            "последняя_активность": g["updated"].max(),
            "медиана_дней_без_движения": ((pd.Timestamp.now(tz="UTC") - assigned["updated"])
                                          .dt.total_seconds() / 86400
                                          ).groupby(assigned["assignee"]).median().round(1),
        }).fillna(0)
        df["закрыто"] = df["закрыто"].astype(int)
        df["открыто"] = df["открыто"].astype(int)
        df = df.sort_values("всего_задач", ascending=False).rename_axis("исполнитель")
        df = df.reset_index()
        top = df.head(15)
        return [
            self.bar(top["исполнитель"],
                     {"закрыто": top["закрыто"].tolist(), "открыто": top["открыто"].tolist()},
                     title="Топ-15 исполнителей: закрытые/открытые задачи",
                     note=_range_note(opts),
                     horizontal=True, stacked=True),
            self.table_from_df(df, title="По исполнителям", collapsed=True),
        ]
