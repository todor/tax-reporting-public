from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from integrations.p2p.shared.appendix6_models import (
    P2PAppendix6Result,
    UnsupportedSecondaryMarketModeError,
)
from integrations.p2p.shared.runtime import (
    build_appendix6_output_path,
    build_p2p_run_cli_summary_lines,
    validate_secondary_market_mode,
)


def test_build_appendix6_output_path_uses_input_stem(tmp_path: Path) -> None:
    input_path = tmp_path / "afranga_statement.pdf"
    input_path.write_bytes(b"x")

    output_path = build_appendix6_output_path(
        input_path=input_path,
        output_dir=tmp_path / "out",
        stem_fallback="fallback",
    )
    assert output_path.name == "afranga_statement_declaration.txt"


def test_build_appendix6_output_path_normalizes_spaces_and_case(tmp_path: Path) -> None:
    input_path = tmp_path / "Afranga report 2025.PDF"
    input_path.write_bytes(b"x")

    output_path = build_appendix6_output_path(
        input_path=input_path,
        output_dir=tmp_path / "out",
        stem_fallback="fallback",
    )
    assert output_path.name == "afranga_report_2025_declaration.txt"


def test_validate_secondary_market_mode_rejects_appendix5_by_default() -> None:
    with pytest.raises(UnsupportedSecondaryMarketModeError, match="not supported yet"):
        validate_secondary_market_mode(mode="appendix_5")


def test_build_p2p_run_cli_summary_lines() -> None:
    result = P2PAppendix6Result(
        platform="afranga",
        tax_year=2025,
        part1_rows=[],
        aggregate_code_603=Decimal("1"),
        aggregate_code_606=Decimal("2"),
        taxable_code_603=Decimal("3"),
        taxable_code_606=Decimal("4"),
        withheld_tax=Decimal("5"),
    )
    lines = build_p2p_run_cli_summary_lines(result=result, output_txt_path=Path("/tmp/out.txt"))
    assert any(line == "platform: afranga" for line in lines)
    assert any(line == "warnings: 0" for line in lines)
    assert any("Declaration TXT: /tmp/out.txt" == line for line in lines)
