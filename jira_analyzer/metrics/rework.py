"""Переделки: переоткрытия (переход Done -> не-Done) и повторные взятия в работу."""
from __future__ import annotations

import pandas as pd

from src.components.base import BaseMetric
from src.core.tables import status_category_map, status_events


def _reopens(tables: dict[str, pd.DataFrame]) -> pd.Series:
    """key -> число переоткрытий."""
    iss, ev = tables["issues"], tables["events"]
    if ev.empty:
        return pd.Series(dtype=int, name="reopens")
    st = status_events(ev, status_category_map(iss))
    reopened = st[(st["cat"] != "Done") & (st["prev_cat"] == "Done")]
    return reopened.groupby("key").size().rename("reopens")


class ReworkIssues(BaseMetric):
    id = "rework_issues"
    label = "Переоткрытия (по задаче)"
    description = "сколько раз каждая задача переоткрывалась после Done"

    def build(self, tables, opts):
        iss = tables["issues"]
        reopens = _reopens(tables)
        df = iss[["key", "summary", "assignee", "status"]].merge(
            reopens.reset_index(), on="key", how="left")
        df["reopens"] = df["reopens"].fillna(0).astype(int)
        df = (df[df["reopens"] > 0]
              .sort_values("reopens", ascending=False).reset_index(drop=True))
        counts = df["reopens"].value_counts().sort_index()
        blocks = []
        if not counts.empty:
            blocks.append(self.bar([str(c) for c in counts.index],
                                   {"задач": counts.tolist()},
                                   title="Задачи по числу переоткрытий",
                                   horizontal=False))
        blocks.append(self.table_from_df(df, collapsed=True))
        return blocks


class ReworkSummary(BaseMetric):
    id = "rework_summary"
    label = "Сводка переоткрытий"
    description = "доля переоткрытых задач и распределение числа переоткрытий"

    def build(self, tables, opts):
        iss = tables["issues"]
        reopens = _reopens(tables)
        total = len(iss)
        reopened_n = len(reopens)
        events = int(reopens.sum())
        buckets = pd.cut(reopens, bins=[1, 2, 3, 4, 10**6],
                         labels=["1", "2", "3", "4+"], right=False).value_counts().sort_index()
        rows = [
            ["Задач всего", total],
            ["Переоткрывались", f"{reopened_n} ({reopened_n / total * 100:.1f}%)"
             if total else "0"],
            ["Событий переоткрытия", events],
            ["Распределение: 1 раз", int(buckets.get("1", 0))],
            ["Распределение: 2 раза", int(buckets.get("2", 0))],
            ["Распределение: 3 раза", int(buckets.get("3", 0))],
            ["Распределение: 4+ раз", int(buckets.get("4+", 0))],
        ]
        return [self.table(["Показатель", "Значение"], rows)]
