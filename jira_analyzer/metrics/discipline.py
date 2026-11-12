"""Дисциплина процесса: доля задач с нарушениями гигиены полей и порядка статусов."""
from __future__ import annotations

import pandas as pd

from src.components.base import BaseMetric
from src.core.tables import status_category_map, status_events


class DisciplineChecks(BaseMetric):
    id = "discipline_checks"
    label = "Дисциплина"
    description = "доли задач: без исполнителя/приоритета/компонента, с дедлайном, " \
                  "закрыто автором, закрыто без работы"

    def build(self, tables, opts):
        iss = tables["issues"]
        total = len(iss)
        if not total:
            return [self.list_block(["нет данных"])]

        resolved = iss[iss["status_category"] == "Done"]

        # закрыто без единого захода в работу (по changelog)
        ev = tables["events"]
        ever_worked = set()
        if not ev.empty:
            st = status_events(ev, status_category_map(iss))
            ever_worked = set(st.loc[st["cat"] == "In Progress", "key"])
        bypassed = resolved[~resolved["key"].isin(ever_worked)]

        both = resolved[resolved["reporter"].notna() & resolved["assignee"].notna()]
        self_closed = both[both["reporter"] == both["assignee"]]

        checks = [
            ["без исполнителя", int(iss["assignee"].isna().sum())],
            ["без приоритета", int(iss["priority"].isna().sum())],
            ["без компонента", int(iss["components"].eq("").sum())],
            ["с дедлайном (факт!)", int(iss["duedate"].notna().sum())],
            ["закрыто самим автором", len(self_closed)],
            ["закрыто минуя работу", len(bypassed)],
        ]
        df = pd.DataFrame(checks, columns=["проверка", "задач"])
        df["доля %"] = (df["задач"] / total * 100).round(1)
        return [
            self.bar(df["проверка"], {"доля %": df["доля %"].tolist()},
                     title="Дисциплина: доли от всех задач, %", horizontal=True),
            self.table(["проверка", "доля %", "задач"],
                       df[["проверка", "доля %", "задач"]].itertuples(index=False, name=None),
                       collapsed=True),
        ]
