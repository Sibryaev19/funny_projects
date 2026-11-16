"""Оркестрация выгрузки одного проекта + CLI.

Запуск (из корня проекта):
    python -m src.fetch.run --project CURATOR --jira-type apache
    python -m src.fetch.run --project CURATOR --jira-type apache --max-issues 50
    python -m src.fetch.run --project X --jira-type jira1 --with-comments
    python -m src.fetch.run --project X --resume   # продолжить после падения/стопа

Результаты в data/raw/<project>/:
    issues.jsonl      — задача в каждой строке (пишется потоково, по страницам)
    changelog.jsonl   — {"key", "histories", "truncated"}; урезанные догруженные
                        задачи дописаны ПОСЛЕ урезанных версий → при чтении в dict
                        по ключу последняя строка даёт полную историю
    comments.jsonl    — {"key", "comments", "truncated"} (только с --with-comments)
    tree.json         — дерево epic -> задача -> подзадача
    fetch_summary.json — метаданные прогона

Прогресс и возобновление: всё пишется в <папка>/_partial/ с чекпоинтами
state.json (см. state.py); корень папки не меняется до полного успеха.
Упавшая или остановленная («Стоп» в web, FetchStopped) выгрузка сохраняет
прогресс: --resume продолжает с чекпоинта, --fresh стирает и начинает заново.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from src.core.config import load_config
from src.core.tables import iter_records_at, last_line_offsets
from .changelog import endpoint_available, iter_changelog, iter_changelog_endpoint
from .client import JiraClient
from .comments import iter_comments
from .issues import iter_issues, validate_dates
from .state import FetchState, clear_partial, fingerprint, load_state, truncate_files
from .tree import build_tree

log = logging.getLogger("fetch")

PARTIAL_NAME = "_partial"


class FetchStopped(RuntimeError):
    """Выгрузка остановлена пользователем («Стоп»): скачанное сохранено в
    _partial/, продолжение возможно (web: «Продолжить», CLI: --resume)."""


def _chunks(keys: list[str], size: int):
    for i in range(0, len(keys), size):
        yield keys[i:i + size]


def _stopped(stop_event, what: str) -> None:
    if stop_event is not None and stop_event.is_set():
        raise FetchStopped(f"остановлено пользователем ({what}); "
                           f"прогресс сохранён — можно продолжить")


def _read_keys(path: Path) -> list[str]:
    """Ключи из issues.jsonl (для фаз changelog/комментариев)."""
    keys: list[str] = []
    for line in _read_lines(path):
        key = line.get("key")
        if key:
            keys.append(key)
    return keys


def _read_lines(path: Path):
    """JSONL построчно (бинарный режим = точные байтовые офсеты)."""
    with open(path, "rb") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def _read_jsonl(path: Path):
    yield from _read_lines(path)


class _Jsonl:
    """Файл выгрузки внутри _partial: бинарная дозапись + чекпоинт офсета.

    Бинарный режим обязателен: f.tell() даёт точные байты, truncate_files
    срезает недописанный хвост ровно по границе последней полной строки.
    """

    def __init__(self, path: Path, state: FetchState, name: str, append: bool):
        self.path = path
        self.info = state.file(name)
        self.f = open(path, "ab" if append else "wb")

    def write_record(self, obj) -> None:
        self.f.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))

    def checkpoint(self, count: int) -> None:
        self.f.flush()
        self.info["offset"] = self.f.tell()
        self.info["count"] = count

    def close(self) -> None:
        self.f.close()


PHASES = ("both", "issues", "changelog")


def _norm_phases(phases: str | None, skip_changelog: bool) -> str:
    """Фазы выгрузки. skip_changelog=True — устаревший алиас phases="issues"."""
    if phases is None or phases == "both":
        return "issues" if skip_changelog else "both"
    if phases not in PHASES:
        raise ValueError(f"phases: ожидалось одно из {PHASES}, получено {phases!r}")
    return phases


def fetch_project(
    project_key: str,
    jira_type: str,
    *,
    max_issues: int | None = None,
    phases: str = "both",
    skip_changelog: bool = False,   # устаревший алиас phases="issues"
    with_comments: bool | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    env_path: Path | None = None,
    resume: bool = False,
    fresh: bool = False,
    stop_event=None,
    progress=None,
) -> Path:
    """Выгрузка проекта с выбором фаз. Возвращает папку с результатами.

    phases="both" — задачи + история (как раньше); "issues" — только задачи
    (быстро, анализ доступен сразу); "changelog" — только история к УЖЕ
    скачанным задачам (issues.jsonl должен быть в папке; сам не трогается).
    Комментарии тянуться только вместе с задачами.

    resume=True — продолжить незавершённую (отпечаток параметров обязан
    совпасть); fresh=True — стереть прогресс и начать заново; без обоих флагов
    при наличии прогресса будет ошибка с подсказкой (выбор — за пользователем).
    stop_event — кнопка «Стоп» (проверяется между страницами и пачками);
    progress(phase, done, total) — прогресс для web-интерфейса.
    """
    phases = _norm_phases(phases, skip_changelog)
    validate_dates(date_from, date_to)
    cfg = load_config(jira_type, env_path=env_path)
    inst = cfg.jira[jira_type.lower()]
    fs = cfg.fetch
    if date_from:
        fs.date_from = date_from
    if date_to:
        fs.date_to = date_to
    pull_comments = fs.with_comments if with_comments is None else with_comments
    if phases == "changelog" and pull_comments:
        log.info("Комментарии тянуться только вместе с задачами — пропускаю")
        pull_comments = False

    out_dir = cfg.data_dir / "raw" / project_key
    if phases == "changelog" and not (out_dir / "issues.jsonl").exists():
        raise ValueError(
            f"В data/raw/{project_key} нет issues.jsonl — докачивать историю "
            f"некуда. Сначала выгрузи задачи (phases=\"issues\" или \"both\").")

    client = JiraClient(
        inst.url, pat=inst.pat, auth=inst.auth,
        delay=fs.delay, max_retries=fs.max_retries,
        timeout=fs.timeout, backoff_step=fs.backoff_step,
    )
    partial = out_dir / PARTIAL_NAME
    fp = fingerprint(project_key, jira_type, inst.url, date_from=fs.date_from,
                     date_to=fs.date_to, max_issues=max_issues,
                     phases=phases, with_comments=pull_comments,
                     fields=cfg.fields)

    prev = load_state(partial)
    if prev is not None and fresh:
        clear_partial(partial)
        prev = None
    if prev is not None and not resume and not fresh:
        raise ValueError(
            "Найдена незавершённая выгрузка: выбери «Продолжить» или "
            "«Начать заново» (CLI: --resume / --fresh)")
    if resume and prev is None:
        raise ValueError("Незавершённой выгрузки не найдено — запускай без --resume")
    if resume and prev.get("fingerprint") != fp:
        raise ValueError("Параметры выгрузки изменились с прошлой сессии "
                         "(даты/лимит/тип Jira/поля) — продолжить нельзя, "
                         "только начать заново (--fresh)")

    resumed = prev is not None
    out_dir.mkdir(parents=True, exist_ok=True)
    partial.mkdir(parents=True, exist_ok=True)
    if resumed:
        truncate_files(partial, prev)
        log.info("Возобновляю выгрузку с чекпоинта: фаза=%s, задач=%s",
                 prev.get("phase"),
                 (prev.get("files") or {}).get("issues.jsonl", {}).get("count"))
    else:
        prev = {
            "version": 1,
            "phase": "changelog" if phases == "changelog" else "issues",
            "fingerprint": fp,
            "files": {},
            "counts": {},
            "totals": {},
            "truncated": [],
            "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
    state = FetchState(partial, prev)

    log.info("=== Выгрузка %s с %s (%s, фазы=%s)%s ===",
             project_key, inst.url, inst.auth, phases,
             ", продолжение" if resumed else "")

    keys = _run_phases(state, client, out_dir, project_key, cfg, fs,
                       max_issues, phases, pull_comments,
                       stop_event, progress, resumed)

    # ---------- коммит: из _partial в корень папки ----------
    state.data["phase"] = "commit"
    state.save()
    t0 = time.monotonic()
    log.info("Собираю итоговые файлы (дедуп по ключу, последняя запись выигрывает)")
    if phases in ("both", "issues"):
        _commit_file(partial / "issues.jsonl", out_dir / "issues.jsonl")
    if phases in ("both", "changelog"):
        _commit_file(partial / "changelog.jsonl", out_dir / "changelog.jsonl")
    if pull_comments:
        _commit_file(partial / "comments.jsonl", out_dir / "comments.jsonl")

    tree = build_tree(_read_jsonl(out_dir / "issues.jsonl"), cfg.custom_fields)
    _dump(out_dir / "tree.json", tree)
    state.stage("commit", time.monotonic() - t0)

    counts = {"issues": len(keys), "tree_roots": len(tree["roots"])}
    expected = state.data["totals"].get("issues")
    if expected is not None and expected != len(keys):
        log.warning("Скачано задач: %d, сервер в total сообщил: %s", len(keys), expected)
    counts["issues_expected"] = expected
    if phases in ("both", "changelog") and keys:
        counts["changelog_events"] = state.counts().get("changelog_events", 0)
        counts["changelog_truncated"] = len(state.data.get("truncated", []))
    if pull_comments and keys:
        counts["comments"] = state.counts().get("comments", 0)

    summary = {
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "jira_url": inst.url,
        "jira_type": jira_type,
        "project": project_key,
        "phases": phases,
        "date_from": fs.date_from,
        "date_to": fs.date_to,
        "with_comments": pull_comments,
        "resumed_from_checkpoint": resumed,
        "stages": dict(state.data.get("stages_sec", {})),
        "counts": counts,
    }

    _dump(out_dir / "fetch_summary.json", summary)
    clear_partial(partial)   # только после успешного коммита

    log.info("=== Готово за %.1fс: задачи=%d, события changelog=%d%s ===",
             sum(state.data.get("stages_sec", {}).values()), len(keys),
             counts.get("changelog_events", 0),
             f", комментарии={counts.get('comments', 0)}" if pull_comments else "")
    log.info("Результаты: %s", out_dir)
    return out_dir


def _run_phases(state, client, out_dir, project_key, cfg, fs,
                max_issues, phases, pull_comments,
                stop_event, progress, resumed) -> list[str]:
    """Фазы выгрузки: задачи -> changelog -> догрузка -> комментарии.
    Возвращает полный список ключей (нужен коммиту). Каждая фаза начинается,
    только если чекпоинт ещё не за неё и входит в выбранные фазы (phases)."""

    partial = state.dir
    keys: list[str] = []
    issues_path = partial / "issues.jsonl"

    # ---------- фаза: задачи ----------
    if phases == "changelog":
        # история качается к УЖЕ скачанным задачам: ключи из корня папки
        keys = _read_keys(out_dir / "issues.jsonl")
    elif state.data["phase"] == "issues":
        f_i = state.file("issues.jsonl")
        seen: set[str] = set()
        start_at = 0
        append = bool(f_i["offset"])
        if append:
            keys = _read_keys(issues_path)
            seen.update(keys)
            start_at = f_i["count"]
        total_hint = {"total": None}

        def on_total(total):
            total_hint["total"] = total
            state.data["totals"]["issues"] = total

        t0 = time.monotonic()
        js = _Jsonl(issues_path, state, "issues.jsonl", append)
        try:
            n = len(keys)
            for issue in iter_issues(client, project_key, cfg.fields,
                                     page_size=fs.page_size, max_issues=max_issues,
                                     date_from=fs.date_from, date_to=fs.date_to,
                                     start_at=start_at, seen=seen,
                                     on_total=on_total):
                _stopped(stop_event, f"задачи {n}")
                js.write_record(issue)
                keys.append(issue["key"])
                n = len(keys)
                if progress:
                    progress("issues", n, total_hint["total"])
                if n % fs.page_size == 0:
                    js.checkpoint(n)
                    state.stage("issues", time.monotonic() - t0)
                    t0 = time.monotonic()
                    state.save()
            js.checkpoint(n)
            state.stage("issues", time.monotonic() - t0)
            state.data["phase"] = "changelog"
            state.save()
        finally:
            js.close()
    else:
        keys = _read_keys(issues_path)

    events = state.counts().get("changelog_events", 0)
    truncated: list[str] = state.data.get("truncated", [])

    # ---------- фаза: changelog ----------
    if state.data["phase"] == "changelog" and phases in ("both", "changelog") and keys:
        f_c = state.file("changelog.jsonl")
        cl_path = partial / "changelog.jsonl"
        done = f_c["count"] if resumed else 0
        total_chunks = (len(keys) + fs.expand_batch - 1) // fs.expand_batch
        t0 = time.monotonic()
        js = _Jsonl(cl_path, state, "changelog.jsonl", bool(f_c["offset"]))
        try:
            for ci, chunk in enumerate(_chunks(keys, fs.expand_batch)):
                if ci < done:
                    continue
                _stopped(stop_event, f"changelog {ci + 1}/{total_chunks}")
                for rec in iter_changelog(client, chunk, expand_batch=fs.expand_batch):
                    js.write_record(rec)
                    events += len(rec.get("histories") or [])
                    if rec.get("truncated"):
                        truncated.append(rec["key"])
                js.checkpoint(ci + 1)
                state.counts()["changelog_events"] = events
                state.data["truncated"] = truncated
                state.stage("changelog", time.monotonic() - t0)
                t0 = time.monotonic()
                if progress:
                    progress("changelog", min((ci + 1) * fs.expand_batch, len(keys)),
                             len(keys))
                state.save()
        finally:
            js.close()
        state.data["phase"] = "doload"
        state.save()

    # ---------- фаза: догрузка полных историй ----------
    if state.data["phase"] == "doload" and phases in ("both", "changelog") and truncated:
        f_c = state.file("changelog.jsonl")
        cl_path = partial / "changelog.jsonl"
        done = state.counts().get("doload", 0)
        if done == 0 and not endpoint_available(client, truncated[0]):
            log.warning("Эндпоинт /changelog недоступен: %d задач остались с 100 "
                        "последними событиями: %s", len(truncated), truncated[:5])
            state.data["truncated"] = []
            state.data["phase"] = "comments"
            state.save()
        else:
            t0 = time.monotonic()
            js = _Jsonl(cl_path, state, "changelog.jsonl", bool(f_c["offset"]))
            try:
                for idx, key in enumerate(truncated):
                    if idx < done:
                        continue
                    _stopped(stop_event, f"догрузка {idx + 1}/{len(truncated)}")
                    full = next(iter_changelog_endpoint(client, [key]), None)
                    if full:
                        js.write_record(full)
                    js.checkpoint(idx + 1)   # count = позиция в списке догрузки
                    state.counts()["doload"] = idx + 1
                    state.stage("doload", time.monotonic() - t0)
                    t0 = time.monotonic()
                    if progress:
                        progress("doload", idx + 1, len(truncated))
                    state.save()
            finally:
                js.close()
        state.data["phase"] = "comments"
        state.save()

    # ---------- фаза: комментарии ----------
    if state.data["phase"] == "comments" and pull_comments and keys:
        f_m = state.file("comments.jsonl")
        cm_path = partial / "comments.jsonl"
        done = f_m["count"] if resumed else 0
        total_chunks = (len(keys) + fs.expand_batch - 1) // fs.expand_batch
        n_comments = state.counts().get("comments", 0)
        t0 = time.monotonic()
        js = _Jsonl(cm_path, state, "comments.jsonl", bool(f_m["offset"]))
        try:
            for ci, chunk in enumerate(_chunks(keys, fs.expand_batch)):
                if ci < done:
                    continue
                _stopped(stop_event, f"комментарии {ci + 1}/{total_chunks}")
                for rec in iter_comments(client, chunk, batch_size=fs.expand_batch):
                    js.write_record(rec)
                    n_comments += len(rec.get("comments") or [])
                js.checkpoint(ci + 1)
                state.counts()["comments"] = n_comments
                state.stage("comments", time.monotonic() - t0)
                t0 = time.monotonic()
                if progress:
                    progress("comments", min((ci + 1) * fs.expand_batch, len(keys)),
                             len(keys))
                state.save()
        finally:
            js.close()

    return keys


def _commit_file(src: Path, dst: Path) -> None:
    """Переезд JSONL из _partial в корень папки с дедупом по ключу: последняя
    запись по ключу выигрывает (лечит сдвиги пагинации между сессиями резюме
    и догруженные полные истории после урезанных)."""
    if not src.exists():
        return
    offsets = last_line_offsets(src)
    with open(dst, "wb") as out:
        for rec in iter_records_at(src, offsets):
            out.write((json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8"))


def _dump(path: Path, obj) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


def main() -> None:
    parser = argparse.ArgumentParser(description="Выгрузка задач Jira-проекта в data/raw/")
    parser.add_argument("--project", required=True, help="ключ проекта, напр. CURATOR")
    parser.add_argument("--jira-type", default="apache",
                        help="ключ секции .env (jira1/jira2/apache), по умолчанию apache")
    parser.add_argument("--max-issues", type=int, default=None,
                        help="обрезать выгрузку (смоук-тест)")
    parser.add_argument("--phases", choices=PHASES, default="both",
                        help="что качать: both (задачи+история), issues (только "
                             "задачи), changelog (только история к скачанным)")
    parser.add_argument("--skip-changelog", action="store_true",
                        help="(устарело) алиас --phases issues")
    parser.add_argument("--with-comments", action="store_true",
                        help="тянуть комментарии (по умолчанию выключены)")
    parser.add_argument("--date-from", default=None, help="YYYY-MM-DD, включительно")
    parser.add_argument("--date-to", default=None, help="YYYY-MM-DD, включительно")
    parser.add_argument("--resume", action="store_true",
                        help="продолжить незавершённую выгрузку с чекпоинта")
    parser.add_argument("--fresh", action="store_true",
                        help="стереть незавершённый прогресс и начать заново")
    parser.add_argument("--env", default=None, help="путь к .env (по умолчанию <проект>/.env)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%H:%M:%S")
    try:
        fetch_project(
            args.project, args.jira_type,
            max_issues=args.max_issues,
            phases=args.phases,
            skip_changelog=args.skip_changelog,
            with_comments=args.with_comments,
            date_from=args.date_from, date_to=args.date_to,
            resume=args.resume, fresh=args.fresh,
            env_path=Path(args.env) if args.env else None,
        )
    except FetchStopped as exc:
        log.warning("Выгрузка остановлена: %s", exc)
        sys.exit(130)


if __name__ == "__main__":
    main()
