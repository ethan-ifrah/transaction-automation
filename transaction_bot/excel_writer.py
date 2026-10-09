from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Any, Optional

from openpyxl import load_workbook


# Headers expected in row 1 of the staging sheet, in order.
# One row is written per agent per transaction.
COLUMNS = [
    "No Mls",
    "No de contrat",
    "Nom de rue",
    "Ville",
    "Province/État",
    "Code postal",
    "Courtier_Nom",
    "Courtier_Bureau",
    "Courtier_Titre",
    "Date d'acceptation",
    "Date notariée",
    "Prix vendu",
    "Rétribution",
    "Types",
]


@dataclass(frozen=True)
class RowData:
    values: Dict[str, Any]


def _find_header_map(ws) -> Dict[str, int]:
    """
    Returns {column_name: column_index} for the row 1 headers.
    Raises if any expected column is missing, so nothing is written
    into a sheet whose layout doesn't match.
    """
    header_map: Dict[str, int] = {}
    for col in range(1, ws.max_column + 1):
        val = ws.cell(row=1, column=col).value
        if isinstance(val, str):
            header_map[val.strip()] = col

    missing = [c for c in COLUMNS if c not in header_map]
    if missing:
        raise ValueError(f"Missing expected headers in row 1: {missing}")

    return header_map


def _next_empty_row(ws, anchor_col_name: str, header_map: Dict[str, int]) -> int:
    """
    Finds the first empty row by scanning down the anchor column.
    """
    anchor_col = header_map[anchor_col_name]
    r = 2
    while True:
        if ws.cell(row=r, column=anchor_col).value in (None, ""):
            return r
        r += 1


def append_rows(
    excel_path: Path,
    sheet_name: str,
    rows: List[RowData],
    anchor_col_name: str = "No Mls",
) -> int:
    """
    Appends rows under existing headers, starting at first empty row.
    Does NOT change formatting of header row.
    """
    wb = load_workbook(excel_path)
    if sheet_name not in wb.sheetnames:
        raise ValueError(f"Sheet not found: {sheet_name}. Found: {wb.sheetnames}")

    ws = wb[sheet_name]
    header_map = _find_header_map(ws)
    start_row = _next_empty_row(ws, anchor_col_name, header_map)

    current = start_row
    for row in rows:
        for col_name in COLUMNS:
            col_idx = header_map[col_name]
            value = row.values.get(col_name, "")

            cell = ws.cell(row=current, column=col_idx)
            cell.value = value

        current += 1

    wb.save(excel_path)
    return len(rows)
