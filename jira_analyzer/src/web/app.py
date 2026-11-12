"""FastAPI-бэкенд web-интерфейса jira_analyzer.

Запуск:  python -m src.web.app   → http://127.0.0.1:8765
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.components.registry import METRICS, ensure_discovered, load_errors
from src.components.reports import MAX_ROWS_HTML, payload
from src.core.config import PROJECT_ROOT, load_env
from src.core.pipeline import PipelineOptions
from src.fetch.issues import validate_dates
from .jobs import get_job, request_stop, snapshot, start_job

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
STATIC_PATH = Path(__file__).parent / "static"
INDEX_PATH = STATIC_PATH / "index.html"
RAW_ROOT = PROJECT_ROOT / "data" / "raw"

# ключ проекта = имя папки выгрузки: путь из него строится, вольности недопустимы
_PROJECT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# INFO-логи модулей fetch/analysis должны доходить до перехватчика заданий
# (по умолчанию root на WARNING — прогресс в вебе был бы пустым)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

app = FastAPI(title="jira_analyzer")
app.mount("/static", StaticFiles(directory=STATIC_PATH), name="static")


class MetricInstance(BaseModel):
    """Экземпляр метрики в запросе: id + значения параметров пользователя."""
    metric_id: str
    params: dict = {}


class FetchRequest(BaseModel):
    """Параметры задания «только выгрузка» (кнопка «Скачать задачи»).
    resume/fresh — как поступить с незавершённым прогрессом (_partial):
    UI спрашивает пользователя, поэтому один из вариантов приходит всегда,
    когда прогресс существует."""
    project: str
    jira_type: str = "apache"
    max_issues: int | None = None
    date_from: str | None = None
    date_to: str | None = None
    with_comments: bool = False
    resume: bool = False
    fresh: bool = False


class RunRequest(BaseModel):
    project: str                      # ключ проекта = имя папки в data/raw
    jira_type: str = "apache"
    max_issues: int | None = None
    date_from: str | None = None
    date_to: str | None = None
    with_comments: bool = False
    # Экземпляры выбранных метрик (порядок = порядок в отчёте); пусто = все
    # зарегистрированные с дефолтными параметрами.
    metrics: list[MetricInstance] | None = None
    skip_fetch: bool = False


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return INDEX_PATH.read_text(encoding="utf-8")


@app.get("/api/metrics")
def metrics_list() -> dict:
    ensure_discovered()
    return {
        "metrics": [{"id": m.id, "label": m.label, "description": m.description,
                     "params": [p.model_dump() for p in m.params]}
                    for m in METRICS.values()],
        "load_errors": load_errors(),
    }


@app.get("/api/downloads")
def downloads_list() -> dict:
    """Скачанные выгрузки (data/raw/<ключ>): метаданные из fetch_summary.json,
    который оставляет fetch. Папки без summary — только имя и наличие файлов.
    Папка с одним лишь незавершённым прогрессом (_partial) тоже показывается —
    из неё можно продолжить выгрузку."""
    out = []
    if RAW_ROOT.is_dir():
        for d in sorted(RAW_ROOT.iterdir()):
            if not d.is_dir():
                continue
            partial_state = d / "_partial" / "state.json"
            has_dump = (d / "issues.jsonl").exists()
            if not has_dump and not partial_state.exists():
                continue
            item = {"folder": d.name, "jira_type": None, "jira_url": None,
                    "fetched_at": None, "issues": None, "changelog_events": None,
                    "with_comments": None,
                    "has_changelog": (d / "changelog.jsonl").exists(),
                    "partial": _partial_info(partial_state)}
            summary = d / "fetch_summary.json"
            if summary.exists():
                try:
                    s = json.loads(summary.read_text(encoding="utf-8"))
                    counts = s.get("counts") or {}
                    item.update(jira_type=s.get("jira_type"),
                                jira_url=s.get("jira_url"),
                                fetched_at=s.get("fetched_at"),
                                issues=counts.get("issues"),
                                changelog_events=counts.get("changelog_events"),
                                with_comments=s.get("with_comments"))
                except Exception:   # битый summary не должен прятать саму папку
                    pass
            out.append(item)
    out.sort(key=lambda x: x.get("fetched_at") or "", reverse=True)
    return {"downloads": out}


def _partial_info(state_path: Path) -> dict | None:
    """Незавершённая выгрузка (_partial/state.json) для баннера «Продолжить».
    None = прогресса нет. Битый state показываем как «неизвестная фаза» —
    всё равно предложить только «Начать заново»."""
    if not state_path.exists():
        return None
    try:
        s = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return {"phase": "unknown"}
    fp = s.get("fingerprint") or {}
    files = {name: (info or {}).get("count")
             for name, info in (s.get("files") or {}).items()}
    return {
        "phase": s.get("phase"),
        "files": files,
        "totals": s.get("totals") or {},
        "updated_at": s.get("updated_at"),
        "params": {k: fp.get(k) for k in
                   ("jira_type", "date_from", "date_to", "max_issues", "with_comments")},
    }


@app.post("/api/fetch")
def fetch(req: FetchRequest) -> dict:
    """Запустить задание «только выгрузка»: задачи проекта -> data/raw/<ключ>.
    Незавершённый прогресс (_partial) требует явного выбора: resume или fresh."""
    project = _check_project(req.project.strip())
    try:
        validate_dates(req.date_from, req.date_to)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    partial_state = RAW_ROOT / project / "_partial" / "state.json"
    if partial_state.exists() and not req.resume and not req.fresh:
        raise HTTPException(409, "У проекта есть незавершённая выгрузка: выбери "
                                 "«Продолжить» или «Начать заново»")
    if req.resume and not partial_state.exists():
        raise HTTPException(400, "Незавершённой выгрузки нет — запускай без «Продолжить»")
    opts = PipelineOptions(
        project=project, jira_type=req.jira_type,
        max_issues=req.max_issues, with_comments=req.with_comments,
        date_from=req.date_from or None, date_to=req.date_to or None,
        resume=req.resume, fresh=req.fresh,
        fetch_only=True,
    )
    return {"job_id": start_job(opts)}


@app.post("/api/run")
def run(req: RunRequest) -> dict:
    if not req.skip_fetch:
        try:
            validate_dates(req.date_from, req.date_to)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    opts = PipelineOptions(
        project=_check_project(req.project.strip()), jira_type=req.jira_type,
        max_issues=req.max_issues, with_comments=req.with_comments,
        date_from=req.date_from or None, date_to=req.date_to or None,
        metrics=[i.model_dump() for i in req.metrics] if req.metrics else None,
        skip_fetch=req.skip_fetch,
    )
    return {"job_id": start_job(opts)}


@app.post("/api/job/{job_id}/stop")
def job_stop(job_id: str) -> dict:
    """«Стоп» для идущей выгрузки: fetch сохранит чекпоинт и завершится
    со статусом stopped, прогресс останется в _partial."""
    if not request_stop(job_id):
        raise HTTPException(409, "Остановить можно только идущую выгрузку")
    return {"stopped": job_id}


def _check_project(project: str) -> str:
    if not project:
        raise HTTPException(400, "Укажи ключ проекта")
    if not _PROJECT_RE.fullmatch(project):
        raise HTTPException(400, "Ключ проекта: латиница/цифры и символы . _ - "
                                 "(это имя папки выгрузки)")
    return project


@app.get("/api/job/{job_id}")
def job_status(job_id: str) -> dict:
    job = get_job(job_id)
    if job is None:
        raise HTTPException(404, "Задание не найдено")
    return snapshot(job)


@app.get("/api/job/{job_id}/blocks")
def job_blocks(job_id: str) -> dict:
    """Блоки посчитанных метрик для рендеринга в интерфейсе."""
    job = get_job(job_id)
    if job is None or job.status != "done":
        raise HTTPException(404, "Результаты ещё не готовы")
    results = job.result.get("results") or []
    errors = job.result.get("errors") or []
    return payload(job.opts.project, results, errors, max_rows=MAX_ROWS_HTML)


@app.get("/api/job/{job_id}/report")
def job_report(job_id: str, format: str = "html") -> FileResponse:
    job = get_job(job_id)
    if job is None or job.status != "done":
        raise HTTPException(404, "Отчёт ещё не готов")
    name = "report.xlsx" if format == "xlsx" else "report.html"
    path = Path(job.result["report_dir"]) / name
    if not path.exists():
        raise HTTPException(404, f"Файл не найден: {name}")
    return FileResponse(path, filename=f"{job.opts.project}_{name}",
                        media_type=XLSX_MIME if format == "xlsx" else "text/html")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    import uvicorn
    # порт из .env (WEB_PORT=8765), по умолчанию 8765
    port = int(load_env().get("WEB_PORT") or 8765)
    print(f"Web-интерфейс: http://127.0.0.1:{port}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
