from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from integrations.shared.spb8 import (
    SPB8Error,
    SPB8Row,
    aggregate_spb8_rows,
    default_platform_rows,
    manual_input_template_rows_for_platform,
    filter_rows_for_options,
    normalize_header_name,
    normalize_type,
    read_spb8_csv,
    render_spb8_notes_section,
    render_spb8_section,
    write_spb8_csv,
)


def test_header_normalization_accepts_aliases() -> None:
    assert normalize_header_name("account_name") == "account name"
    assert normalize_header_name("account-name") == "account name"
    assert normalize_header_name(" start value ") == "start nav"
    assert normalize_header_name("end_nav") == "end nav"


def test_type_normalization_accepts_codes_and_labels() -> None:
    assert normalize_type("1") == "01"
    assert normalize_type("03. Сметки, открити в чужбина") == "03"
    assert normalize_type("04 securities") == "04"


def test_country_and_type_are_inferred_from_platform_csv(tmp_path: Path) -> None:
    input_path = tmp_path / "spb8.csv"
    input_path.write_text(
        "account,platform,type,country,currency,start,end\n"
        "kraken account,kraken,,,EUR,1000,2000\n",
        encoding="utf-8",
    )

    rows = read_spb8_csv(input_path)

    assert rows == [
        SPB8Row(
            account_name="kraken account",
            platform="kraken",
            type_code="03",
            country="Ирландия",
            currency="EUR",
            start_nav=Decimal("1000"),
            end_nav=Decimal("2000"),
            source="csv",
        )
    ]


def test_bulgaria_rows_are_kept_but_excluded_from_filing_rendering() -> None:
    rows = default_platform_rows(platform="afranga", account_name="afranga report")
    filtered, notes = filter_rows_for_options(rows, enabled=True, exclude_crypto=False)

    assert filtered[0].country == "България"
    assert notes == ["Не се включва в СПБ-8: държава България."]
    assert render_spb8_section(filtered) == []
    assert render_spb8_notes_section(notes) == [
        "Забележки за СПБ-8",
        "- Не се включва в СПБ-8: държава България.",
    ]


def test_manual_template_rows_skip_automated_ibkr_platform() -> None:
    assert manual_input_template_rows_for_platform(platform="ibkr", account_name="ibkr report") == []
    assert manual_input_template_rows_for_platform(platform="kraken", account_name="kraken report") == [
        SPB8Row(
            account_name="kraken report",
            platform="kraken",
            type_code="03",
            country="Ирландия",
            currency="EUR",
        )
    ]


def test_write_completed_csv_uses_canonical_header(tmp_path: Path) -> None:
    output_path = tmp_path / "spb8-input-file.csv"

    write_spb8_csv(
        output_path,
        [
            SPB8Row(
                account_name="kraken",
                platform="kraken",
                type_code="03",
                country="Ирландия",
                currency="EUR",
                start_nav=Decimal("1000"),
                end_nav=Decimal("2000"),
            )
        ],
    )

    assert output_path.read_text(encoding="utf-8").splitlines()[0] == (
        "account name,platform,type,country,currency,start nav,end nav"
    )


def test_validation_reports_bad_platform_with_row_number(tmp_path: Path) -> None:
    input_path = tmp_path / "spb8.csv"
    input_path.write_text(
        "account name,platform,type,country,currency,start nav,end nav\n"
        "bad,unknown,03,Ireland,EUR,1,2\n",
        encoding="utf-8",
    )

    with pytest.raises(SPB8Error, match="row 2: unknown SPB-8 platform"):
        read_spb8_csv(input_path)


def test_validation_requires_nav_for_foreign_filing_rows(tmp_path: Path) -> None:
    input_path = tmp_path / "spb8.csv"
    input_path.write_text(
        "account name,platform,type,country,currency,start nav,end nav\n"
        "kraken,kraken,03,Ireland,EUR,,2\n",
        encoding="utf-8",
    )

    with pytest.raises(SPB8Error, match="row 2: start nav is required"):
        read_spb8_csv(input_path)


def test_no_spb8_disables_rows_and_crypto_exclusion_adds_note() -> None:
    rows = default_platform_rows(platform="kraken", account_name="kraken")

    assert filter_rows_for_options(rows, enabled=False, exclude_crypto=False) == ([], [])
    assert filter_rows_for_options(rows, enabled=True, exclude_crypto=True) == (
        [],
        ["Crypto platforms were excluded from SPB-8 because --spb8-exclude-crypto was used."],
    )


def test_aggregated_spb8_grouping_sums_compatible_rows() -> None:
    rows = [
        SPB8Row("kraken", "kraken", "03", "Ирландия", "EUR", Decimal("1000"), Decimal("2000")),
        SPB8Row("coinbase", "coinbase", "03", "Ирландия", "EUR", Decimal("3000"), Decimal("4000")),
        SPB8Row("isin", "ibkr", "04", "Ирландия", "", Decimal("1"), Decimal("2"), isin="IE00B4L5Y983"),
    ]

    grouped = aggregate_spb8_rows(rows)

    assert grouped == [
        SPB8Row("kraken, coinbase", "kraken", "03", "Ирландия", "EUR", Decimal("4000"), Decimal("6000")),
        SPB8Row("isin", "ibkr", "04", "Ирландия", "", Decimal("1"), Decimal("2"), isin="IE00B4L5Y983"),
    ]
