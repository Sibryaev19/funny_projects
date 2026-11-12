"""Обзор проекта: общие числа по выгрузке + разбивка по типам."""
from __future__ import annotations

import pandas as pd

from src.components.base import BaseMetric


class Overview(BaseMetric):
    id = "overview"
    label = "Обзор проекта"
    description = "общие числа: объём, открытые/закрытые, участники, разбивка по типам"

    def build(self, tables, opts):
        iss, ev = tables["issues"], tables["events"]
        total = len(iss)
        done = int((iss["status_category"] == "Done").sum())
        rows = [
            ["Задач в выгрузке", total],
            ["Открытых", total - done],
            ["Закрытых (категория Done)", done],
            ["Первая создана", iss["created"].min()],
            ["Последняя создана", iss["created"].max()],
            ["Последнее обновление", iss["updated"].max()],
            ["Событий changelog", len(ev)],
            ["Участников (меняли задачи)",
             int(ev["author"].nunique()) if not ev.empty else 0],
            ["Исполнителей", int(iss["assignee"].nunique())],
        ]
        return [self.table(["Показатель", "Значение"], rows),
                self.pie_from_counts(iss["type"].value_counts(),
                                     title="Разбивка по типам", top=12)]
