from __future__ import annotations

from pathlib import Path

from tests.integrations.ibkr.support import _base_rows, _run


def test_ibkr_unknown_section_warning_is_consolidated(tmp_path: Path) -> None:
    rows = _base_rows()
    rows.extend(
        [
            ["Mystery Section", "Header", "Field"],
            ["Mystery Section", "Data", "Value"],
            ["Another Section", "Header", "Field"],
            ["Another Section", "Data", "Value"],
        ]
    )

    result = _run(tmp_path, rows)

    warnings = [warning for warning in result.summary.warnings if "които анализаторът все още не обработва" in warning]
    assert len(warnings) == 1
    assert "[Another Section]" in warnings[0]
    assert "[Mystery Section]" in warnings[0]


def test_ibkr_corporate_actions_are_not_duplicated_in_unknown_section_warning(tmp_path: Path) -> None:
    rows = _base_rows()
    rows.extend(
        [
            ["Corporate Actions", "Header", "Field"],
            ["Corporate Actions", "Data", "Value"],
            ["Mystery Section", "Header", "Field"],
            ["Mystery Section", "Data", "Value"],
        ]
    )

    result = _run(tmp_path, rows)

    assert result.summary.spb8_corporate_actions_present is True
    warnings = [warning for warning in result.summary.warnings if "които анализаторът все още не обработва" in warning]
    assert len(warnings) == 1
    assert "[Mystery Section]" in warnings[0]
    assert "Corporate Actions" not in warnings[0]
