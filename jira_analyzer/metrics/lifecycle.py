"""Время жизни задач: циклы из changelog (категории статусов вместо имён —
работает на любой локализации процесса: In Progress/В работе и т.п.).

Три времени на задачу (в сутках):
  pickup — создана -> первый раз в работе
  cycle  — первый раз в работе -> первый раз в Done
  lead   — создана -> первый раз в Done
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.components.base import BaseMetric
from src.core.tables import status_category_map, status_events

_DAYS = lambda td: td.dt.total_seconds() / 86400  # noqa: E731


def _cycles(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    iss = tables["issues"]
    ev = tables["events"]
    if ev.empty:
        return pd.DataFrame(columns=["key", "summary", "assignee", "created",
                                     "in_progress_at", "done_at",
                                     "pickup_days", "cycle_days", "lead_days"])
    cat_map = status_category_map(iss)
    st = status_events(ev, cat_map)
    first_progress = st[st["cat"] == "In Progress"].groupby("key")["time"].min()
    first_done = st[st["cat"] == "Done"].groupby("key")["time"].min()

    cyc = pd.DataFrame({"in_progress_at": first_progress, "done_at": first_done}).reset_index()
    cyc = cyc.merge(iss[["key", "summary", "created", "assignee"]], on="key", how="left")
    cyc["pickup_days"] = _DAYS(cyc["in_progress_at"] - cyc["created"]).round(1)
    cyc["cycle_days"] = _DAYS(cyc["done_at"] - cyc["in_progress_at"]).round(1)
    cyc["lead_days"] = _DAYS(cyc["done_at"] - cyc["created"]).round(1)
    return cyc[["key", "summary", "assignee", "created", "in_progress_at", "done_at",
                "pickup_days", "cycle_days", "lead_days"]]


class LifecycleCycles(BaseMetric):
    id = "lifecycle_cycles"
    label = "Циклы задач (по задаче)"
    description = "время взятия в работу, время работы и общий lead по каждой задаче"

    def build(self, tables, opts):
        cyc = _cycles(tables)
        blocks = []
        s = cyc["cycle_days"].dropna() if "cycle_days" in cyc else pd.Series(dtype=float)
        s = s[(s >= 0) & (s <= s.quantile(0.95))] if not s.empty else s
        if not s.empty:
            counts, edges = np.histogram(s, bins=30)
            blocks.append(self.bar(
                [f"{edges[i]:.0f}–{edges[i + 1]:.0f}" for i in range(len(counts))],
                {"задач": counts.tolist()},
                title="Распределение времени работы над задачей (цикл, дни)",
                note=f"медиана {s.median():.1f} дн; выбросы обрезаны по p95 "
                     f"({s.max():.1f} дн)", horizontal=False))
        blocks.append(self.table_from_df(cyc, collapsed=True))
        return blocks


class LifecycleSummary(BaseMetric):
    id = "lifecycle_summary"
    label = "Сводка времени жизни"
    description = "медианы и перцентили: сколько занимает взятие в работу, работа, общий цикл"

    def build(self, tables, opts):
        cyc = _cycles(tables)
        rows = []
        for col, name in (("pickup_days", "создана -> в работе"),
                          ("cycle_days", "в работе -> закрыта"),
                          ("lead_days", "создана -> закрыта (lead)")):
            s = cyc[col].dropna() if col in cyc else pd.Series(dtype=float)
            s = s[s >= 0]
            if s.empty:
                rows.append([name, 0, 0.0, 0.0, 0.0, 0.0])
                continue
            rows.append([name, len(s), round(s.quantile(.5), 1), round(s.mean(), 1),
                         round(s.quantile(.75), 1), round(s.quantile(.9), 1)])
        return [self.table(["этап", "задач", "медиана, дн", "среднее, дн", "p75, дн", "p90, дн"],
                           rows, align=["left", "right", "right", "right", "right", "right"])]
