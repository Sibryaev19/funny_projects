"""Выгрузка комментариев (опционально: по умолчанию выключены — на больших
проектах тяжело, а метрики «общения» — третий эшелон анализа).

Батч-режим: комментарии приходят пакетно в search (fields=comment, пачки ключей).
Ограничение API: на задачу возвращается до 100 последних комментариев; урезанные
догружаются отдельным эндпоинтом, если он отдаёт полную версию.

Формат выдачи — генератор записей, по строке на задачу (JSONL у потребителя).
"""
from __future__ import annotations

import logging
from collections.abc import Iterator

from .client import JiraClient
from .issues import SEARCH_PATH

log = logging.getLogger(__name__)

EMBED_CAP = 100  # сколько комментариев API возвращает на задачу в батч-режиме


def _chunks(keys: list[str], size: int):
    for i in range(0, len(keys), size):
        yield keys[i:i + size]


def iter_comments(client: JiraClient, issue_keys: list[str],
                  *, batch_size: int = 50) -> Iterator[dict]:
    """Генератор: {"key", "comments", "truncated"} на каждую задачу."""
    total = len(issue_keys)
    for done, chunk in enumerate(_chunks(issue_keys, batch_size), 1):
        jql = "key in (" + ", ".join(chunk) + ")"
        data = client.get(SEARCH_PATH, params={
            "jql": jql,
            "fields": "comment",
            "maxResults": batch_size,
        })
        for issue in data.get("issues", []):
            c = issue["fields"].get("comment") or {}
            comments = c.get("comments", [])
            yield {
                "key": issue["key"],
                "comments": comments,
                "truncated": c.get("total", 0) > len(comments),
            }
        log.info("Комментарии (батч): обработано %d / %d задач",
                 min(done * batch_size, total), total)
