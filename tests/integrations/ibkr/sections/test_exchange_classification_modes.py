from __future__ import annotations

from pathlib import Path

from tests.integrations.ibkr import support as h

EXCHANGE_CLASS_EU_NON_REGULATED = h.EXCHANGE_CLASS_EU_NON_REGULATED
EXCHANGE_CLASS_EU_REGULATED = h.EXCHANGE_CLASS_EU_REGULATED
EXCHANGE_CLASS_NON_EU = h.EXCHANGE_CLASS_NON_EU
EXCHANGE_CLASS_UNMAPPED = h.EXCHANGE_CLASS_UNMAPPED
EXCHANGE_CLASS_INVALID = h.EXCHANGE_CLASS_INVALID
_classify_exchange = h._classify_exchange
_run = h._run
_rows_with_review_status = h._rows_with_review_status
_base_rows = h._base_rows


def test_open_world_basic_exchange_classifications() -> None:
    assert _classify_exchange("IBIS2") == EXCHANGE_CLASS_EU_REGULATED
    assert _classify_exchange("TGATE") == EXCHANGE_CLASS_EU_NON_REGULATED
    assert _classify_exchange("NYSE") == EXCHANGE_CLASS_NON_EU
    assert _classify_exchange("NASDAQ") == EXCHANGE_CLASS_NON_EU
    assert _classify_exchange("LSE") == EXCHANGE_CLASS_NON_EU
    assert _classify_exchange("SWX") == EXCHANGE_CLASS_NON_EU
    assert _classify_exchange("NEWCODE") == EXCHANGE_CLASS_UNMAPPED
    assert _classify_exchange("EUIBFRSH") == EXCHANGE_CLASS_EU_NON_REGULATED
    assert _classify_exchange("TRWBIT") == EXCHANGE_CLASS_EU_NON_REGULATED
    assert _classify_exchange("TRADEWEBG") == EXCHANGE_CLASS_EU_NON_REGULATED
    assert _classify_exchange("") == EXCHANGE_CLASS_INVALID


def test_closed_world_activation_from_cli_exchanges(tmp_path: Path) -> None:
    open_result = _run(tmp_path / "open", _base_rows(), mode="listed_symbol")
    assert open_result.summary.exchange_classification_mode == "OPEN_WORLD MODE"

    closed_result = _run(
        tmp_path / "closed",
        _base_rows(),
        mode="listed_symbol",
        eu_regulated_exchanges=["ENEXT.FR"],
    )
    assert closed_result.summary.exchange_classification_mode == "CLOSED_WORLD MODE"


def test_closed_world_activation_from_explicit_flag(tmp_path: Path) -> None:
    open_result = _run(tmp_path / "open", _base_rows(), mode="listed_symbol")
    assert open_result.summary.exchange_classification_mode == "OPEN_WORLD MODE"

    closed_result = _run(
        tmp_path / "closed",
        _base_rows(),
        mode="listed_symbol",
        closed_world=True,
    )
    assert closed_result.summary.exchange_classification_mode == "CLOSED_WORLD MODE"
    assert closed_result.summary.cli_eu_regulated_overrides == set()


def test_cli_override_can_promote_non_regulated_to_eu_regulated() -> None:
    assert _classify_exchange("TGATE") == EXCHANGE_CLASS_EU_NON_REGULATED
    assert _classify_exchange(
        "TGATE",
        eu_regulated_exchange_overrides={"TGATE"},
    ) == EXCHANGE_CLASS_EU_REGULATED


def test_cli_override_can_promote_non_eu_to_eu_regulated() -> None:
    assert _classify_exchange("NYSE") == EXCHANGE_CLASS_NON_EU
    assert _classify_exchange(
        "NYSE",
        eu_regulated_exchange_overrides={"NYSE"},
    ) == EXCHANGE_CLASS_EU_REGULATED


def test_closed_world_readable_unknown_codes_do_not_remain_unmapped() -> None:
    for code in ("EUIBFRSH", "TRWBIT", "TRADEWEBG"):
        assert _classify_exchange(code, closed_world_mode=True) == EXCHANGE_CLASS_EU_NON_REGULATED


