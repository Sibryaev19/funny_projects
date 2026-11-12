"""CLI jira_analyzer: выгрузка + анализ одним прогоном.

Примеры (из корня проекта):
    python main.py --project CURATOR --jira-type apache
    python main.py --project CURATOR --jira-type apache --max-issues 50
    python main.py --project PROJ --jira-type jira --date-from 2024-01-01
    python main.py --project PROJ --jira-type jira --skip-fetch
    python main.py --metrics 'stale_issues(stale_days=45),overview' --skip-fetch \
        --project CURATOR                      # метрики с параметрами
    python main.py --list-metrics                 # какие метрики зарегистрированы
    python main.py --check-metrics                # тест-харнесс метрик (синтетика)
"""
from __future__ import annotations

import argparse
import logging
import sys

from src.core.pipeline import PipelineOptions, run_pipeline


def _split_top_level(s: str) -> list[str]:
    """Разбить строку по запятым верхнего уровня (запятые внутри скобок не делят)."""
    parts, buf, depth = [], [], 0
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                raise ValueError("лишняя закрывающая скобка «)»")
        if ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if depth != 0:
        raise ValueError("не закрыта скобка «(»")
    parts.append("".join(buf))
    return parts


def parse_metrics_spec(spec: str) -> list[dict]:
    """'stale_issues(stale_days=45),overview' -> экземпляры
    [{'metric_id': ..., 'params': {...}}, ...]. Значения остаются строками —
    типы по декларации приводит resolve_params() в конвейере. Одна метрика
    может встречаться несколько раз (порядок = порядок в отчёте)."""
    out = []
    for chunk in _split_top_level(spec):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "(" not in chunk:
            out.append({"metric_id": chunk, "params": {}})
            continue
        name, rest = chunk.split("(", 1)
        if not rest.endswith(")"):
            raise ValueError(f"не закрыта скобка в «{chunk}»")
        params = {}
        for kv in _split_top_level(rest[:-1]):
            kv = kv.strip()
            if not kv:
                continue
            if "=" not in kv:
                raise ValueError(f"параметр «{kv}» без «=» (метрика {name.strip()})")
            key, val = kv.split("=", 1)
            params[key.strip()] = val.strip()
        out.append({"metric_id": name.strip(), "params": params})
    return out


def main() -> None:
    p = argparse.ArgumentParser(description="jira_analyzer: выгрузка Jira -> метрики -> отчёт")
    p.add_argument("--project", default=None, help="ключ проекта Jira, напр. CURATOR")
    p.add_argument("--jira-type", default="apache",
                   help="префикс секции .env: jira / jira_delta / apache (по умолчанию apache)")
    p.add_argument("--skip-fetch", action="store_true",
                   help="не выгружать, анализировать имеющуюся выгрузку data/raw/<project>")
    p.add_argument("--resume", action="store_true",
                   help="продолжить незавершённую выгрузку с чекпоинта (_partial)")
    p.add_argument("--fresh", action="store_true",
                   help="стереть незавершённый прогресс выгрузки и начать заново")
    p.add_argument("--max-issues", type=int, default=None, help="обрезать выгрузку (тест)")
    p.add_argument("--skip-changelog", action="store_true", help="без истории изменений")
    p.add_argument("--with-comments", action="store_true", help="тянуть комментарии")
    p.add_argument("--date-from", default=None, help="YYYY-MM-DD, только задачи с этой даты")
    p.add_argument("--date-to", default=None, help="YYYY-MM-DD, только до этой даты")
    p.add_argument("--metrics", default=None,
                   help="экземпляры метрик через запятую, параметры в скобках: "
                        "'stale_issues(stale_days=45),overview' "
                        "(по умолчанию все с дефолтными параметрами)")
    p.add_argument("--list-metrics", action="store_true",
                   help="показать зарегистрированные метрики и выйти")
    p.add_argument("--check-metrics", action="store_true",
                   help="тест-харнесс метрик (валидация блоков) и выйти")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%H:%M:%S")

    if args.list_metrics or args.check_metrics:
        from src.components.registry import METRICS, ensure_discovered, load_errors
        ensure_discovered()
        if args.list_metrics:
            print(f"Зарегистрировано метрик: {len(METRICS)}")
            for m in METRICS.values():
                line = f"  {m.id:22s} {m.label}"
                if m.params:
                    decl = ", ".join(f"{q.name}:{q.type}" +
                                     (f"={q.default!r}" if q.default is not None else "")
                                     for q in m.params)
                    line += f"   ({decl})"
                print(line)
            for f, e in load_errors().items():
                print(f"  ОШИБКА ЗАГРУЗКИ {f}: {e}")
            return
        from src.components.check import run_check
        sys.exit(run_check(args.project))

    if not args.project:
        p.error("--project обязателен для прогона (для служебных команд: --list-metrics, --check-metrics)")

    try:
        instances = parse_metrics_spec(args.metrics) if args.metrics else None
    except ValueError as exc:
        p.error(f"--metrics: {exc}")

    result = run_pipeline(PipelineOptions(
        project=args.project, jira_type=args.jira_type,
        max_issues=args.max_issues, skip_changelog=args.skip_changelog,
        with_comments=args.with_comments,
        date_from=args.date_from, date_to=args.date_to,
        metrics=instances, skip_fetch=args.skip_fetch,
        resume=args.resume, fresh=args.fresh,
    ))
    print()
    print("Отчёты:")
    print("  HTML:", result["report_html"])
    print("  Excel:", result["report_xlsx"])
    if result.get("errors"):
        print("Метрики с ошибками (см. лог):",
              ", ".join(e["metric_id"] for e in result["errors"]))


if __name__ == "__main__":
    main()
