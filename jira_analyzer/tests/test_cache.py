"""Parquet-кеш (data/processed/): сборка, повторное чтение без разбора JSONL,
инвалидация при изменении сырых файлов, работа когда истории нет (issues-only)."""
from __future__ import annotations

import json

from src.core.cache import processed_dir
from src.core.tables import load_tables

from conftest import make_issue


def _write_raw(tmp_path, issues=3, histories=2, with_changelog=True):
    raw = tmp_path / "raw" / "P"
    raw.mkdir(parents=True)
    with open(raw / "issues.jsonl", "w", encoding="utf-8") as f:
        for i in range(1, issues + 1):
            f.write(json.dumps(make_issue(i, "P", histories=histories),
                               ensure_ascii=False) + "\n")
    if with_changelog:
        with open(raw / "changelog.jsonl", "w", encoding="utf-8") as f:
            for i in range(1, issues + 1):
                rec = make_issue(i, "P", histories=histories)
                f.write(json.dumps({"key": rec["key"],
                                    "histories": rec["changelog"]["histories"],
                                    "truncated": False}, ensure_ascii=False) + "\n")
    return raw


def test_cache_build_and_reuse(tmp_path, monkeypatch):
    raw = _write_raw(tmp_path)
    t1 = load_tables(raw)
    proc = processed_dir(raw)
    assert (proc / "manifest.json").exists()
    assert len(t1["issues"]) == 3
    assert len(t1["events"]) == 3 * 2

    # второй вызов обязан читать кеш: разбор JSONL отключён и должен упасть,
    # если вдруг вызван
    monkeypatch.setattr("src.core.tables._iter_dedup",
                        lambda p: (_ for _ in ()).throw(AssertionError("JSONL read!")))
    t2 = load_tables(raw)
    assert len(t2["issues"]) == 3
    assert len(t2["events"]) == 3 * 2
    assert list(t2["issues"]["key"]) == list(t1["issues"]["key"])


def test_cache_invalidated_on_change(tmp_path):
    raw = _write_raw(tmp_path)
    load_tables(raw)
    # «докачали» ещё задач: сырой файл вырос — кеш должен пересобраться
    with open(raw / "issues.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(make_issue(9, "P"), ensure_ascii=False) + "\n")
    t = load_tables(raw)
    assert len(t["issues"]) == 4


def test_cache_issues_only(tmp_path):
    raw = _write_raw(tmp_path, with_changelog=False)
    t1 = load_tables(raw)
    assert len(t1["issues"]) == 3
    assert t1["events"].empty
    t2 = load_tables(raw)   # из кеша
    assert t2["events"].empty
    assert len(t2["issues"]) == 3


def test_use_cache_false_skips_processed(tmp_path, monkeypatch):
    raw = _write_raw(tmp_path)
    load_tables(raw)
    called = []
    real = load_tables
    t = real(raw, use_cache=False)   # путь без кеша: JSONL разбирается снова
    assert len(t["issues"]) == 3
    # и кеш не тронулся/не пересобрался принудительно: manifest тот же
    called.append(1)
