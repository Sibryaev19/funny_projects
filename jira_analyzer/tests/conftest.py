"""Общие приспособления тестов: мини-стенд Jira REST API без сети.

FakeJira имитирует то, чем пользуется fetch: search-эндпоинт (пагинация по
startAt, expand=changelog), отдельный эндпоинт /issue/{key}/changelog,
настраиваемый лимит глубины пагинации (cap_start_at) и отказы после N запросов.
Тесты fetch гоняют настоящий fetch_project, подменяя только HTTP-слой.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

import pytest

from src.fetch.client import JiraApiError


# ---------------- фейковые HTTP-ответы ----------------

class FakeResponse:
    def __init__(self, payload, status=200, headers=None):
        self._payload = payload
        self.status_code = status
        self.headers = headers or {}
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class FakeClient:
    """Подходит на место JiraClient там, где нужен только .get()."""

    def __init__(self, jira: "FakeJira"):
        self.jira = jira

    def get(self, path, params=None):
        return self.jira.get(path, params)


# ---------------- конструирование задач ----------------

def make_issue(i: int, project: str = "CURATOR", *, histories: int = 2,
               resolved: bool | None = None) -> dict:
    """Задача как в ответе Jira: strictly возрастающий created по i,
    changelog с указанным числом событий (статусные смены)."""
    key = f"{project}-{i}"
    base = datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(days=i, hours=i % 5)
    created = base.strftime("%Y-%m-%dT%H:%M:%S.000+0000")
    done = resolved if resolved is not None else (i % 3 == 0)
    items = []
    hs = []
    for k in range(histories):
        when = (base + timedelta(hours=k + 1)).strftime("%Y-%m-%dT%H:%M:%S.000+0000")
        hs.append({
            "created": when,
            "author": {"displayName": f"user{k % 3}"},
            "items": [{"field": "status", "fromString": "Open",
                       "toString": f"State {k}"}],
        })
    fields = {
        "summary": f"Задача {i}",
        "issuetype": {"name": "Task"},
        "status": {"name": "Done" if done else "In Progress",
                   "statusCategory": {"name": "Done" if done else "In Progress"}},
        "priority": {"name": "Major"},
        "assignee": {"displayName": f"user{i % 7}"},
        "reporter": {"displayName": "reporter"},
        "created": created,
        "updated": created,
        "resolutiondate": created if done else None,
        "resolution": {"name": "Fixed"} if done else None,
        "duedate": None,
        "components": [{"name": f"comp{i % 3}"}],
        "parent": None,
        "subtasks": [],
        "timespent": 3600 * (i % 4),
    }
    return {"key": key, "fields": fields,
            "changelog": {"histories": hs, "total": len(hs)}}


def make_issues(n: int, project: str = "CURATOR", **kw) -> list[dict]:
    return [make_issue(i, project, **kw) for i in range(1, n + 1)]


# ---------------- мини-стенд Jira ----------------

class FakeJira:
    """search + /issue/{key}/changelog.

    cap_start_at — серверный лимит глубины: страницы с startAt >= cap отдаются
    пустыми БЕЗ ошибки (как на настоящем Jira Server при jira.search.max.results).
    fail_after — поднять сетевую ошибку после N запросов (симуляция падения).
    """

    def __init__(self, issues, *, cap_start_at=None, fail_after=None):
        self.issues = list(issues)
        self.cap_start_at = cap_start_at
        self.fail_after = fail_after
        self.requests = 0

    def get(self, path, params=None):
        self.requests += 1
        if self.fail_after is not None and self.requests > self.fail_after:
            raise ConnectionError("simulated network failure")
        params = params or {}
        if path == "rest/api/2/search":
            return self._search(params)
        m = re.match(r"rest/api/2/issue/([^/]+)/changelog$", path)
        if m:
            return self._issue_changelog(m.group(1), params)
        raise AssertionError(f"FakeJira: неизвестный путь {path}")

    @staticmethod
    def _parse_ts(s: str) -> datetime:
        s = s.strip('"')
        for fmt in ("%Y/%m/%d %H:%M", "%Y-%m-%d"):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
        raise ValueError(f"FakeJira: не разобрал дату JQL {s!r}")

    def _search(self, params) -> FakeResponse:
        jql = params.get("jql", "")
        keys = None
        lo = hi = None
        m = re.search(r"key in \(([^)]*)\)", jql)
        if m:
            keys = [k.strip() for k in m.group(1).split(",") if k.strip()]
        else:
            mlo = re.search(r'created >= "([^"]+)"', jql)
            mhi = re.search(r'created < "([^"]+)"', jql)
            lo = self._parse_ts(mlo.group(1)) if mlo else None
            hi = self._parse_ts(mhi.group(1)) if mhi else None

        selected = []
        for iss in self.issues:
            if keys is not None:
                if iss["key"] in keys:
                    selected.append(iss)
                continue
            created = datetime.strptime(iss["fields"]["created"][:19],
                                        "%Y-%m-%dT%H:%M:%S")
            if lo is not None and created < lo:
                continue
            if hi is not None and created >= hi:
                continue
            selected.append(iss)
        selected.sort(key=lambda x: x["fields"]["created"])

        start = int(params.get("startAt") or 0)
        maxr = int(params.get("maxResults") or 0)
        if self.cap_start_at is not None and start >= self.cap_start_at:
            page = []
        else:
            page = selected[start:start + maxr]

        out = []
        for iss in page:
            iss = json.loads(json.dumps(iss))   # копия: expand меняет changelog
            if params.get("expand") == "changelog":
                cl = iss.get("changelog") or {"histories": [], "total": 0}
                # API отдаёт максимум 100 последних историй
                iss["changelog"] = {"histories": cl["histories"][:100],
                                    "total": cl["total"]}
            out.append(iss)
        return {"issues": out, "total": len(selected),
                "startAt": start, "maxResults": maxr}

    def _issue_changelog(self, key: str, params) -> dict:
        iss = next((x for x in self.issues if x["key"] == key), None)
        hs = (iss.get("changelog") or {}).get("histories", []) if iss else []
        start = int(params.get("startAt") or 0)
        maxr = int(params.get("maxResults") or 0)
        return {"histories": hs[start:start + maxr], "total": len(hs)}


# ---------------- приспособления ----------------

ENV_TMPL = """APACHE_URL=http://jira.test
APACHE_AUTH=none
DATA_DIR={data_dir}
FETCH_DELAY=0
FETCH_MAX_RETRIES=1
FETCH_BACKOFF_STEP=0
FETCH_PAGE_SIZE={page_size}
FETCH_EXPAND_BATCH={expand_batch}
"""


@pytest.fixture
def fetch_env(tmp_path, request):
    """Временный .env: DATA_DIR в tmp (проект не засоряем), быстрые паузы,
    мелкие страницы/пачки — чтобы тесты проходили фазы и чекпоинты."""
    marker = getattr(request, "node", None)
    page_size = 10
    expand_batch = 7
    env = tmp_path / ".env"
    env.write_text(ENV_TMPL.format(data_dir=(tmp_path / "data").as_posix(),
                                   page_size=page_size, expand_batch=expand_batch),
                   encoding="utf-8")
    return {"env_path": env, "data_dir": tmp_path / "data",
            "page_size": page_size, "expand_batch": expand_batch}


def use_fake_jira(monkeypatch, jira: FakeJira) -> None:
    """Подменить JiraClient в fetch-слое на обёртку над FakeJira."""
    from src.fetch import run as run_mod

    class _Client:
        def __init__(self, url, **kw):
            self.url = url
            self.jira = jira

        def get(self, path, params=None):
            return jira.get(path, params)

    monkeypatch.setattr(run_mod, "JiraClient", _Client)


# отказ выгрузки в тестах: FakeClient не оборачивает сбои в JiraApiError
# (как настоящий JiraClient), поэтому сетевые отказы прилетают как есть
FetchFailure = (JiraApiError, ConnectionError, TimeoutError)


def raw_dir_of(fetch_env, project: str):
    return fetch_env["data_dir"] / "raw" / project


__all__ = ["FakeResponse", "FakeClient", "FakeJira", "make_issue", "make_issues",
           "use_fake_jira", "raw_dir_of", "JiraApiError", "FetchFailure"]
