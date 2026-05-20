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
    merge_external_platform_rows,
    missing_spb8_value_notes,
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
    assert normalize_header_name("ISIN") == "ISIN"
    assert normalize_header_name(" start value ") == "start amount"
    assert normalize_header_name("end_nav") == "end amount"


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
        "СПБ-8",
        "Бележки към СПБ-8",
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
        "account name,platform,type,country,ISIN,currency,start amount,end amount"
    )


def test_write_spb8_csv_uses_type_specific_isin_and_currency_values(tmp_path: Path) -> None:
    output_path = tmp_path / "spb8-input-file.csv"

    write_spb8_csv(
        output_path,
        [
            SPB8Row("kraken", "kraken", "03", "Ирландия", "EUR", Decimal("1000"), Decimal("2000")),
            SPB8Row("isin", "ibkr", "04", "Ирландия", "", Decimal("1"), Decimal("2"), isin="IE00B4L5Y983"),
        ],
    )

    assert output_path.read_text(encoding="utf-8").splitlines() == [
        "account name,platform,type,country,ISIN,currency,start amount,end amount",
        "kraken,kraken,03,Ирландия,-,EUR,1000,2000",
        "isin,ibkr,04,Ирландия,IE00B4L5Y983,-,1,2",
    ]


def test_validation_reports_bad_platform_with_row_number(tmp_path: Path) -> None:
    input_path = tmp_path / "spb8.csv"
    input_path.write_text(
        "account name,platform,type,country,ISIN,currency,start amount,end amount\n"
        "bad,unknown,03,Ireland,-,EUR,1,2\n",
        encoding="utf-8",
    )

    with pytest.raises(SPB8Error, match="row 2: unknown SPB-8 platform"):
        read_spb8_csv(input_path)


def test_validation_requires_nav_for_foreign_filing_rows(tmp_path: Path) -> None:
    input_path = tmp_path / "spb8.csv"
    input_path.write_text(
        "account name,platform,type,country,ISIN,currency,start amount,end amount\n"
        "kraken,kraken,03,Ireland,-,EUR,,2\n",
        encoding="utf-8",
    )

    rows = read_spb8_csv(input_path)

    assert rows[0].start_nav is None
    assert rows[0].end_nav == Decimal("2")


def test_type_specific_validation_for_spb8_csv(tmp_path: Path) -> None:
    type04_bad_currency = tmp_path / "bad_type04.csv"
    type04_bad_currency.write_text(
        "account name,platform,type,country,ISIN,currency,start amount,end amount\n"
        "isin,ibkr,04,Ireland,IE00B4L5Y983,EUR,1,2\n",
        encoding="utf-8",
    )
    with pytest.raises(SPB8Error, match="currency must be '-'"):
        read_spb8_csv(type04_bad_currency)

    non_security_bad_isin = tmp_path / "bad_type03.csv"
    non_security_bad_isin.write_text(
        "account name,platform,type,country,ISIN,currency,start amount,end amount\n"
        "kraken,kraken,03,Ireland,IE00B4L5Y983,EUR,1,2\n",
        encoding="utf-8",
    )
    with pytest.raises(SPB8Error, match="ISIN must be '-'"):
        read_spb8_csv(non_security_bad_isin)


def test_no_spb8_disables_rows_and_crypto_exclusion_adds_note() -> None:
    rows = default_platform_rows(platform="kraken", account_name="kraken")

    assert filter_rows_for_options(rows, enabled=False, exclude_crypto=False) == ([], [])
    assert filter_rows_for_options(rows, enabled=True, exclude_crypto=False) == (
        rows,
        [
            "Използвана интерпретация за този отчет: крипто платформите са включени като "
            "03. Сметки, открити в чужбина, с валута EUR по подразбиране. "
            "Потвърдете с вашия счетоводител."
        ],
    )
    assert filter_rows_for_options(rows, enabled=True, exclude_crypto=True) == (
        [],
        ["Крипто платформите са изключени от СПБ-8, защото е използван --spb8-exclude-crypto."],
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


def test_spb8_input_values_override_analyzer_values_and_empty_values_fall_back() -> None:
    analyzer_rows = [
        SPB8Row("kraken analyzer", "kraken", "03", "Ирландия", "EUR", Decimal("100"), Decimal("200")),
        SPB8Row("isin analyzer", "ibkr", "04", "Ирландия", "", Decimal("1"), Decimal("2"), isin="IE00B4L5Y983"),
    ]
    external_rows = [
        SPB8Row("kraken csv", "kraken", "03", "Ирландия", "EUR", Decimal("1000"), None, source="csv"),
        SPB8Row("isin csv", "ibkr", "04", "Ирландия", "", Decimal("10"), None, isin="IE00B4L5Y983", source="csv"),
    ]

    merged = merge_external_platform_rows(analyzer_rows, external_rows)

    assert merged == [
        SPB8Row("kraken csv", "kraken", "03", "Ирландия", "EUR", Decimal("1000"), Decimal("200"), source="csv"),
        SPB8Row("isin csv", "ibkr", "04", "Ирландия", "", Decimal("10"), Decimal("2"), isin="IE00B4L5Y983", source="csv"),
    ]


def test_missing_spb8_values_emit_warnings() -> None:
    notes = missing_spb8_value_notes(
        [
            SPB8Row("kraken", "kraken", "03", "Ирландия", "EUR", None, Decimal("2000")),
            SPB8Row("isin", "ibkr", "04", "Ирландия", "", None, Decimal("2"), isin="IE00B4L5Y983"),
        ]
    )

    assert any("platform=kraken" in note and "start amount" in note for note in notes)
    assert any("ISIN=IE00B4L5Y983" in note and "start amount" in note for note in notes)


def test_render_spb8_omits_non_securities_row_when_rendered_end_value_is_zero() -> None:
    lines = render_spb8_section(
        [
            SPB8Row(
                "revolut",
                "revolut",
                "03",
                "Литва",
                "USD",
                Decimal("0"),
                Decimal("0.1"),
            )
        ]
    )

    assert lines == []


def test_render_spb8_omits_securities_row_when_end_value_is_zero() -> None:
    lines = render_spb8_section(
        [
            SPB8Row(
                "isin",
                "ibkr",
                "04",
                "Ирландия",
                "",
                Decimal("12"),
                Decimal("0"),
                isin="IE00B4L5Y983",
            )
        ]
    )

    assert lines == []


def test_render_spb8_combines_declaration_rows_and_notes_under_one_heading() -> None:
    lines = render_spb8_section(
        [
            SPB8Row(
                "kraken",
                "kraken",
                "03",
                "Ирландия",
                "EUR",
                Decimal("1000"),
                Decimal("2000"),
            )
        ],
        notes=["CFD позициите не се включват в СПБ-8."],
        aggregate=True,
    )

    assert lines.count("СПБ-8") == 1
    assert "Данни за попълване" in lines
    assert "Бележки към СПБ-8" in lines
    assert "- CFD позициите не се включват в СПБ-8." in lines
    assert "- Детайлите по платформи са налични в индивидуалните TXT файлове." in lines
