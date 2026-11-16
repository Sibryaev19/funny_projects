"""Фазы выгрузки (v7): issues-only, changelog-only, обе.

Changelog-only докачивает историю к УЖЕ скачанным задачам: issues.jsonl
в корне папки не меняется, changelog.jsonl появляется. Без issues.jsonl —
понятная ошибка. Устаревший skip_changelog=True = phases="issues".
"""
from __future__ import annotations

import json

import pytest

from src.fetch.run import fetch_project

from conftest import FakeJira, make_issues, use_fake_jira

PROJECT = "PH"


def read_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def base_kwargs(fetch_env, **extra):
    kw = dict(jira_type="apache", env_path=fetch_env["env_path"])
    kw.update(extra)
    return kw


def test_issues_only_then_changelog_only(fetch_env, monkeypatch):
    issues = make_issues(15, PROJECT, histories=3)
    use_fake_jira(monkeypatch, FakeJira(issues))

    out = fetch_project(project_key=PROJECT, phases="issues", **base_kwargs(fetch_env))
    issues_before = (out / "issues.jsonl").read_bytes()
    assert len(read_jsonl(out / "issues.jsonl")) == 15
    assert not (out / "changelog.jsonl").exists()
    summary = json.loads((out / "fetch_summary.json").read_text(encoding="utf-8"))
    assert summary["phases"] == "issues"

    # докачка истории: issues.jsonl не тронут, changelog появился
    out2 = fetch_project(project_key=PROJECT, phases="changelog",
                         **base_kwargs(fetch_env))
    assert (out / "issues.jsonl").read_bytes() == issues_before
    recs = read_jsonl(out / "changelog.jsonl")
    assert sorted(r["key"] for r in recs) == sorted(i["key"] for i in issues)
    summary = json.loads((out2 / "fetch_summary.json").read_text(encoding="utf-8"))
    assert summary["phases"] == "changelog"


def test_changelog_only_requires_issues(fetch_env, monkeypatch):
    use_fake_jira(monkeypatch, FakeJira(make_issues(5, PROJECT)))
    with pytest.raises(ValueError, match="issues.jsonl"):
        fetch_project(project_key=PROJECT, phases="changelog",
                      **base_kwargs(fetch_env))


def test_legacy_skip_changelog_alias(fetch_env, monkeypatch):
    use_fake_jira(monkeypatch, FakeJira(make_issues(5, PROJECT)))
    out = fetch_project(project_key=PROJECT, skip_changelog=True,
                        **base_kwargs(fetch_env))
    assert (out / "issues.jsonl").exists()
    assert not (out / "changelog.jsonl").exists()


def test_phases_fingerprint_guards_resume(fetch_env, monkeypatch):
    issues = make_issues(15, PROJECT)
    jira = FakeJira(issues)
    use_fake_jira(monkeypatch, jira)
    # issues-only не создаёт changelog.jsonl, резюме другой фазы — новый запрос
    fetch_project(project_key=PROJECT, phases="issues", **base_kwargs(fetch_env))
    with pytest.raises(ValueError, match="Незавершённой выгрузки не найдено"):
        fetch_project(project_key=PROJECT, phases="changelog", resume=True,
                      **base_kwargs(fetch_env))
