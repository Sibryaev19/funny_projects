"""Отчёты: обрезка таблиц в payload, имена листов Excel, пояснительный лист."""
from __future__ import annotations

import openpyxl

from src.components.blocks import MetricResult, TableBlock
from src.components.reports import MAX_ROWS_HTML, MAX_ROWS_SHEET, build_excel, payload


def table_result(label: str, rows: int) -> MetricResult:
    return MetricResult(
        metric_id="t", label=label, description="",
        blocks=[TableBlock(title="таблица", columns=["a", "b"],
                           rows=[[i, i * 2] for i in range(rows)])])


def test_payload_truncates_long_tables_with_note():
    res = table_result("Метрика", 250)
    data = payload("PROJ", [res], [])
    block = data["results"][0]["blocks"][0]
    assert len(block["rows"]) == MAX_ROWS_HTML
    assert "полные — в Excel" in block["note"]


def test_payload_keeps_short_tables():
    res = table_result("Метрика", 5)
    data = payload("PROJ", [res], [])
    assert len(data["results"][0]["blocks"][0]["rows"]) == 5


def test_excel_sheet_names_unique_and_valid(tmp_path):
    results = [table_result("Одна и та же метрика", 3),
               table_result("Одна и та же метрика", 4),
               table_result("Очень длинное название метрики " * 2, 5)]
    path = tmp_path / "report.xlsx"
    build_excel(results, path)
    wb = openpyxl.load_workbook(path)
    names = wb.sheetnames
    assert len(names) == 3
    assert len(set(names)) == 3                      # уникальны
    assert all(len(n) <= 31 for n in names)          # лимит Excel
    assert all(not set(n) & set(":\\/?*[]") for n in names)


def test_excel_sheet_row_limit(tmp_path):
    res = table_result("Метрика", MAX_ROWS_SHEET + 500)
    path = tmp_path / "report.xlsx"
    build_excel([res], path)
    wb = openpyxl.load_workbook(path)
    ws = wb[wb.sheetnames[0]]
    assert ws.max_row == MAX_ROWS_SHEET + 1   # 5000 строк данных + строка заголовков


def test_excel_without_table_blocks_explains(tmp_path):
    """Метрики вернули только графики (или упали) — книга с пояснением, не падение."""
    from src.components.blocks import PieBlock, PieItem
    res = MetricResult(metric_id="p", label="Круговая", description="",
                       blocks=[PieBlock(title="пирог",
                                        items=[PieItem(name="a", value=1)])])
    path = tmp_path / "report.xlsx"
    build_excel([res], path)
    wb = openpyxl.load_workbook(path)
    assert "примечание" in wb[wb.sheetnames[0]]["A1"].value