def test_open_world_unmapped_listing_requires_review(tmp_path: Path) -> None:
    rows = _rows_with_review_status(
        listing_exchange="NEWCODE",
        execution_exchange="NEWCODE",
        review_status="",
    )
    result = _run(tmp_path, rows, mode="listed_symbol")
    assert result.summary.review_required_rows == 1
    assert any("Unmapped listing exchange (open-world mode)" in warning for warning in result.summary.warnings)


def test_closed_world_unmapped_listing_is_auto_non_regulated(tmp_path: Path) -> None:
    rows = _rows_with_review_status(
        listing_exchange="NEWCODE",
        execution_exchange="NEWCODE",
        review_status="",
    )
    result = _run(
        tmp_path,
        rows,
        mode="listed_symbol",
        eu_regulated_exchanges=["ENEXT.FR"],
    )
    assert result.summary.review_required_rows == 0
    assert result.summary.appendix_5.rows == 1
    assert result.summary.appendix_13.rows == 0


def test_closed_world_flag_unmapped_listing_is_auto_non_regulated(tmp_path: Path) -> None:
    rows = _rows_with_review_status(
        listing_exchange="NEWCODE",
        execution_exchange="NEWCODE",
        review_status="",
    )
    result = _run(
        tmp_path,
        rows,
        mode="listed_symbol",
        closed_world=True,
    )
    assert result.summary.review_required_rows == 0
    assert result.summary.appendix_5.rows == 1
    assert result.summary.appendix_13.rows == 0


def test_closed_world_execution_no_unmapped_reason_for_readable_unknown_exchange(tmp_path: Path) -> None:
    rows = _rows_with_review_status(
        listing_exchange="IBIS2",
        execution_exchange="EUIBFRSH",
        review_status="",
    )
    result = _run(
        tmp_path,
        rows,
        mode="execution_exchange",
        closed_world=True,
    )
    assert result.summary.appendix_5.rows == 1
    assert result.summary.review_rows == 0
    assert "EUIBFRSH" in result.summary.encountered_eu_non_regulated_exchanges
    assert "EUIBFRSH" not in result.summary.encountered_unmapped_exchanges
    assert all(entry.reason != "EU-listed + unmapped execution" for entry in result.summary.review_entries)


def test_closed_world_invalid_exchange_still_requires_review(tmp_path: Path) -> None:
    rows = _rows_with_review_status(
        listing_exchange="-",
        execution_exchange="-",
        review_status="",
    )
    result = _run(
        tmp_path,
        rows,
        mode="listed_symbol",
        eu_regulated_exchanges=["ENEXT.FR"],
    )
    assert result.summary.review_required_rows == 1
    assert any("Invalid listing exchange" in warning for warning in result.summary.warnings)


def test_cli_exchange_normalization_and_dedup(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        _base_rows(),
        mode="listed_symbol",
        eu_regulated_exchanges=[" tgate ", "TGATE, tgate "],
    )
    assert result.summary.exchange_classification_mode == "CLOSED_WORLD MODE"
    assert result.summary.cli_eu_regulated_overrides == {"TGATE"}


def test_audit_section_contains_exchange_categories(tmp_path: Path) -> None:
    rows = _base_rows()
    rows[7][6] = "NEWCODE"
    rows[9][6] = "-"
    result = _run(tmp_path, rows, mode="execution_exchange")
    text = result.declaration_txt_path.read_text(encoding="utf-8")

    assert "Одитни данни" in text
    assert "EU-регулирани пазари, открити в отчета" in text
    assert "EU нерегулирани пазари, открити в отчета" in text
    assert "Не-EU пазари, открити в отчета" in text
    assert "Неразпознати пазари, открити в отчета" in text
    assert "Невалидни/нечетими стойности за пазар, открити в отчета" in text
    assert "NEWCODE" in text


def test_audit_uses_effective_classification_with_cli_override(tmp_path: Path) -> None:
    rows = _rows_with_review_status(
        listing_exchange="TGATE",
        execution_exchange="IBIS2",
        review_status="",
    )
    result = _run(
        tmp_path,
        rows,
        mode="listed_symbol",
        eu_regulated_exchanges=["TGATE"],
    )
    assert "TGATE" in result.summary.encountered_eu_regulated_exchanges
    assert "TGATE" not in result.summary.encountered_eu_non_regulated_exchanges


