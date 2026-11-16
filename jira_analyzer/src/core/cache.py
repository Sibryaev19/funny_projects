"""Parquet-кеш таблиц: data/processed/<ключ>/ рядом с data/raw/<ключ>/.

Зачем: разбор сырых JSONL (дедуп, разворачивание histories) на больших
выгрузках занимает минуты — и повторялся при каждом анализе. Кеш хранит
ГОТОВЫЕ pandas-таблицы в parquet; анализ читает их за секунды. Сырые и
обработанные данные в раздельных папках, поэтому идущая параллельно
выгрузка (пишет в raw) не мешает анализу (читает processed).

Инвалидация: manifest.json фиксирует размер+mtime_ns каждого jsonl; любое
изменение сырых файлов (докачка истории, «обновить») → кеш пересобирается
один раз при следующем анализе. Отсутствие кеша — не ошибка: он строится на
месте. pyarrow не установлен → тихо работаем по-старому (чтение JSONL).
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

RAW_FILES = ("issues.jsonl", "changelog.jsonl", "comments.jsonl")
TABLE_FILES = {"issues": "issues.parquet", "events": "events.parquet",
               "comments": "comments.parquet"}
MANIFEST = "manifest.json"

try:
    import pyarrow  # noqa: F401
    HAVE_PARQUET = True
except ImportError:
    HAVE_PARQUET = False


def processed_dir(raw_dir: Path) -> Path:
    """data/raw/<ключ> -> data/processed/<ключ> (тот же DATA_DIR).
    parent.parent: у raw_dir parent — это сам data/raw, нам нужен data/."""
    return raw_dir.parent.parent / "processed" / raw_dir.name


def _signature(raw_dir: Path) -> dict[str, list[int]] | None:
    """[размер, mtime_ns] каждого сырого jsonl; None, если issues.jsonl нет."""
    out: dict[str, list[int]] = {}
    for name in RAW_FILES:
        p = raw_dir / name
        if p.exists():
            st = p.stat()
            out[name] = [st.st_size, st.st_mtime_ns]
    if "issues.jsonl" not in out:
        return None
    return out


def _fresh(raw_dir: Path, proc_dir: Path) -> bool:
    sig = _signature(raw_dir)
    if sig is None:
        return False
    try:
        manifest = json.loads((proc_dir / MANIFEST).read_text(encoding="utf-8"))
    except Exception:
        return False
    if manifest.get("files") != sig:
        return False
    return all((proc_dir / f).exists() for f in TABLE_FILES.values())


def _load_parquet(proc_dir: Path, schemas: dict[str, list[str]]) -> dict[str, pd.DataFrame]:
    out = {}
    for table, fname in TABLE_FILES.items():
        df = pd.read_parquet(proc_dir / fname)
        # пустые таблицы: гарантировать колонки (метрики ждут схему)
        if df.empty and not len(df.columns):
            df = pd.DataFrame(columns=schemas.get(table))
        out[table] = df
    return out


def _store(proc_dir: Path, tables: dict[str, pd.DataFrame],
           sig: dict[str, list[int]]) -> None:
    proc_dir.mkdir(parents=True, exist_ok=True)
    for table, fname in TABLE_FILES.items():
        tmp = proc_dir / (fname + ".tmp")
        tables[table].to_parquet(tmp, index=False)
        os.replace(tmp, proc_dir / fname)   # атомарно: читатели не видят полфайла
    # манифест последним: его наличие = кеш собран целиком
    (proc_dir / MANIFEST).write_text(
        json.dumps({"files": sig}, ensure_ascii=False), encoding="utf-8")
    log.info("Parquet-кеш записан: %s", proc_dir)
