"""Выполненные задачи по исполнителям: кто сколько закрыл за период (по resolved_at)."""
from __future__ import annotations

import pandas as pd

from src.components.base import BaseMetric
from src.components.params import Param


class CompletedByAssignee(BaseMetric):
    id = "completed_by_assignee"
    label = "Выполнено по исполнителям"
    description = ("кто сколько задач закрыл за выбранный период "
                   "(фильтр по дате выполнения resolved_at)")
    params = [
        Param(name="date_from", label="Дата с", type="date",
              placeholder="пример: 2025-01-01",
              hint="учитывать задачи, выполненные с этой даты (включительно)"),
        Param(name="date_to", label="Дата по", type="date",
              placeholder="пример: 2025-12-31",
              hint="учитывать задачи, выполненные до этой даты (включительно)"),
    ]

    def build(self, tables, opts):
        iss = tables["issues"]
        done = iss[(iss["status_category"] == "Done")
                   & iss["resolved_at"].notna()].copy()
        n_unassigned = int(done["assignee"].isna().sum())
        done = done[done["assignee"].notna()]
        frm, to = opts.get("date_from"), opts.get("date_to")
        if frm:
            done = done[done["resolved_at"] >= pd.Timestamp(frm, tz="UTC")]
        if to:
            done = done[done["resolved_at"] < pd.Timestamp(to, tz="UTC") + pd.Timedelta(days=1)]

        period = (f"{frm or '…'} … {to or '…'}" if frm or to else "за всё время")
        note = f"фильтр по дате выполнения: {period}"
        if n_unassigned:
            note += f"; выполнено без исполнителя: {n_unassigned} (не показаны)"

        counts = done["assignee"].value_counts()
        if counts.empty:
            return [self.list_block([f"нет выполненных задач ({period})"],
                                    title=self.label, note=note)]
        return [
            self.bar(counts.index.tolist(), {"выполнено": counts.tolist()},
                     title="Выполненные задачи по исполнителям", note=note,
                     horizontal=True),
            self.table(["Исполнитель", "Выполнено"], list(counts.items()),
                       title="По исполнителям", collapsed=True),
        ]
