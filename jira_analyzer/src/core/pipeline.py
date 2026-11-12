"""Оркестратор: связывает fetch и анализ в один прогон.

Здесь не должно быть логики — только последовательность вызовов модулей
и передача параметров. Общий клиент и для CLI (main.py), и для web-бэкенда.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

from src.components.base import MetricOpts
from src.components.blocks import MetricResult, sanitize_result
from src.components.params import params_label, resolve_params
from src.components.registry import METRICS, ensure_discovered
from src.core.config import PROJECT_ROOT

log = logging.getLogger(__name__)


@dataclass
class PipelineOptions:
    project: str
    jira_type: str = "apache"
    max_issues: int | None = None
    skip_changelog: bool = False
    with_comments: bool | None = None
    date_from: str | None = None
    date_to: str | None = None
    # Экземпляры метрик: {"metric_id": str, "params": dict} (или просто "id").
    # None/пусто = все зарегистрированные с дефолтными параметрами.
    metrics: list | None = None
    skip_fetch: bool = False      # анализ по уже имеющейся выгрузке
    fetch_only: bool = False      # задание «только выгрузка» (web: кнопка «Скачать»)
    resume: bool = False          # продолжить незавершённую выгрузку (_partial)
    fresh: bool = False           # стереть незавершённый прогресс и начать заново
    # Управление из web-задания: событие «Стоп» (fetch проверяет между
    # страницами/пачками) и колбэк progress(phase, done, total) для UI.
    stop_event: object | None = None
    progress: object | None = None
    raw_dir: Path | None = None
    out_dir: Path | None = None


def run_fetch(opts: PipelineOptions) -> dict:
    """Только выгрузка задач проекта в data/raw/<project> (без анализа).
    Отдельный шаг конвейера и отдельный тип web-задания: скачал один раз —
    анализируй сколько угодно через skip_fetch."""
    from src.fetch.run import fetch_project
    raw_dir = fetch_project(
        opts.project, opts.jira_type,
        max_issues=opts.max_issues,
        skip_changelog=opts.skip_changelog,
        with_comments=opts.with_comments,
        date_from=opts.date_from, date_to=opts.date_to,
        resume=opts.resume, fresh=opts.fresh,
        stop_event=opts.stop_event, progress=opts.progress,
    )
    return {"project": opts.project, "raw_dir": raw_dir}


def _instances(metrics: list | None) -> list[dict]:
    """Нормализация экземпляров: строка -> {metric_id, params: {}}.
    Одна метрика может встречаться несколько раз с разными params."""
    out = []
    for m in metrics or []:
        if isinstance(m, str):
            out.append({"metric_id": m, "params": {}})
        elif isinstance(m, dict):
            mid = str(m.get("metric_id") or m.get("id") or "").strip()
            out.append({"metric_id": mid, "params": dict(m.get("params") or {})})
        else:
            raise ValueError(f"Некорректный экземпляр метрики: {m!r}")
    return out


def run_pipeline(opts: PipelineOptions) -> dict:
    """Выгрузка (если не отключена) + анализ. Возвращает пути результатов
    и сами результаты: results (MetricResult) + errors (упавшие метрики)."""
    result: dict = {"project": opts.project}

    if not opts.skip_fetch:
        result.update(run_fetch(opts))
    raw_dir = opts.raw_dir or PROJECT_ROOT / "data" / "raw" / opts.project
    if not raw_dir.joinpath("issues.jsonl").exists():
        raise FileNotFoundError(f"Нет выгрузки: {raw_dir}/issues.jsonl — "
                                f"сначала запусти fetch (python main.py)")

    log.info("Читаю выгрузку из %s", raw_dir)
    from src.core.tables import load_tables
    tables = load_tables(raw_dir)
    log.info("Таблицы: задач=%d, событий changelog=%d, комментариев=%d",
             len(tables["issues"]), len(tables["events"]), len(tables["comments"]))

    ensure_discovered()
    selected = _instances(opts.metrics)
    if not selected:
        selected = [{"metric_id": mid, "params": {}} for mid in METRICS]
    unknown = [i["metric_id"] for i in selected if i["metric_id"] not in METRICS]
    if unknown:
        raise ValueError(f"Неизвестные метрики: {unknown}. "
                         f"Доступны: {sorted(METRICS)} (список: python main.py --list-metrics)")

    results: list[MetricResult] = []
    errors: list[dict] = []
    for inst in selected:   # порядок экземпляров = порядок в отчёте
        metric = METRICS[inst["metric_id"]]
        t0 = time.monotonic()
        try:
            values = resolve_params(metric.params, inst["params"])
            plabel = params_label(metric.params, values)
            blocks = metric.build(tables, MetricOpts(params=values))
            results.append(sanitize_result(MetricResult(
                metric_id=metric.id, label=metric.label or metric.id,
                description=metric.description, params_label=plabel, blocks=blocks)))
            log.info("  %-20s готово за %.2fс", metric.id, time.monotonic() - t0)
        except Exception as exc:  # одна упавшая метрика не роняет задание
            log.exception("Метрика %s не построилась", metric.id)
            errors.append({"metric_id": metric.id,
                           "error": f"{type(exc).__name__}: {exc}",
                           "params_label": params_label(metric.params,
                                                        inst["params"])})

    from src.components.reports import build_excel, build_html
    out_dir = opts.out_dir or PROJECT_ROOT / "data" / "reports" / opts.project
    excel_path, html_path = out_dir / "report.xlsx", out_dir / "report.html"
    build_excel(results, excel_path)
    build_html(opts.project, results, errors, html_path)

    log.info("=== Анализ готов ===")
    log.info("Отчёты: %s, %s", html_path, excel_path)
    result.update(report_dir=str(out_dir), results=results, errors=errors,
                  report_html=html_path, report_xlsx=excel_path)
    return result
