"""Фоновые задания для web-интерфейса: состояние в памяти процесса.

Прогресс — из логов и от fetch-фаз: обработчик перехватывает INFO-записи
ТОЛЬКО своего потока (параллельные задания не смешивают логи), fetch докладывает
(фаза, сколько скачано, total) — фронтенд опрашивает /api/job/{id}.
«Стоп» (request_stop) работает для выгрузок: fetch завершает текущую
страницу/пачку, сохраняет чекпоинт и выходит со статусом stopped.
"""
from __future__ import annotations

import logging
import threading
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime

from src.core.pipeline import PipelineOptions, run_fetch, run_pipeline
from src.fetch.run import FetchStopped

# Готовые задания держим в памяти ограниченно: самые свежие.
MAX_FINISHED_JOBS = 50

PHASE_LABELS = {
    "issues": "задачи",
    "changelog": "история изменений",
    "doload": "догрузка полных историй",
    "comments": "комментарии",
}


class _JobLogHandler(logging.Handler):
    def __init__(self, sink: deque, thread_name: str):
        super().__init__(logging.INFO)
        self.sink = sink
        self.thread_name = thread_name

    def emit(self, record: logging.LogRecord) -> None:
        if record.threadName != self.thread_name:
            return  # чужие задания пишут в свои буферы, а не в этот
        try:
            self.sink.append(f"{datetime.now():%H:%M:%S} {record.getMessage()}")
        except Exception:
            pass


@dataclass
class Job:
    id: str
    opts: PipelineOptions
    status: str = "running"  # running | done | error | stopped
    started: str = field(default_factory=lambda: datetime.now().strftime("%H:%M:%S"))
    error: str | None = None
    result: dict = field(default_factory=dict)
    log: deque = field(default_factory=lambda: deque(maxlen=500))
    stop_event: threading.Event = field(default_factory=threading.Event)
    # {"phase", "done", "total"} — обновляет fetch через колбэк opts.progress
    progress: dict | None = None


JOBS: dict[str, Job] = {}
_LOCK = threading.Lock()


def start_job(opts: PipelineOptions) -> str:
    _prune_jobs()
    job = Job(id=uuid.uuid4().hex[:12], opts=opts)
    thread_name = f"job-{job.id}"
    opts.progress = _progress_setter(job)      # fetch докладывает прогресс в задание
    opts.stop_event = job.stop_event           # «Стоп» из web: fetch проверяет между страницами/пачками

    def target() -> None:
        root = logging.getLogger()
        handler = _JobLogHandler(job.log, thread_name)
        root.addHandler(handler)
        try:
            job.result = run_fetch(opts) if opts.fetch_only else run_pipeline(opts)
            job.log.append("=== ЗАВЕРШЕНО ===")
            job.status = "done"
        except FetchStopped as exc:
            job.error = str(exc)
            job.log.append(f"=== ОСТАНОВЛЕНО: {exc} ===")
            job.status = "stopped"
        except Exception as exc:  # показываем ошибку пользователю, не роняем сервер
            job.error = str(exc)
            job.log.append(f"=== ОШИБКА: {exc} ===")
            job.status = "error"
        finally:
            root.removeHandler(handler)

    threading.Thread(target=target, daemon=True, name=thread_name).start()
    with _LOCK:
        JOBS[job.id] = job
    return job.id


def _prune_jobs() -> None:
    """Не дать JOBS расти бесконечно: готовые задания храним последние N."""
    with _LOCK:
        finished = [j for j in JOBS.values() if j.status != "running"]
        excess = len(finished) - MAX_FINISHED_JOBS
        for job in finished[:max(0, excess)]:   # dict хранит порядок вставки
            JOBS.pop(job.id, None)


def get_job(job_id: str) -> Job | None:
    return JOBS.get(job_id)


def request_stop(job_id: str) -> bool:
    """Кнопка «Стоп»: True, если задание выгрузки и оно ещё идёт."""
    job = JOBS.get(job_id)
    if job is None or job.status != "running" or not job.opts.fetch_only:
        return False
    job.stop_event.set()
    return True


def _progress_setter(job: Job):
    def set_progress(phase: str, done: int, total: int | None) -> None:
        job.progress = {"phase": PHASE_LABELS.get(phase, phase),
                        "done": done, "total": total}
    return set_progress


def snapshot(job: Job) -> dict:
    opts = job.opts
    return {
        "id": job.id,
        "status": job.status,
        "started": job.started,
        "error": job.error,
        "log": list(job.log),
        "progress": job.progress,
        "stoppable": job.status == "running" and opts.fetch_only,
        "fetch_only": opts.fetch_only,
        "report_ready": job.status == "done" and bool(job.result.get("report_dir")),
        "project": opts.project,
    }
