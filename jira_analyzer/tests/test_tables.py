"""Чтение таблиц: «последняя запись по ключу выигрывает» (догруженные полные
истории и хвосты resume не порождают дублей), устойчивость к оборванной строке,
категоризация статусов."""
from __future__ import annotations

import json

from src.core.tables import cat_of, load_tables, status_category_map, status_events


def write_jsonl(path, records, *, torn_tail=False):
    with open(path, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        if torn_tail:
            f.write('{"key": "CURA')   # оборванная строка — падение посреди записи


def make_issue(key, status="Done"):
    return {"key": key, "fields": {
        "summary": key, "issuetype": {"name": "Task"},
        "status": {"name": status,
                   "statusCategory": {"name": "Done" if status == "Done"
                                      else "In Progress"}},
        "created": "2024-01-01T10:00:00.000+0000", "subtasks": []}}


def test_changelog_doload_not_double_counted(tmp_path):
    """Урезанная запись (2 события) + дописанная полная (3 события) одной задачи:
    в таблице событий должно быть 3, а не 5."""
    key = "CURA-1"
    trunc = {"key": key, "histories": [
        {"created": "2024-01-01T11:00:00.000+0000", "author": {"displayName": "u"},
         "items": [{"field": "status", "fromString": "Open", "toString": "WIP"}]},
        {"created": "2024-01-01T12:00:00.000+0000", "author": {"displayName": "u"},
         "items": [{"field": "status", "fromString": "WIP", "toString": "Done"}]},
    ], "truncated": True}
    full = {"key": key, "histories": trunc["histories"] + [
        {"created": "2024-01-01T13:00:00.000+0000", "author": {"displayName": "u"},
         "items": [{"field": "status", "fromString": "Done", "toString": "Reopened"}]},
    ], "truncated": False}
    write_jsonl(tmp_path / "issues.jsonl", [make_issue(key)])
    write_jsonl(tmp_path / "changelog.jsonl", [trunc, full])
    tables = load_tables(tmp_path)
    assert len(tables["events"]) == 3
    assert tables["issues"].shape[0] == 1


def test_duplicate_issues_last_wins(tmp_path):
    dup = make_issue("CURA-1", status="Done")
    fixed = make_issue("CURA-1", status="In Progress")   # «перезапись» резюме
    write_jsonl(tmp_path / "issues.jsonl", [dup, fixed])
    tables = load_tables(tmp_path)
    assert tables["issues"].shape[0] == 1
    assert tables["issues"].iloc[0]["status"] == "In Progress"


def test_torn_tail_tolerated(tmp_path):
    write_jsonl(tmp_path / "issues.jsonl", [make_issue("CURA-1")], torn_tail=True)
    write_jsonl(tmp_path / "changelog.jsonl", [])
    tables = load_tables(tmp_path)
    assert tables["issues"].shape[0] == 1


def test_missing_and_empty_files(tmp_path):
    tables = load_tables(tmp_path)
    assert tables["issues"].empty
    assert tables["events"].empty
    assert tables["comments"].empty


def test_status_categories_and_events(tmp_path):
    issues_rows = [
        make_issue("CURA-1", status="Done"),
        make_issue("CURA-2", status="В работе"),
        make_issue("CURA-3", status="Открыта"),
    ]
    write_jsonl(tmp_path / "issues.jsonl", issues_rows)
    events = []
    for n, (key, seq) in enumerate([("CURA-1", ["Open", "WIP", "Готово"]),
                                    ("CURA-2", ["Open", "Ревью"])]):
        for k, st in enumerate(seq[1:], 1):
            events.append({"key": key, "histories": [
                {"created": f"2024-01-0{n + 1}T1{k}:00:00.000+0000",
                 "author": {"displayName": "u"},
                 "items": [{"field": "status", "fromString": seq[k - 1],
                            "toString": st}]}], "truncated": False})
    write_jsonl(tmp_path / "changelog.jsonl", events)
    tables = load_tables(tmp_path)
    cat_map = status_category_map(tables["issues"])
    assert cat_map["Done"] == "Done"
    assert cat_map["В работе"] == "In Progress"
    st = status_events(tables["events"], cat_map)
    # «Готово» не встречается как текущий статус → эвристика по имени
    assert st[st["to"] == "Готово"]["cat"].iloc[0] == "Done"
    assert st[st["to"] == "Ревью"]["cat"].iloc[0] == "In Progress"


def test_cat_of_heuristics():
    cat_map = {"Open": "To Do"}
    assert cat_of("Open", cat_map) == "To Do"
    assert cat_of("Готово", {}) == "Done"
    assert cat_of("В работе", {}) == "In Progress"
    assert cat_of("Закрыт", {}) == "Done"
    assert cat_of(None, cat_map) == "To Do"
    assert cat_of("Совсем неизвестный", cat_map) == "To Do"
