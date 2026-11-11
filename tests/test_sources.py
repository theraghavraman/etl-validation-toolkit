from __future__ import annotations

import codecs

from openpyxl import Workbook

from etlguard.config import SourceSettings
from etlguard.sources.csv import CsvSource
from etlguard.sources.excel import ExcelSource


def test_csv_bom_chunking_and_physical_lineage(tmp_path):
    path = tmp_path / "bom.csv"
    path.write_bytes(codecs.BOM_UTF8 + b"a,b\n1, 2\n3,4\n5,6\n")
    cfg = SourceSettings(type="csv", path="bom.csv", chunksize=1)
    records = list(CsvSource(cfg, tmp_path).read("batch-id"))
    assert [row.data for row in records] == [
        {"a": "1", "b": " 2"}, {"a": "3", "b": "4"}, {"a": "5", "b": "6"},
    ]
    assert [row.lineage.source_row_number for row in records] == [2, 3, 4]
    assert all(row.lineage.batch_id == "batch-id" for row in records)
    assert records[0].lineage.ingested_at.tzinfo is not None


def test_csv_non_utf8_encoding_and_delimiter(tmp_path):
    path = tmp_path / "latin.csv"
    path.write_bytes("Name;City\nJosé;Paris\n".encode("cp1252"))
    cfg = SourceSettings(type="csv", path=path.name, delimiter=";", encoding="cp1252")
    records = list(CsvSource(cfg, tmp_path).read("b"))
    assert records[0].data == {"Name": "José", "City": "Paris"}


def test_csv_blank_record_is_counted(tmp_path):
    path = tmp_path / "blank.csv"
    path.write_text("a,b\n1,2\n\n3,4\n")
    records = list(CsvSource(SourceSettings(type="csv", path="blank.csv"), tmp_path).read("b"))
    assert len(records) == 3
    assert records[1].lineage.source_row_number == 3


def test_csv_header_offset(tmp_path):
    path = tmp_path / "header.csv"
    path.write_text("ignorable,text\na,b\n1,2\n")
    cfg = SourceSettings(type="csv", path="header.csv", header_row=2, chunksize=1)
    rows = list(CsvSource(cfg, tmp_path).read("b"))
    assert rows[0].data == {"a": "1", "b": "2"}
    assert rows[0].lineage.source_row_number == 3


def test_excel_sheet_name_skip_rows_and_header(tmp_path):
    book = Workbook()
    ws = book.active
    ws.title = "Wrong"
    ws.append(["x", "y"])
    ws = book.create_sheet("Data")
    for row in [["Report note"], ["ID", "Name"], ["C001", "Alice"], ["C002", "Bob"]]:
        ws.append(row)
    book.save(tmp_path / "file.xlsx")
    settings = SourceSettings(type="excel", path="file.xlsx", sheet="Data", skip_rows=1)
    records = list(ExcelSource(settings, tmp_path).read("b"))
    assert [row.data["ID"] for row in records] == ["C001", "C002"]
    assert records[0].lineage.source_row_number == 3


def test_excel_sheet_index(tmp_path):
    book = Workbook()
    ws = book.active
    ws.append(["Number"])
    ws.append([1])
    book.save(tmp_path / "num.xlsx")
    records = list(ExcelSource(SourceSettings(type="excel", path="num.xlsx", sheet=0), tmp_path).read("b"))
    assert records[0].data["Number"] == "1"
