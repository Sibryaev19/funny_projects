"""Нормализация сырых JSONL (data/raw/<project>) в pandas-таблицы.

Выход load_tables(): {"issues", "events", "comments"} — далее все метрики-компоненты
работают только с этими таблицами (схемы колонок — в metrics/README.md).

Все три файла читаются «последняя запись по ключу выигрывает»: fetch дописывает
ПОЛНУЮ историю задачи после урезанной (>100 событий), а возобновлённая выгрузка
может оставить дубли на стыках сессий — без дедупа события считались бы дважды.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pandas as pd

DONE_HINTS = ("done", "closed", "resolved", "выполн", "закрыт", "решен", "готов")
WORK_HINTS = ("progress", "review", "test", "develop", "reopened",
              "работ", "ревью", "тест", "перес")


def _read_jsonl(path: Path):
    if not path.exists():
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def last_line_offsets(path: Path) -> dict:
    """{ключ: смещение последней строки по ключу} — дедуп JSONL при чтении.

    Смещения байтовые (файл читается в бинарном режиме), поэтому их можно
    хранить в чекпоинте и использовать в новой сессии (см. fetch/state.py).
    """
    best: dict = {}
    if not path.exists():
        return best
    with open(path, "rb") as f:
        while True:
            offset = f.tell()
            line = f.readline()
            if not line:
                break
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue  # оборванный хвост не должен ронять загрузку
            key = rec.get("key")
            if key is not None:
                best[key] = offset
    return best


def iter_records_at(path: Path, offsets: dict) -> Iterator[dict]:
    """Записи с сохранённых смещений last_line_offsets (порядок файла)."""
    with open(path, "rb") as f:
        for offset in sorted(offsets.values()):
            f.seek(offset)
            yield json.loads(f.readline())


def _iter_dedup(path: Path) -> Iterator[dict]:
    """Записи JSONL без дублей по ключу (последняя строка по ключу)."""
    if not path.exists():
        return
    offsets = last_line_offsets(path)
    yield from iter_records_at(path, offsets)


def _dt(v) -> pd.Timestamp:
    return pd.to_datetime(v, errors="coerce", utc=True)


def load_tables(raw_dir: Path) -> dict[str, pd.DataFrame]:
    rows = []
    for issue in _iter_dedup(raw_dir / "issues.jsonl"):
        f = issue.get("fields") or {}
        rows.append({
            "key": issue.get("key"),
            "summary": f.get("summary"),
            "type": (f.get("issuetype") or {}).get("name"),
            "status": (f.get("status") or {}).get("name"),
            "status_category": ((f.get("status") or {}).get("statusCategory") or {}).get("name"),
            "priority": (f.get("priority") or {}).get("name"),
            "assignee": (f.get("assignee") or {}).get("displayName"),
            "reporter": (f.get("reporter") or {}).get("displayName"),
            "created": _dt(f.get("created")),
            "updated": _dt(f.get("updated")),
            "resolved_at": _dt(f.get("resolutiondate")),
            "resolution": (f.get("resolution") or {}).get("name"),
            "duedate": _dt(f.get("duedate")),
            "components": ", ".join((c.get("name") or "") for c in (f.get("components") or [])),
            "parent": (f.get("parent") or {}).get("key"),
            "n_subtasks": len(f.get("subtasks") or []),
            "timespent_h": round((f.get("timespent") or 0) / 3600, 2),
        })
    issues = pd.DataFrame(rows)

    events = []
    for rec in _iter_dedup(raw_dir / "changelog.jsonl"):
        for h in rec.get("histories", []):
            when = _dt(h.get("created"))
            who = (h.get("author") or {}).get("displayName")
            for it in h.get("items", []):
                events.append({
                    "key": rec.get("key"),
                    "time": when,
                    "author": who,
                    "field": it.get("field"),
                    "from": it.get("fromString"),
                    "to": it.get("toString"),
                })
    events_df = pd.DataFrame(events, columns=["key", "time", "author", "field", "from", "to"])

    comments = []
    for rec in _iter_dedup(raw_dir / "comments.jsonl"):
        for c in rec.get("comments", []):
            comments.append({
                "key": rec.get("key"),
                "author": (c.get("author") or {}).get("displayName"),
                "created": _dt(c.get("created")),
            })
    comments_df = pd.DataFrame(comments, columns=["key", "author", "created"])

    return {"issues": issues, "events": events_df, "comments": comments_df}


def status_category_map(issues: pd.DataFrame) -> dict[str, str]:
    """Статус -> категория по текущим данным выгрузки. Для статусов, которые не
    встретились как текущие (напр., переименованные в истории), есть эвристика
    cat_of() по имени."""
    sub = (issues.dropna(subset=["status"])[["status", "status_category"]]
           .drop_duplicates("status"))
    return dict(zip(sub["status"], sub["status_category"]))


def cat_of(status_name, cat_map: dict[str, str]) -> str:
    """Категория статуса по имени: сначала точные данные, потом эвристика."""
    if not status_name:
        return "To Do"
    if status_name in cat_map:
        return cat_map[status_name]
    low = status_name.lower()
    if any(h in low for h in DONE_HINTS):
        return "Done"
    if any(h in low for h in WORK_HINTS):
        return "In Progress"
    return "To Do"


def status_events(events: pd.DataFrame, cat_map: dict[str, str]) -> pd.DataFrame:
    """События смены статуса с категориями: cat (куда перешло) и prev_cat
    (откуда перешло, вычислен по полю from)."""
    cols = ["key", "time", "from", "to", "cat", "prev_cat"]
    if events.empty:
        return pd.DataFrame(columns=cols)
    st = (events[events["field"] == "status"][["key", "time", "from", "to"]]
          .sort_values("time", kind="stable").copy())
    st["cat"] = st["to"].map(lambda x: cat_of(x, cat_map))
    st["prev_cat"] = st["from"].map(lambda x: cat_of(x, cat_map))
    return st[cols]
