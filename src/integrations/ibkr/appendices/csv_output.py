from __future__ import annotations

from ..constants import (
    ADDED_DIVIDENDS_COLUMNS,
    ADDED_INTEREST_COLUMNS,
    ADDED_OPEN_POSITIONS_COLUMNS,
    ADDED_TRADES_COLUMNS,
    ADDED_WITHHOLDING_COLUMNS,
)
from ..models import CsvStructureError, IbkrAnalyzerError, _ActiveHeader


def build_output_rows(
    *,
    rows: list[list[str]],
    active_headers: dict[int, _ActiveHeader],
    trades_row_extras: dict[int, list[str]],
    trades_row_base_len: dict[int, int],
    interest_row_extras: dict[int, list[str]],
    interest_row_base_len: dict[int, int],
    dividends_row_extras: dict[int, dict[str, str]],
    dividends_row_base_len: dict[int, int],
    dividends_row_added_columns: dict[int, list[str]],
    withholding_row_extras: dict[int, dict[str, str]],
    withholding_row_base_len: dict[int, int],
    withholding_row_added_columns: dict[int, list[str]],
    open_positions_row_extras: dict[int, dict[str, str]],
    open_positions_row_base_len: dict[int, int],
    open_positions_row_added_columns: dict[int, list[str]],
) -> list[list[str]]:
    output_rows: list[list[str]] = []
    for idx, row in enumerate(rows):
        if len(row) < 2:
            output_rows.append(row)
            continue

        if row[0] == "Trades" and row[1] == "Header":
            output_rows.append(row + ADDED_TRADES_COLUMNS)
            continue

        if row[0] == "Trades":
            base_len = trades_row_base_len.get(idx)
            if base_len is None:
                raise CsvStructureError(f"row {idx + 1}: Trades row encountered before Trades Header")
            padded = row + [""] * (base_len - len(row))
            extras = trades_row_extras.get(idx, [""] * len(ADDED_TRADES_COLUMNS))
            output_rows.append(padded + extras)
            continue

        if row[0] == "Interest" and row[1] == "Header":
            output_rows.append(row + ADDED_INTEREST_COLUMNS)
            continue

        if row[0] == "Interest":
            base_len = interest_row_base_len.get(idx)
            if base_len is None:
                raise CsvStructureError(f"row {idx + 1}: Interest row encountered before Interest Header")
            padded = row + [""] * (base_len - len(row))
            extras = interest_row_extras.get(idx, [""] * len(ADDED_INTEREST_COLUMNS))
            output_rows.append(padded + extras)
            continue

        if row[0] == "Dividends" and row[1] == "Header":
            added_cols = dividends_row_added_columns.get(
                idx,
                [col for col in ADDED_DIVIDENDS_COLUMNS if col not in row[2:]],
            )
            output_rows.append(row + added_cols)
            continue

        if row[0] == "Dividends":
            base_len = dividends_row_base_len.get(idx)
            if base_len is None:
                raise CsvStructureError(f"row {idx + 1}: Dividends row encountered before Dividends Header")
            padded = row + [""] * (base_len - len(row))
            active_header = active_headers.get(idx)
            added_cols = dividends_row_added_columns.get(
                idx,
                [col for col in ADDED_DIVIDENDS_COLUMNS if col not in (active_header.headers if active_header is not None else [])],
            )
            extras_map = dividends_row_extras.get(idx, {})
            extras = [extras_map.get(col, "") for col in added_cols]
            output_rows.append(padded + extras)
            continue

        if row[0] == "Withholding Tax" and row[1] == "Header":
            added_cols = withholding_row_added_columns.get(
                idx,
                [col for col in ADDED_WITHHOLDING_COLUMNS if col not in row[2:]],
            )
            output_rows.append(row + added_cols)
            continue

        if row[0] == "Withholding Tax":
            base_len = withholding_row_base_len.get(idx)
            if base_len is None:
                raise CsvStructureError(
                    f"row {idx + 1}: Withholding Tax row encountered before Withholding Tax Header"
                )
            padded = row + [""] * (base_len - len(row))
            active_header = active_headers.get(idx)
            added_cols = withholding_row_added_columns.get(
                idx,
                [col for col in ADDED_WITHHOLDING_COLUMNS if col not in (active_header.headers if active_header is not None else [])],
            )
            extras_map = withholding_row_extras.get(idx, {})
            extras = [extras_map.get(col, "") for col in added_cols]
            output_rows.append(padded + extras)
            continue

        if row[0] == "Open Positions" and row[1] == "Header":
            added_cols = open_positions_row_added_columns.get(
                idx,
                [col for col in ADDED_OPEN_POSITIONS_COLUMNS if col not in row[2:]],
            )
            output_rows.append(row + added_cols)
            continue

        if row[0] == "Open Positions":
            base_len = open_positions_row_base_len.get(idx)
            if base_len is None:
                raise CsvStructureError(
                    f"row {idx + 1}: Open Positions row encountered before Open Positions Header"
                )
            padded = row + [""] * (base_len - len(row))
            active_header = active_headers.get(idx)
            added_cols = open_positions_row_added_columns.get(
                idx,
                [col for col in ADDED_OPEN_POSITIONS_COLUMNS if col not in (active_header.headers if active_header is not None else [])],
            )
            extras_map = open_positions_row_extras.get(idx, {})
            extras = [extras_map.get(col, "") for col in added_cols]
            output_rows.append(padded + extras)
            continue

        output_rows.append(row)

    return output_rows


