"""Возобновление выгрузки: чекпоинты, падение → продолжение, стоп, отпечаток
параметров, дедуп при коммите. Полный цикл: результат «упал → продолжил»
обязан совпасть с результатом одного цельного прогона."""
from __future__ import annotations

import json
import threading

import pytest

from conftest import FetchFailure, FakeJira, make_issues, raw_dir_of, use_fake_jira
from src.fetch.run import FetchStopped, fetch_project
from src.fetch.state import load_state

PROJECT = "CURATOR"


def read_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def issue_keys(path):
    return [r["key"] for r in read_jsonl(path)]


def base_kwargs(fetch_env, **extra):
    kw = dict(jira_type="apache", env_path=fetch_env["env_path"])
    kw.update(extra)
    return kw


def test_crash_then_resume_equals_full_run(tmp_path, fetch_env, monkeypatch):
    issues = make_issues(57, histories=3)
    # эталон: один цельный прогон
    use_fake_jira(monkeypatch, FakeJira(issues))
    ref_dir = fetch_project(project_key=PROJECT + "-REF", **base_kwargs(fetch_env))
    ref_keys = issue_keys(ref_dir / "issues.jsonl")
    ref_events = {r["key"]: len(r["histories"]) for r in read_jsonl(ref_dir / "changelog.jsonl")}

    # прогон 1: сеть падает посреди фазы changelog (после страниц задач)
    pages_of_issues = -(-57 // fetch_env["page_size"])          # 6 страниц
    fail_after = pages_of_issues + 2                            # пара пачек changelog
    use_fake_jira(monkeypatch, FakeJira(issues, fail_after=fail_after))
    with pytest.raises(FetchFailure):
        fetch_project(project_key=PROJECT, **base_kwargs(fetch_env))
    raw = raw_dir_of(fetch_env, PROJECT)
    assert not (raw / "issues.jsonl").exists()      # корень не тронут
    assert (raw / "_partial" / "state.json").exists()
    st = load_state(raw / "_partial")
    assert st["files"]["issues.jsonl"]["count"] == 57
    assert 0 < st["files"]["changelog.jsonl"]["count"] < -(-57 // fetch_env["expand_batch"])

    # прогон 2: продолжение — должно докачать и собрать то же, что эталон
    use_fake_jira(monkeypatch, FakeJira(issues))
    out = fetch_project(project_key=PROJECT, resume=True, **base_kwargs(fetch_env))
    assert issue_keys(out / "issues.jsonl") == ref_keys
    assert not (raw / "_partial").exists()          # чекпоинт подчищен
    got = {r["key"]: len(r["histories"]) for r in read_jsonl(out / "changelog.jsonl")}
    assert got == ref_events                        # без дублей и потерь


def test_stop_saves_progress_and_resume(tmp_path, fetch_env, monkeypatch):
    issues = make_issues(40)
    use_fake_jira(monkeypatch, FakeJira(issues))
    stop = threading.Event()

    def progress(phase, done, total):
        if done >= 25:
            stop.set()

    with pytest.raises(FetchStopped):
        fetch_project(project_key=PROJECT, stop_event=stop, progress=progress,
                      **base_kwargs(fetch_env))
    raw = raw_dir_of(fetch_env, PROJECT)
    st = load_state(raw / "_partial")
    assert st["phase"] == "issues"
    # чекпоинт — по границе страницы: написано 25 задач, зафиксированы 20
    # (хвост без чекпоинта срежется при возобновлении)
    assert st["files"]["issues.jsonl"]["count"] == 20

    use_fake_jira(monkeypatch, FakeJira(issues))
    out = fetch_project(project_key=PROJECT, resume=True, **base_kwargs(fetch_env))
    assert issue_keys(out / "issues.jsonl") == [f"CURATOR-{i}" for i in range(1, 41)]


def test_resume_requires_matching_fingerprint(fetch_env, monkeypatch):
    issues = make_issues(30)
    use_fake_jira(monkeypatch, FakeJira(issues, fail_after=3))
    with pytest.raises(FetchFailure):
        fetch_project(project_key=PROJECT, **base_kwargs(fetch_env))
    # другие даты — продолжать нельзя
    use_fake_jira(monkeypatch, FakeJira(issues))
    with pytest.raises(ValueError, match="заново"):
        fetch_project(project_key=PROJECT, resume=True, date_from="2024-03-01",
                      **base_kwargs(fetch_env))
    # fresh стирает прогресс и качает с нуля
    out = fetch_project(project_key=PROJECT, fresh=True, **base_kwargs(fetch_env))
    assert len(issue_keys(out / "issues.jsonl")) == 30
    assert not (raw_dir_of(fetch_env, PROJECT) / "_partial").exists()


def test_partial_exists_requires_explicit_choice(fetch_env, monkeypatch):
    issues = make_issues(20)
    use_fake_jira(monkeypatch, FakeJira(issues, fail_after=2))
    with pytest.raises(FetchFailure):
        fetch_project(project_key=PROJECT, **base_kwargs(fetch_env))
    # без флагов — отказ с подсказкой
    use_fake_jira(monkeypatch, FakeJira(issues))
    with pytest.raises(ValueError, match="Продолжить"):
        fetch_project(project_key=PROJECT, **base_kwargs(fetch_env))
    # resume без фактического прогресса после fresh — тоже ошибка
    fetch_project(project_key=PROJECT, fresh=True, **base_kwargs(fetch_env))
    with pytest.raises(ValueError, match="без --resume"):
        fetch_project(project_key=PROJECT, resume=True, **base_kwargs(fetch_env))


def test_truncated_histories_doload_and_dedup(fetch_env, monkeypatch):
    """Задача с >100 событий: expand даёт 100 (truncated), эндпоинт докачивает
    полную историю; при чтении таблиц события не должны удваиваться."""
    issues = make_issues(12, histories=105)
    use_fake_jira(monkeypatch, FakeJira(issues))
    out = fetch_project(project_key=PROJECT, **base_kwargs(fetch_env))
    recs = read_jsonl(out / "changelog.jsonl")
    assert len(recs) == 12   # по одной записи на задачу (дедуп последним)
    assert all(len(r["histories"]) == 105 for r in recs)

    from src.core.tables import load_tables
    tables = load_tables(out)
    per_key = tables["events"]["key"].value_counts()
    assert per_key.tolist() == [105] * 12   # фикс double-count


def test_summary_counts_and_expected(fetch_env, monkeypatch):
    issues = make_issues(23)
    use_fake_jira(monkeypatch, FakeJira(issues))
    out = fetch_project(project_key=PROJECT, **base_kwargs(fetch_env))
    summary = json.loads((out / "fetch_summary.json").read_text(encoding="utf-8"))
    assert summary["counts"]["issues"] == 23
    assert summary["counts"]["issues_expected"] == 23
    assert summary["counts"]["tree_roots"] >= 1
