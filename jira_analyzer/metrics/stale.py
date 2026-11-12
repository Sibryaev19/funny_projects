"""Зависшие задачи: открыты и без движения дольше N дней (по updated)."""
from __future__ import annotations

import pandas as pd

from src.components.base import BaseMetric
from src.components.params import Param


class StaleIssues(BaseMetric):
    id = "stale_issues"
    label = "Зависшие задачи"
    description = "открытые задачи без обновлений дольше порога (по умолчанию 14 дней)"
    params = [Param(name="stale_days", label="Порог, дней", type="number",
                    default=14, required=True,
                    hint="задача считается зависшей, если она открыта и не "
                         "обновлялась дольше этого числа дней")]

    def build(self, tables, opts):
        stale_days = opts.get("stale_days")
        iss = tables["issues"]
        opened = iss[iss["status_category"] != "Done"].copy()
        opened["days_stale"] = ((pd.Timestamp.now(tz="UTC") - opened["updated"])
                                .dt.total_seconds() / 86400).round(1)
        stale = (opened[opened["days_stale"] >= stale_days]
                 .sort_values("days_stale", ascending=False)
                 [["key", "summary", "assignee", "status", "days_stale", "created"]]
                 .reset_index(drop=True))
        top = stale.head(20)
        return [
            self.bar(top["key"], {"дней без движения": top["days_stale"].tolist()},
                     title="Топ-20 зависших задач",
                     note=f"порог {stale_days:g} дн без обновлений",
                     horizontal=True),
            self.table_from_df(stale, collapsed=True),
        ]
