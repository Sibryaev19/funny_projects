"""Web API без сети: санитизация проекта, валидация дат, правила
resume/fresh при незавершённом прогрессе, список выгрузок с _partial,
эндпоинт стопа и его проводка до fetch. Реальный запуск заданий подменяется."""
from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from src.fetch.run import FetchStopped
from src.web import app as app_mod


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_mod, "RAW_ROOT", tmp_path / "raw")
    # задания не запускаем: фиксируем параметры (последнего вызова) и возвращаем фейковый id
    captured = {}
    monkeypatch.setattr(app_mod, "start_job",
                        lambda opts: (captured.update(opts=opts), "job-1")[1])
    return TestClient(app_mod.app), captured


def make_partial(raw_root: str, project: str):
    p = raw_root / project / "_partial"
    p.mkdir(parents=True, exist_ok=True)
    (p / "state.json").write_text(json.dumps({
        "phase": "issues",
        "files": {"issues.jsonl": {"offset": 123, "count": 12}},
        "totals": {"issues": 100},
        "updated_at": "2026-11-13T08:00:00+00:00",
        "fingerprint": {"jira_type": "apache", "date_from": None, "date_to": None,
                        "max_issues": None, "with_comments": False},
    }, ensure_ascii=False), encoding="utf-8")


def test_project_sanitization(client):
    c, _ = client
    r = c.post("/api/fetch", json={"project": "../evil", "jira_type": "apache",
                                   "fresh": True})
    assert r.status_code == 400
    r = c.post("/api/fetch", json={"project": "", "fresh": True})
    assert r.status_code == 400


def test_date_validation_on_endpoints(client):
    c, captured = client
    r = c.post("/api/fetch", json={"project": "PROJ", "date_from": "01.02.2024",
                                   "fresh": True})
    assert r.status_code == 400
    assert "ГГГГ-ММ-ДД" in r.json()["detail"]
    r = c.post("/api/fetch", json={"project": "PROJ", "date_from": "2024-06-01",
                                   "date_to": "2024-01-01", "fresh": True})
    assert r.status_code == 400
    r = c.post("/api/run", json={"project": "PROJ", "date_to": "кривая",
                                 "skip_fetch": True})
    assert r.status_code == 200        # анализ не ходит в Jira — даты не важны
    assert captured["opts"].skip_fetch


def test_fetch_partial_requires_explicit_choice(client):
    c, _ = client
    make_partial(_raw_root(c), "PROJ")
    r = c.post("/api/fetch", json={"project": "PROJ"})
    assert r.status_code == 409
    r = c.post("/api/fetch", json={"project": "PROJ", "resume": True})
    assert r.status_code == 200
    r = c.post("/api/fetch", json={"project": "PROJ", "fresh": True})
    assert r.status_code == 200
    r = c.post("/api/fetch", json={"project": "OTHER", "resume": True})
    assert r.status_code == 400        # resume без прогресса


def test_fetch_phases_param(client):
    """phases доходит до PipelineOptions; changelog-only не валидирует даты
    (они не участвуют) и не требует их, issues-фаза — требует."""
    c, captured = client
    r = c.post("/api/fetch", json={"project": "PROJ", "phases": "кривая"})
    assert r.status_code == 400
    r = c.post("/api/fetch", json={"project": "PROJ", "phases": "issues",
                                   "date_from": "01.02.2024"})
    assert r.status_code == 400        # даты для задач валидируются
    r = c.post("/api/fetch", json={"project": "PROJ", "phases": "changelog",
                                   "date_from": "кривая"})
    assert r.status_code == 200        # changelog-only даты не смотрит
    assert captured["opts"].phases == "changelog"
    r = c.post("/api/fetch", json={"project": "PROJ", "phases": "issues"})
    assert r.status_code == 200
    assert captured["opts"].phases == "issues"


def _raw_root(client):
    from src.web.app import RAW_ROOT
    return RAW_ROOT


def test_downloads_lists_partial_only_folder(client):
    c, _ = client
    from src.web.app import RAW_ROOT
    make_partial(RAW_ROOT, "PROJ")
    data = c.get("/api/downloads").json()
    items = {d["folder"]: d for d in data["downloads"]}
    assert "PROJ" in items
    p = items["PROJ"]["partial"]
    assert p["phase"] == "issues"
    assert p["files"]["issues.jsonl"] == 12
    assert p["totals"]["issues"] == 100
    assert p["params"]["jira_type"] == "apache"


def test_downloads_full_folder(client):
    c, _ = client
    from src.web.app import RAW_ROOT
    d = RAW_ROOT / "FULL"
    d.mkdir(parents=True)
    (d / "issues.jsonl").write_text("{}", encoding="utf-8")
    (d / "fetch_summary.json").write_text(json.dumps({
        "jira_type": "jira", "jira_url": "http://x", "fetched_at": "2026-11-12T10:00:00+00:00",
        "counts": {"issues": 5, "changelog_events": 9}, "with_comments": False}),
        encoding="utf-8")
    items = {x["folder"]: x for x in c.get("/api/downloads").json()["downloads"]}
    assert items["FULL"]["issues"] == 5
    assert items["FULL"]["partial"] is None


def test_stop_endpoint_unknown_job(client):
    c, _ = client
    assert c.post("/api/job/unknown/stop").status_code == 409


def test_metrics_endpoint_shape(client):
    c, _ = client
    data = c.get("/api/metrics").json()
    assert data["metrics"]
    m = data["metrics"][0]
    assert {"id", "label", "description", "params"} <= set(m)
    for p in m["params"]:
        assert {"name", "label", "type", "default"} <= set(p)


def test_stop_wiring_reaches_fetch(monkeypatch):
    """«Стоп» из web обязан дойти до fetch (opts.stop_event = job.stop_event):
    регрессионный тест на реальную проводку, не только на эндпоинт."""
    from src.core.pipeline import PipelineOptions
    from src.web import jobs as jobs_mod

    def fake_run_fetch(opts):
        assert opts.stop_event is not None, "stop_event не подключён к opts"
        if opts.stop_event.wait(timeout=1.5):
            raise FetchStopped("остановлено по запросу")
        return {}   # стоп не пришёл — задание доработало бы «успешно»

    monkeypatch.setattr(jobs_mod, "run_fetch", fake_run_fetch)
    job_id = jobs_mod.start_job(PipelineOptions(project="X", fetch_only=True))
    time.sleep(0.2)
    assert jobs_mod.request_stop(job_id) is True
    deadline = time.time() + 3
    while jobs_mod.get_job(job_id).status == "running" and time.time() < deadline:
        time.sleep(0.05)
    assert jobs_mod.get_job(job_id).status == "stopped"
