from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from integrations.crypto.shared.runtime import (
    build_enriched_ir_output_paths,
    default_eur_unit_rate_provider,
)


def test_build_enriched_ir_output_paths_normalizes_input_stem() -> None:
    output_csv_path, declaration_txt_path, state_json_path = build_enriched_ir_output_paths(
        input_path=Path("Coinbase Report - since inception.csv"),
        output_dir=Path("/tmp/out"),
        tax_year=2025,
        stem_fallback="coinbase_report",
    )

    assert output_csv_path == Path("/tmp/out/coinbase_report_since_inception_modified.csv")
    assert declaration_txt_path == Path("/tmp/out/coinbase_report_since_inception_declaration.txt")
    assert state_json_path == Path("/tmp/out/coinbase_report_since_inception_state_end_2025.json")


def test_build_enriched_ir_output_paths_uses_fallback_for_empty_normalized_stem() -> None:
    output_csv_path, declaration_txt_path, state_json_path = build_enriched_ir_output_paths(
        input_path=Path("---.csv"),
        output_dir=Path("/tmp/out"),
        tax_year=2025,
        stem_fallback="coinbase_report",
    )

    assert output_csv_path == Path("/tmp/out/coinbase_report_modified.csv")
    assert declaration_txt_path == Path("/tmp/out/coinbase_report_declaration.txt")
    assert state_json_path == Path("/tmp/out/coinbase_report_state_end_2025.json")


def test_default_eur_unit_rate_provider_returns_one_for_eur() -> None:
    provider = default_eur_unit_rate_provider(cache_dir=None)
    rate = provider("eur", datetime(2025, 1, 1, tzinfo=timezone.utc))
    assert rate == Decimal("1")