def test_execution_exchange_audit_tracks_listing_always_and_execution_conditionally(tmp_path: Path) -> None:
    rows = _rows_with_review_status(
        listing_exchange="NASDAQ",  # NON_EU listing routes directly to Appendix 5
        execution_exchange="ISLAND",
        review_status="",
    )
    result = _run(tmp_path, rows, mode="execution_exchange")

    assert "NASDAQ" in result.summary.encountered_non_eu_exchanges
    assert "ISLAND" not in result.summary.encountered_unmapped_exchanges


def test_execution_exchange_audit_includes_execution_when_listing_is_eu_regulated(tmp_path: Path) -> None:
    rows = _rows_with_review_status(
        listing_exchange="IBIS2",
        execution_exchange="EUDARK",
        review_status="",
    )
    result = _run(tmp_path, rows, mode="execution_exchange")
    assert "IBIS2" in result.summary.encountered_eu_regulated_exchanges
    assert "EUDARK" in result.summary.encountered_eu_non_regulated_exchanges


def test_execution_mode_branch_listing_non_eu_goes_appendix5_without_execution_step(tmp_path: Path) -> None:
    rows = _rows_with_review_status(
        listing_exchange="NASDAQ",
        execution_exchange="EUIBFRSH",
        review_status="",
    )
    result = _run(tmp_path, rows, mode="execution_exchange")
    assert result.summary.appendix_5.rows == 1
    assert result.summary.appendix_13.rows == 0
    assert result.summary.review_rows == 0
    assert "EUIBFRSH" not in result.summary.encountered_unmapped_exchanges


def test_execution_mode_branch_listing_eu_non_regulated_goes_appendix5_without_execution_step(tmp_path: Path) -> None:
    rows = _rows_with_review_status(
        listing_exchange="TGATE",
        execution_exchange="EUIBFRSH",
        review_status="",
    )
    result = _run(tmp_path, rows, mode="execution_exchange")
    assert result.summary.appendix_5.rows == 1
    assert result.summary.appendix_13.rows == 0
    assert result.summary.review_rows == 0
    assert "TGATE" in result.summary.encountered_eu_non_regulated_exchanges
    assert "EUIBFRSH" not in result.summary.encountered_unmapped_exchanges


def test_execution_mode_branch_listing_eu_regulated_then_execution_regulated_goes_appendix13(tmp_path: Path) -> None:
    rows = _rows_with_review_status(
        listing_exchange="IBIS2",
        execution_exchange="IBIS2",
        review_status="",
    )
    result = _run(tmp_path, rows, mode="execution_exchange")
    assert result.summary.appendix_13.rows == 1
    assert result.summary.appendix_5.rows == 0
    assert result.summary.review_rows == 0


def test_execution_mode_branch_listing_eu_regulated_then_execution_unknown_goes_review(tmp_path: Path) -> None:
    rows = _rows_with_review_status(
        listing_exchange="IBIS2",
        execution_exchange="NEWCODE",
        review_status="",
    )
    result = _run(tmp_path, rows, mode="execution_exchange")
    assert result.summary.review_rows == 1
    assert any(entry.reason == "EU-listed + unmapped execution" for entry in result.summary.review_entries)


def test_execution_mode_branch_listing_unknown_then_execution_regulated_goes_appendix13(tmp_path: Path) -> None:
    rows = _rows_with_review_status(
        listing_exchange="NEWCODE",
        execution_exchange="IBIS2",
        review_status="",
    )
    result = _run(tmp_path, rows, mode="execution_exchange")
    assert result.summary.appendix_13.rows == 1
    assert result.summary.appendix_5.rows == 0
    assert result.summary.review_rows == 0
    assert "NEWCODE" in result.summary.encountered_unmapped_exchanges


def test_execution_mode_invalid_listing_still_discovers_readable_execution_unmapped(tmp_path: Path) -> None:
    rows = _rows_with_review_status(
        listing_exchange="-",
        execution_exchange="TRADEWEBG",
        review_status="",
    )
    result = _run(tmp_path, rows, mode="execution_exchange")

    assert result.summary.review_rows == 1
    assert any(entry.reason == "Invalid listing exchange" for entry in result.summary.review_entries)
    assert "TRADEWEBG" in result.summary.encountered_eu_non_regulated_exchanges
    assert "TRADEWEBG" not in result.summary.encountered_unmapped_exchanges
    assert "<EMPTY>" in result.summary.encountered_invalid_exchange_values
