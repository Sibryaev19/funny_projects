"""Отчёты из результатов метрик.

Excel — лист на каждый табличный блок (лимит 5000 строк, как раньше).
HTML — самодостаточный файл: JSON-данные + инлайн echarts.min.js + blocks.js.
Открывается офлайн двойным кликом, графики живые (тот же рендер, что в web).
"""
from __future__ import annotations

import html
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .blocks import MetricResult, TableBlock

log = logging.getLogger(__name__)

MAX_ROWS_SHEET = 5000   # лимит строк на лист Excel
MAX_ROWS_HTML = 200     # лимит строк таблицы в HTML-payload (полные — в Excel)

_STATIC = Path(__file__).resolve().parents[1] / "web" / "static"

_CSS = """
body { font-family: 'Segoe UI', Arial, sans-serif; margin: 24px auto; max-width: 1100px;
       color: #1a1a2e; background: #fafafa; padding: 0 16px; }
h1 { border-bottom: 3px solid #4c81c9; padding-bottom: 8px; }
.meta { color: #666; font-size: 0.9em; }
"""


def payload(project: str, results: list[MetricResult],
            errors: list[dict] | None = None, *,
            max_rows: int = MAX_ROWS_HTML) -> dict:
    """Результаты -> JSON-словарь для web и HTML-отчёта: длинные таблицы
    обрезаются с пометкой (полные данные — в Excel)."""
    out = {"project": project,
           "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M") + " UTC",
           "results": [], "errors": list(errors or [])}
    for res in results:
        data = res.model_dump()
        for b in data["blocks"]:
            if b["type"] == "table" and len(b["rows"]) > max_rows:
                add = f"показаны первые {max_rows} из {len(b['rows'])} строк (полные — в Excel)"
                b["note"] = f"{b['note']}; {add}" if b["note"] else add
                b["rows"] = b["rows"][:max_rows]
        out["results"].append(data)
    return out


def build_excel(results: list[MetricResult], path: Path) -> None:
    """Лист на каждый табличный блок. Имя листа — метка метрики; если табличных
    блоков несколько, к имени добавляется номер. Если табличных блоков нет
    вовсе (метрики упали на параметрах или вернули только графики) — книга с
    одной пояснительной страницей, а не падение."""
    path.parent.mkdir(parents=True, exist_ok=True)
    used: set[str] = set()
    n_sheets = 0
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for res in results:
            for tno, b in enumerate((b for b in res.blocks
                                     if isinstance(b, TableBlock) and b.rows), start=1):
                base = (res.label if tno == 1 else f"{res.label} ({tno})")[:31]
                sheet = _sheet_name(base, used)
                used.add(sheet)
                n_sheets += 1
                df = pd.DataFrame(b.rows, columns=b.columns).head(MAX_ROWS_SHEET)
                df.to_excel(writer, sheet_name=sheet, index=False)
        if not n_sheets:
            pd.DataFrame({"примечание": ["в этом прогоне нет табличных блоков: "
                                         "метрики вернули только графики/списки или упали"]}
                         ).to_excel(writer, sheet_name="Отчёт", index=False)


def _sheet_name(base: str, used: set[str]) -> str:
    """Валидное уникальное имя листа Excel: <=31 символ, без : \\ / ? * [ ]."""
    for ch in ":\\/?*[]":
        base = base.replace(ch, " ")
    name, n = base.strip() or "метрика", 2
    while name in used:
        suffix = f" ({n})"
        name = base[:31 - len(suffix)] + suffix
        n += 1
    return name


def build_html(project: str, results: list[MetricResult],
               errors: list[dict] | None, path: Path) -> None:
    """Самодостаточный HTML: данные + echarts + blocks.js инлайном (офлайн-отчёт)."""
    vendor = (_STATIC / "vendor" / "echarts.min.js").read_text(encoding="utf-8")
    blocks_js = (_STATIC / "blocks.js").read_text(encoding="utf-8")
    data = json.dumps(payload(project, results, errors),
                      ensure_ascii=False).replace("</", "<\\/")
    parts = [
        "<!doctype html><html lang=ru><head><meta charset=utf-8>",
        f"<title>Отчёт: {html.escape(project)}</title><style>{_CSS}</style></head><body>",
        f"<h1>Анализ Jira-проекта {html.escape(project)}</h1>",
        f"<p class=meta>Сформирован {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC · "
        f"метрик: {len(results)} · ошибок: {len(errors or [])}</p>",
        "<div id=report-root></div>",
        "<script>window.REPORT_DATA = ", data, ";</script>",
        "<script>", vendor, "</script>",
        "<script>", blocks_js, "</script>",
        "<script>blocks.renderAll(document.getElementById('report-root'), "
        "window.REPORT_DATA);</script>",
        "</body></html>",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(parts), encoding="utf-8")
