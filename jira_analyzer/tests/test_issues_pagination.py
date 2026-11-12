"""Пагинация задач: лимит глубины сервера → окна по created; лимиты, дедуп,
валидация дат и включительность date_to."""
from __future__ import annotations

import pytest

from conftest import FakeClient, FakeJira, make_issue, make_issues
from src.fetch.issues import build_jql, iter_issues, validate_dates

FIELDS = ["summary", "created"]


def collect(client, **kw):
    return [i["key"] for i in iter_issues(client, "CURATOR", FIELDS, **kw)]


def test_full_pagination():
    jira = FakeJira(make_issues(57))
    keys = collect(FakeClient(jira), page_size=10)
    assert keys == [f"CURATOR-{i}" for i in range(1, 58)]


def test_cap_fallback_via_created_windows():
    """Сервер отдаёт пустые страницы с startAt>=30: обычная пагинация обрывается,
    деление диапазона по created должно добрать все задачи."""
    jira = FakeJira(make_issues(57), cap_start_at=30)
    keys = collect(FakeClient(jira), page_size=10)
    assert len(keys) == 57
    assert len(set(keys)) == 57   # без дублей на стыках окон


def test_cap_inside_small_window_degrades_gracefully():
    """Окно мельче часа делить нельзя — берём сколько дали, без зависаний."""
    from datetime import datetime, timedelta, timezone
    issues = []
    base = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
    for i in range(1, 58):
        iss = make_issue(i)
        iss["fields"]["created"] = (base + timedelta(seconds=i)).strftime(
            "%Y-%m-%dT%H:%M:%S.000+0000")
        issues.append(iss)
    jira = FakeJira(issues, cap_start_at=20)
    keys = collect(FakeClient(jira), page_size=10,
                   date_from="2024-06-01", date_to="2024-06-01")
    # сколько-то скачали (сколько отдал сервер), но не упали и не зависли;
    # громкое предупреждение о неполноте попадает в лог
    assert 0 < len(keys) < 57


def test_max_issues_cut():
    jira = FakeJira(make_issues(100))
    keys = collect(FakeClient(jira), page_size=10, max_issues=25)
    assert keys == [f"CURATOR-{i}" for i in range(1, 26)]


def test_resume_position_and_dedup():
    issues = make_issues(50)
    jira = FakeJira(issues)
    first10 = {f"CURATOR-{i}" for i in range(1, 11)}
    rest = collect(FakeClient(FakeJira(issues)), page_size=10,
                   start_at=10, seen=set(first10))
    assert rest == [f"CURATOR-{i}" for i in range(11, 51)]


def test_on_total_reports_server_total():
    seen_totals = []
    jira = FakeJira(make_issues(35))
    for _ in iter_issues(FakeClient(jira), "CURATOR", FIELDS, page_size=10,
                         on_total=lambda t: seen_totals.append(t)):
        break
    assert seen_totals == [35]


def test_validate_dates():
    validate_dates("2024-01-01", "2024-06-30")
    validate_dates(None, None)
    with pytest.raises(ValueError, match="ГГГГ-ММ-ДД"):
        validate_dates("01.01.2024", None)
    with pytest.raises(ValueError, match="позже"):
        validate_dates("2024-06-30", "2024-01-01")


def test_build_jql_quotes_and_inclusive_date_to():
    jql = build_jql("MY PROJ", "2024-01-01", "2024-05-31")
    assert 'project = "MY PROJ"' in jql
    assert 'created >= "2024-01-01"' in jql
    # date_to включительно: верхняя граница — полночь следующего дня
    assert 'created < "2024-06-01"' in jql
    assert "ORDER BY created ASC" in jql
