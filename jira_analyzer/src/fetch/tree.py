"""Построение структуры дерева epic -> задача -> подзадача из выгруженных задач.

Связь подзадачи с родителем — стандартное поле parent.
Связь задачи с epic — кастомное поле, ID которого задаётся в config/fields.json
(custom_fields.epic_link, например "customfield_10001"); у инстансов без epic-полей
там null и ветка epic просто не участвует.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def build_tree(issues: list[dict], custom_fields: dict) -> dict:
    epic_field = custom_fields.get("epic_link")
    nodes: dict[str, dict] = {}
    children: dict[str, list[str]] = {}

    for issue in issues:
        key = issue["key"]
        fields = issue.get("fields") or {}

        parent_key = None
        parent = fields.get("parent")
        if isinstance(parent, dict):
            parent_key = parent.get("key")

        epic_key = None
        if epic_field:
            epic = fields.get(epic_field)
            # значение epic-поля бывает строкой-ключом или объектом-задачей (зависит от версии)
            if isinstance(epic, dict):
                epic_key = epic.get("key")
            elif isinstance(epic, str):
                epic_key = epic

        nodes[key] = {
            "key": key,
            "type": (fields.get("issuetype") or {}).get("name"),
            "status": (fields.get("status") or {}).get("name"),
            "parent": parent_key,
            "epic": epic_key,
        }
        for link in filter(None, (parent_key, epic_key)):
            children.setdefault(link, []).append(key)

    known = set(nodes)
    broken = [n["key"] for n in nodes.values()
              if (n["parent"] and n["parent"] not in known)
              or (n["epic"] and n["epic"] not in known)]
    if broken:
        log.warning("Родители вне выгрузки у %d задач (фильтр по датам или другой проект): %s",
                    len(broken), broken[:5])

    roots = sorted(k for k, n in nodes.items() if not n["parent"] and not n["epic"])
    return {"count": len(nodes), "roots": roots, "children": children, "nodes": nodes}
