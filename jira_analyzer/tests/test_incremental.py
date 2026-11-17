"""Инкрементные обновления (v8) и текст resume-ошибки.

Инкремент: качаются только задачи с updated >= fetched_at прошлого коммита,
дельта мержится с существующими JSONL по ключу (последняя запись выигрывает).
"""
from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone

import pytest

from src.fetch.issues import build_jql, iter_issues
from src.fetch.run import fetch_project

from conftest import FakeJira, FetchFailure, make_issue, make_issues, use_fake_jira

PROJECT = "INC"


def read_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def base_kwargs(fetch_env, **extra):
    kw = dict(jira_type="apache", env_path=fetch_env["env_path"])
    kw.update(extra)
    return kw


# ---------------- JQL ----------------

def test_build_jql_date_field():
    jql = build_jql("X", date_from="2024-01-01", date_field="updated")
    assert 'updated >= "2024-01-01"' in jql
    assert "created >=" not in jql
    with pytest.raises(ValueError, match="date_field"):
        build_jql("X", date_field="resolved")


def test_iter_issues_bad_date_field():
    with pytest.raises(ValueError, match="date_field"):
        list(iter_issues(None, "X", ["key"], date_field="due"))


# ---------------- инкремент ----------------

def test_incremental_merges_delta(fetch_env, monkeypatch):
    issues = make_issues(10, PROJECT)
    use_fake_jira(monkeypatch, FakeJira(issues))
    out = fetch_project(project_key=PROJECT, phases="both", **base_kwargs(fetch_env))
    assert len(read_jsonl(out / "issues.jsonl")) == 10

    # дельта: PH-3 изменилась, PH-11 новая (updated после прошлого коммита)
    soon = (datetime.now(timezone.utc) + timedelta(minutes=5)
            ).strftime("%Y-%m-%dT%H:%M:%S.000+0000")
    changed = copy.deepcopy(issues[2])
    changed["fields"]["updated"] = soon
    changed["fields"]["summary"] = "Обновлённая задача 3"
    fresh = make_issue(11, PROJECT, histories=1)
    fresh["fields"]["updated"] = soon
    use_fake_jira(monkeypatch, FakeJira([changed, fresh]))

    out2 = fetch_project(project_key=PROJECT, phases="both", incremental=True,
                         **base_kwargs(fetch_env))
    recs = {r["key"]: r for r in read_jsonl(out2 / "issues.jsonl")}
    assert len(recs) == 11                                  # 10 старых + 1 новая
    assert recs[changed["key"]]["fields"]["summary"] == "Обновлённая задача 3"
    assert recs[fresh["key"]]["fields"]["summary"] == "Задача 11"
    assert recs[issues[0]["key"]] == issues[0]              # нетронутые не тронуты

    # changelog смержился так же: PH-3 и PH-11 свежие записи, остальные старые
    cl_keys = {r["key"] for r in read_jsonl(out2 / "changelog.jsonl")}
    assert cl_keys == {i["key"] for i in issues} | {fresh["key"]}

    summary = json.loads((out2 / "fetch_summary.json").read_text(encoding="utf-8"))
    assert summary["incremental"] is True
    assert summary["incremental_from"]
    assert summary["counts"]["delta_issues"] == 2
    assert summary["counts"]["issues"] == 11                # итоговый объём папки


def test_incremental_requires_existing(fetch_env, monkeypatch):
    use_fake_jira(monkeypatch, FakeJira(make_issues(3, PROJECT)))
    with pytest.raises(ValueError, match="нет готовой выгрузки"):
        fetch_project(project_key=PROJECT, incremental=True, **base_kwargs(fetch_env))


def test_incremental_rejects_limits(fetch_env, monkeypatch):
    use_fake_jira(monkeypatch, FakeJira(make_issues(3, PROJECT)))
    fetch_project(project_key=PROJECT, phases="issues", **base_kwargs(fetch_env))
    with pytest.raises(ValueError, match="max_issues"):
        fetch_project(project_key=PROJECT, incremental=True, max_issues=5,
                      **base_kwargs(fetch_env))
    with pytest.raises(ValueError, match="date_to"):
        fetch_project(project_key=PROJECT, incremental=True, date_to="2026-01-01",
                      **base_kwargs(fetch_env))


def test_incremental_explicit_date_from(fetch_env, monkeypatch):
    """Явный date_from перекрывает fetched_at: обновить изменения за период."""
    issues = make_issues(6, PROJECT)
    use_fake_jira(monkeypatch, FakeJira(issues))
    fetch_project(project_key=PROJECT, phases="issues", **base_kwargs(fetch_env))
    # updated у задач = created = 2024-01-01 + i дней; граница «2024-01-03»
    # включительно отбирает PH-2..PH-6
    use_fake_jira(monkeypatch, FakeJira(issues))
    out = fetch_project(project_key=PROJECT, phases="issues", incremental=True,
                        date_from="2024-01-03", **base_kwargs(fetch_env))
    summary = json.loads((out / "fetch_summary.json").read_text(encoding="utf-8"))
    assert summary["counts"]["delta_issues"] == 5
    assert summary["date_from"] == "2024-01-03"   # строка пользователя сохранена
    assert summary["incremental_from"] == "2024-01-03"


# ---------------- resume-ошибка ----------------

def test_resume_error_lists_diff(fetch_env, monkeypatch):
    use_fake_jira(monkeypatch, FakeJira(make_issues(30, PROJECT), fail_after=1))
    with pytest.raises(FetchFailure):
        fetch_project(project_key=PROJECT, phases="issues", **base_kwargs(fetch_env))
    with pytest.raises(ValueError) as ei:
        fetch_project(project_key=PROJECT, phases="issues", resume=True,
                      date_from="2024-06-01", **base_kwargs(fetch_env))
    msg = str(ei.value)
    assert "Разошлись" in msg
    assert "date_from" in msg       # какое поле
    assert "None" in msg            # было / стало
    assert "2024-06-01" in msg
