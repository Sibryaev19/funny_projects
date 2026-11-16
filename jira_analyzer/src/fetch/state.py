"""Чекпоинт незавершённой выгрузки: data/raw/<КЛЮЧ>/_partial/ + state.json.

fetch_project пишет файлы в _partial/ и после каждой страницы задач / пачки
changelog / задачи догрузки сбрасывает состояние: байтовые офсеты файлов,
счётчики, фазу, отпечаток параметров. Корень папки проекта не меняется до
полного успеха: упавший или остановленный fetch оставляет прошлую целую
выгрузку нетронутой. Возобновление срезает файлы по офсетам (недописанный
хвост после чекпоинта) и продолжает с той же позиции.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
from pathlib import Path

STATE_NAME = "state.json"


def fingerprint(project: str, jira_type: str, url: str, *, date_from, date_to,
                max_issues, phases, with_comments, fields) -> dict:
    """Отпечаток параметров: продолжать можно только полностью совпадающую
    выгрузку (иначе в одной папке смешаются данные разных настроек)."""
    return {
        "project": project,
        "jira_type": jira_type,
        "url": url,
        "date_from": date_from,
        "date_to": date_to,
        "max_issues": max_issues,
        "phases": phases,
        "with_comments": with_comments,
        "fields": hashlib.md5(json.dumps(fields, sort_keys=True).encode("utf-8")).hexdigest(),
    }


def load_state(partial_dir: Path) -> dict | None:
    """Прочитать чекпоинт; None = прогресса нет (или файл нечитаем — считаем,
    что нет: пользователь может начать заново)."""
    path = partial_dir / STATE_NAME
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) and data.get("phase") else None


def clear_partial(partial_dir: Path) -> None:
    shutil.rmtree(partial_dir, ignore_errors=True)


def truncate_files(partial_dir: Path, state: dict) -> None:
    """Срезать файлы по сохранённым офсетам: после последнего чекпоинта мог
    остаться недописанный хвост (полстраницы, оборванная строка JSON)."""
    for name, info in (state.get("files") or {}).items():
        offset = int((info or {}).get("offset") or 0)
        path = partial_dir / name
        if offset <= 0 or not path.exists():
            continue
        with open(path, "r+b") as f:
            if f.seek(0, os.SEEK_END) > offset:
                f.truncate(offset)


class FetchState:
    """Обёртка над state-словарём: файловые счётчики + атомарная запись.

    data["files"] — {имя файла: {offset, count}}; offset — байты записанного,
    count — единицы фазы (строки задач / пачки changelog / задачи догрузки).
    """

    def __init__(self, partial_dir: Path, data: dict):
        self.dir = partial_dir
        self.data = data

    def file(self, name: str) -> dict:
        return self.data.setdefault("files", {}).setdefault(name, {"offset": 0, "count": 0})

    def counts(self) -> dict:
        return self.data.setdefault("counts", {})

    def stage(self, name: str, extra_sec: float) -> None:
        st = self.data.setdefault("stages_sec", {})
        st[name] = round(st.get(name, 0.0) + extra_sec, 1)

    def save(self) -> None:
        """Атомарная запись: tmp + rename, чтобы падение не оставило битый state."""
        self.data["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        tmp = self.dir / (STATE_NAME + ".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.dir / STATE_NAME)
