"""Выгрузка истории изменений (changelog) по каждой задаче.

Главный источник аналитики: кто, когда и какое поле перевёл из чего во что.

Стратегия (2 режима):
1. expand-режим (основной) — changelog приходит пакетно вместе с задачами через
   search (пачки по expand_batch ключей). 136k задач ≈ 2700 запросов. Ограничение
   API: на задачу возвращается до 100 последних событий.
2. Для урезанных задач пробуем догрузить полную историю отдельным эндпоинтом
   /issue/{key}/changelog (есть на свежих Server/DC, но не везде: на Apache
   отключён — тогда оставляем как есть с предупреждением).

Формат выдачи — генератор записей; потребитель (run.py) пишет по строке на задачу.
Если задача догружалась эндпоинтом, её ПОЛНАЯ запись дописывается в файл после
урезанной — потребитель при чтении в словарь берёт последнюю строку по ключу.
"""
from __future__ import annotations

import logging
from collections.abc import Iterator

from .client import JiraApiError, JiraClient
from .issues import SEARCH_PATH

log = logging.getLogger(__name__)

EXPAND_CAP = 100  # сколько историй API возвращает на задачу в expand-режиме


def _chunks(keys: list[str], size: int):
    for i in range(0, len(keys), size):
        yield keys[i:i + size]


def iter_changelog(client: JiraClient, issue_keys: list[str],
                   *, expand_batch: int = 50) -> Iterator[dict]:
    """Генератор: {"key", "histories", "truncated"} на каждую задачу (expand-режим)."""
    total = len(issue_keys)
    for done, chunk in enumerate(_chunks(issue_keys, expand_batch), 1):
        jql = "key in (" + ", ".join(chunk) + ")"
        data = client.get(SEARCH_PATH, params={
            "jql": jql,
            "expand": "changelog",
            "fields": "summary",
            "maxResults": expand_batch,
        })
        for issue in data.get("issues", []):
            cl = issue.get("changelog") or {}
            histories = cl.get("histories", [])
            yield {
                "key": issue["key"],
                "histories": histories,
                "truncated": cl.get("total", 0) > len(histories),
            }
        log.info("Changelog (expand): обработано %d / %d задач",
                 min(done * expand_batch, total), total)


def iter_changelog_endpoint(client: JiraClient, issue_keys: list[str],
                            *, page_size: int = 1000) -> Iterator[dict]:
    """Полная история по задачам через отдельный эндпоинт (догрузка урезанных)."""
    for idx, key in enumerate(issue_keys, 1):
        histories: list[dict] = []
        start = 0
        while True:
            data = client.get(
                f"rest/api/2/issue/{key}/changelog",
                params={"startAt": start, "maxResults": page_size},
            )
            batch = data.get("histories", [])
            histories.extend(batch)
            if not batch or len(histories) >= data.get("total", 0):
                break
            start += page_size
        yield {"key": key, "histories": histories, "truncated": False}
        if idx % 100 == 0 or idx == len(issue_keys):
            log.info("Changelog (эндпоинт): %d / %d", idx, len(issue_keys))


def endpoint_available(client: JiraClient, key: str) -> bool:
    try:
        client.get(f"rest/api/2/issue/{key}/changelog", params={"maxResults": 1})
        return True
    except JiraApiError:
        return False
