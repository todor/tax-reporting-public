from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from integrations.ibkr.sections.corporate_actions import (
    parse_merged_acquisition_final_tuple,
    process_corporate_actions_section,
)
from integrations.ibkr.shared import _build_active_headers
from tests.integrations.ibkr import support as h


def _classify(rows: list[list[str]]):
    active_headers, _seen_headers = _build_active_headers(rows)
    return process_corporate_actions_section(rows=rows, active_headers=active_headers)


def test_merged_acquisition_final_tuple_parses_symbol_name_and_isin() -> None:
    parsed = parse_merged_acquisition_final_tuple(
        "GOGL(BMG396372051) Merged(Acquisition) WITH BE0003816338 19 for 20 "
        "(CMBT, CMB TECH NV, BE0003816338)"
    )

    assert parsed is not None
    assert parsed.symbol == "CMBT"
    assert parsed.name == "CMB TECH NV"
    assert parsed.isin == "BE0003816338"


def test_corporate_actions_ignore_non_actionable_rows() -> None:
    result = _classify(
        [
            ["Corporate Actions", "Header", "Asset Category", "Currency", "Description", "Quantity"],
            ["Corporate Actions", "Data", "Total", "", "", "0"],
            ["Corporate Actions", "Data", "Total in EUR", "", "", "0"],
            ["Corporate Actions", "Data", "Stocks", "USD", "", "0"],
            ["Corporate Actions", "Data", "Stocks", "USD", "Basis: 31227.45249", "3315"],
        ]
    )

    assert result.ignored_rows == 4
    assert result.recognized_rows == 0
    assert result.unsupported_rows == 0
    assert {extras["Tax Status"] for extras in result.row_extras.values()} == {"IGNORE"}


def test_corporate_actions_recognize_merger_received_and_removed_quantities() -> None:
    result = _classify(
        [
            ["Corporate Actions", "Header", "Asset Category", "Currency", "Description", "Quantity"],
            [
                "Corporate Actions",
                "Data",
                "Stocks",
                "USD",
                "GOGL(BMG396372051) Merged(Acquisition) WITH BE0003816338 19 for 20 "
                "(CMBT, CMB TECH NV, BE0003816338)",
                "3217.65",
            ],
            [
                "Corporate Actions",
                "Data",
                "Stocks",
                "USD",
                "GOGL(BMG396372051) Merged(Acquisition) WITH BE0003816338 19 for 20 "
                "(GOGL, GOLDEN OCEAN GROUP LTD, BMG396372051)",
                "-3387",
            ],
        ]
    )

    assert result.recognized_rows == 2
    assert result.unsupported_rows == 0
    assert result.recognized_quantity_delta_by_isin["BE0003816338"] == Decimal("3217.65")
    assert result.recognized_quantity_delta_by_isin["BMG396372051"] == Decimal("-3387")
    assert result.recognized_quantity_delta_by_key[("Stocks", "BE0003816338")] == Decimal("3217.65")
    assert result.recognized_quantity_delta_by_key[("Stocks", "BMG396372051")] == Decimal("-3387")
    assert {extras["Tax Status"] for extras in result.row_extras.values()} == {"RECOGNIZED"}
    assert all("non-taxable corporate action" in extras["Tax Reason"] for extras in result.row_extras.values())


def test_unsupported_corporate_action_is_marked_not_supported() -> None:
    result = _classify(
        [
            ["Corporate Actions", "Header", "Asset Category", "Currency", "Description", "Quantity"],
            ["Corporate Actions", "Data", "Stocks", "USD", "SOME_CO(US1111111111) Spin-Off something", "10"],
        ]
    )

    assert result.unsupported_rows == 1
    assert result.row_extras[1]["Tax Status"] == "NOT_SUPPORTED"
    assert "not currently supported" in result.row_extras[1]["Tax Reason"]


def test_recognized_merger_rows_are_annotated_and_do_not_create_taxable_income(tmp_path: Path) -> None:
    rows = h._base_rows()
    rows.extend(
        [
            [
                "Corporate Actions",
                "Header",
                "Asset Category",
                "Currency",
                "Report Date",
                "Date/Time",
                "Description",
                "Quantity",
                "Proceeds",
                "Value",
                "Realized P/L",
            ],
            [
                "Corporate Actions",
                "Data",
                "Stocks",
                "USD",
                "2025-08-20",
                "2025-08-19, 20:25:00",
                "GOGL(BMG396372051) Merged(Acquisition) WITH BE0003816338 19 for 20 (CMBT, CMB TECH NV, BE0003816338)",
                "3217.65",
                "0",
                "25869.906",
                "0",
            ],
            ["Corporate Actions", "Data", "Stocks", "USD", "Closed Lot:", "2025-02-05", "Basis: 31227.45249", "3315", "", "", "0"],
            ["Corporate Actions", "Data", "Total", "", "", "", "", "0", "-1158.354", "0"],
        ]
    )

    result = h._run(tmp_path, rows)
    modified_rows = h._read_rows(result.output_csv_path)
    header = next(row for row in modified_rows if row[:2] == ["Corporate Actions", "Header"])
    data_rows = [row for row in modified_rows if row[:2] == ["Corporate Actions", "Data"]]
    idx = {name: header.index(name) for name in ("Tax Status", "Tax Action", "Tax Reason")}

    assert result.summary.corporate_actions_recognized_rows == 1
    assert result.summary.corporate_actions_ignored_rows == 2
    assert result.summary.corporate_actions_unsupported_rows == 0
    assert result.summary.appendix_6_code_606_eur == Decimal("0")
    assert result.summary.appendix_6_code_603_eur == Decimal("0")
    assert result.summary.appendix_8_output_rows == []
    assert data_rows[0][idx["Tax Status"]] == "RECOGNIZED"
    assert data_rows[0][idx["Tax Action"]] == "Apply recognized non-taxable merger"
    assert "does not create taxable income or realized gain/loss" in data_rows[0][idx["Tax Reason"]]
    assert data_rows[1][idx["Tax Status"]] == "IGNORE"
    assert data_rows[2][idx["Tax Status"]] == "IGNORE"