def validate_output_rows(
    *,
    output_rows: list[list[str]],
    active_headers: dict[int, _ActiveHeader],
    trades_row_base_len: dict[int, int],
    interest_row_base_len: dict[int, int],
    dividends_row_base_len: dict[int, int],
    dividends_row_added_columns: dict[int, list[str]],
    withholding_row_base_len: dict[int, int],
    withholding_row_added_columns: dict[int, list[str]],
    open_positions_row_base_len: dict[int, int],
    open_positions_row_added_columns: dict[int, list[str]],
) -> None:
    for idx, row in enumerate(output_rows):
        if len(row) >= 2 and row[0] == "Trades":
            base_len = trades_row_base_len.get(idx)
            if base_len is None:
                raise CsvStructureError(f"row {idx + 1}: Trades row encountered before Trades Header")
            expected_len = base_len + len(ADDED_TRADES_COLUMNS)
            if len(row) != expected_len:
                raise IbkrAnalyzerError(
                    f"Trades row column count mismatch at row {idx + 1}: expected {expected_len}, got {len(row)}"
                )

        if len(row) >= 2 and row[0] == "Interest":
            base_len = interest_row_base_len.get(idx)
            if base_len is None:
                raise CsvStructureError(f"row {idx + 1}: Interest row encountered before Interest Header")
            expected_len = base_len + len(ADDED_INTEREST_COLUMNS)
            if len(row) != expected_len:
                raise IbkrAnalyzerError(
                    f"Interest row column count mismatch at row {idx + 1}: expected {expected_len}, got {len(row)}"
                )

        if len(row) >= 2 and row[0] == "Dividends":
            base_len = dividends_row_base_len.get(idx)
            if base_len is None:
                raise CsvStructureError(f"row {idx + 1}: Dividends row encountered before Dividends Header")
            added_cols = dividends_row_added_columns.get(idx)
            if added_cols is None:
                if row[1] == "Header":
                    added_cols = [col for col in ADDED_DIVIDENDS_COLUMNS if col not in row[2:]]
                else:
                    active_header = active_headers.get(idx)
                    if active_header is None:
                        raise CsvStructureError(
                            f"row {idx + 1}: Dividends row encountered before Dividends Header"
                        )
                    added_cols = [col for col in ADDED_DIVIDENDS_COLUMNS if col not in active_header.headers]
            expected_len = base_len + len(added_cols)
            if len(row) != expected_len:
                raise IbkrAnalyzerError(
                    f"Dividends row column count mismatch at row {idx + 1}: expected {expected_len}, got {len(row)}"
                )

        if len(row) >= 2 and row[0] == "Withholding Tax":
            base_len = withholding_row_base_len.get(idx)
            if base_len is None:
                raise CsvStructureError(
                    f"row {idx + 1}: Withholding Tax row encountered before Withholding Tax Header"
                )
            added_cols = withholding_row_added_columns.get(idx)
            if added_cols is None:
                if row[1] == "Header":
                    added_cols = [col for col in ADDED_WITHHOLDING_COLUMNS if col not in row[2:]]
                else:
                    active_header = active_headers.get(idx)
                    if active_header is None:
                        raise CsvStructureError(
                            f"row {idx + 1}: Withholding Tax row encountered before Withholding Tax Header"
                        )
                    added_cols = [col for col in ADDED_WITHHOLDING_COLUMNS if col not in active_header.headers]
            expected_len = base_len + len(added_cols)
            if len(row) != expected_len:
                raise IbkrAnalyzerError(
                    f"Withholding Tax row column count mismatch at row {idx + 1}: expected {expected_len}, got {len(row)}"
                )

        if len(row) >= 2 and row[0] == "Open Positions":
            base_len = open_positions_row_base_len.get(idx)
            if base_len is None:
                raise CsvStructureError(
                    f"row {idx + 1}: Open Positions row encountered before Open Positions Header"
                )
            added_cols = open_positions_row_added_columns.get(idx)
            if added_cols is None:
                if row[1] == "Header":
                    added_cols = [col for col in ADDED_OPEN_POSITIONS_COLUMNS if col not in row[2:]]
                else:
                    active_header = active_headers.get(idx)
                    if active_header is None:
                        raise CsvStructureError(
                            f"row {idx + 1}: Open Positions row encountered before Open Positions Header"
                        )
                    added_cols = [col for col in ADDED_OPEN_POSITIONS_COLUMNS if col not in active_header.headers]
            expected_len = base_len + len(added_cols)
            if len(row) != expected_len:
                raise IbkrAnalyzerError(
                    f"Open Positions row column count mismatch at row {idx + 1}: expected {expected_len}, got {len(row)}"
                )
