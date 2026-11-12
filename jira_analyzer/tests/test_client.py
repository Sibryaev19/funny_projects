"""JiraClient: ретраи с прогрессивной задержкой, Retry-After, фатальные коды."""
from __future__ import annotations

import pytest
import requests

from conftest import FakeResponse
from src.fetch.client import JiraApiError, JiraClient


class ScriptedSession:
    """Ответы по сценарию: последний элемент повторяется бесконечно."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append(params)
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(item, Exception):
            raise item
        return item


def make_client(script, **kw):
    client = JiraClient("http://jira.test", auth="none", delay=0,
                        max_retries=kw.pop("max_retries", 3),
                        backoff_step=kw.pop("backoff_step", 0.001), **kw)
    client.session = ScriptedSession(script)
    return client


def test_success_first_try():
    client = make_client([FakeResponse({"ok": 1})])
    assert client.get("rest/api/2/search")["ok"] == 1


def test_retry_on_500_then_success(monkeypatch):
    pauses = []
    monkeypatch.setattr("src.fetch.client.time.sleep", pauses.append)
    client = make_client([FakeResponse({"err": 1}, status=500),
                          FakeResponse({"ok": 1})])
    assert client.get("x")["ok"] == 1
    assert len(pauses) == 1
    assert pauses[0] == pytest.approx(0.001)


def test_retry_after_header_respected(monkeypatch):
    pauses = []
    monkeypatch.setattr("src.fetch.client.time.sleep", pauses.append)
    client = make_client([FakeResponse({}, status=429, headers={"Retry-After": "0.005"}),
                          FakeResponse({"ok": 1})])
    client.get("x")
    assert pauses == [pytest.approx(0.005)]


def test_fatal_401_no_retry():
    client = make_client([FakeResponse({}, status=401)])
    with pytest.raises(JiraApiError, match="PAT"):
        client.get("x")
    assert len(client.session.calls) == 1   # без ретраев


def test_network_errors_exhausted(monkeypatch):
    monkeypatch.setattr("src.fetch.client.time.sleep", lambda s: None)
    client = make_client([requests.ConnectionError("down")], max_retries=2)
    with pytest.raises(JiraApiError, match="ретра"):
        client.get("x")
    assert len(client.session.calls) == 3   # первая + 2 ретрая


def test_bearer_requires_pat():
    with pytest.raises(ValueError, match="PAT"):
        JiraClient("http://jira.test", auth="bearer", pat=None)
