"""Конвейер анализа по имеющейся выгрузке: результаты, упавшие метрики,
отчёты на диске. Без сети — raw_dir задаётся напрямую."""
from __future__ import annotations

import json

import pytest

from src.core.pipeline import PipelineOptions, run_pipeline


def write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def synthetic_raw(tmp_path):
    raw = tmp_path / "data" / "raw" / "TESTP"
    issues = []
    for i in range(1, 9):
        issues.append({"key": f"TESTP-{i}", "fields": {
            "summary": f"задача {i}", "issuetype": {"name": "Task"},
            "status": {"name": "Done", "statusCategory": {"name": "Done"}},
            "priority": {"name": "Major"}, "assignee": {"displayName": f"user{i % 2}"},
            "reporter": {"displayName": "rep"},
            "created": f"2024-01-0{i}T10:00:00.000+0000",
            "updated": f"2024-02-0{i}T10:00:00.000+0000",
            "resolutiondate": f"2024-02-0{i}T12:00:00.000+0000",
            "resolution": {"name": "Fixed"}, "duedate": None, "components": [],
            "parent": None, "subtasks": [], "timespent": 3600}})
    events = []
    for i in range(1, 9):
        events.append({"key": f"TESTP-{i}", "histories": [
            {"created": f"2024-01-0{i}T11:00:00.000+0000",
             "author": {"displayName": f"user{i % 2}"},
             "items": [{"field": "status", "fromString": "Open",
                        "toString": "In Progress"}]}], "truncated": False})
    write_jsonl(raw / "issues.jsonl", issues)
    write_jsonl(raw / "changelog.jsonl", events)
    return raw


def test_run_pipeline_without_fetch(tmp_path):
    raw = synthetic_raw(tmp_path)
    out = tmp_path / "reports"
    opts = PipelineOptions(project="TESTP", skip_fetch=True,
                           metrics=[{"metric_id": "overview", "params": {}},
                                    {"metric_id": "lifecycle_summary", "params": {}}],
                           raw_dir=raw, out_dir=out)
    result = run_pipeline(opts)
    assert not result["errors"]
    ids = [r.metric_id for r in result["results"]]
    assert ids == ["overview", "lifecycle_summary"]      # порядок = порядок в отчёте
    assert (out / "report.xlsx").exists()
    assert (out / "report.html").exists()


def test_same_metric_twice_with_params(tmp_path):
    raw = synthetic_raw(tmp_path)
    opts = PipelineOptions(project="TESTP", skip_fetch=True,
                           metrics=[{"metric_id": "stale_issues", "params": {"stale_days": 14}},
                                    {"metric_id": "stale_issues", "params": {"stale_days": 45}}],
                           raw_dir=raw, out_dir=tmp_path / "reports")
    result = run_pipeline(opts)
    assert not result["errors"]
    labels = [r.params_label for r in result["results"]]
    assert len(labels) == 2
    assert labels[0] != labels[1]                        # экземпляры различимы


def test_bad_params_become_metric_error(tmp_path):
    raw = synthetic_raw(tmp_path)
    opts = PipelineOptions(project="TESTP", skip_fetch=True,
                           metrics=[{"metric_id": "stale_issues",
                                     "params": {"stale_days": "абракадабра"}}],
                           raw_dir=raw, out_dir=tmp_path / "reports")
    result = run_pipeline(opts)
    assert not result["results"]
    assert result["errors"][0]["metric_id"] == "stale_issues"
    assert "ожидалось число" in result["errors"][0]["error"]


def test_unknown_metric_raises(tmp_path):
    raw = synthetic_raw(tmp_path)
    opts = PipelineOptions(project="TESTP", skip_fetch=True,
                           metrics=[{"metric_id": "нет_такой"}],
                           raw_dir=raw, out_dir=tmp_path / "reports")
    with pytest.raises(ValueError, match="Неизвестные метрики"):
        run_pipeline(opts)


def test_missing_dump_raises(tmp_path):
    opts = PipelineOptions(project="NOPE", skip_fetch=True,
                           raw_dir=tmp_path / "nowhere",
                           out_dir=tmp_path / "reports")
    with pytest.raises(FileNotFoundError, match="выгрузк"):
        run_pipeline(opts)


def test_events_metric_on_issues_only_becomes_hint_error(tmp_path):
    """v7: выгрузка без истории (issues-only) — метрикам с requires=events
    не падаем, а кладём понятную ошибку с подсказкой докачать."""
    raw = synthetic_raw(tmp_path)
    (raw / "changelog.jsonl").unlink()      # имитируем выгрузку phases=issues
    opts = PipelineOptions(project="TESTP", skip_fetch=True,
                           metrics=[{"metric_id": "overview", "params": {}},
                                    {"metric_id": "rework_summary", "params": {}}],
                           raw_dir=raw, out_dir=tmp_path / "reports")
    result = run_pipeline(opts)
    ids = [r.metric_id for r in result["results"]]
    assert ids == ["overview"]              # метрики без событий живут
    err = result["errors"][0]
    assert err["metric_id"] == "rework_summary"
    assert "история изменений" in err["error"]
    assert "--phases changelog" in err["error"]
