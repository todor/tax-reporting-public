from __future__ import annotations

from pathlib import Path

import pytest

from integrations.p2p.afranga import report_analyzer as analyzer
from tests.integrations.p2p.afranga.support import afranga_sample_pages, write_text_pdf


def test_analyze_afranga_report_end_to_end_with_real_pdf_file(tmp_path: Path) -> None:
    input_pdf = write_text_pdf(tmp_path / "afranga_sample.pdf", pages=afranga_sample_pages())

    run_result = analyzer.analyze_afranga_report(
        input_pdf=input_pdf,
        tax_year=2025,
        output_dir=tmp_path / "out",
    )

    assert run_result.output_txt_path.exists()
    text = run_result.output_txt_path.read_text(encoding="utf-8")
    assert "Приложение 6" in text
    assert "Част I" in text
    assert "- Ред 1.1" in text
    assert "- Обща сума на доходите с код 603: 84.50" in text
    assert "- Облагаем доход по чл. 35, код 606: 100.00" in text
    assert "- Удържан и/или внесен окончателен данък за доходи: 9.50" in text
    assert "- Използван режим за вторичен пазар: appendix_6" in text


def test_analyze_afranga_report_fails_when_secondary_mode_appendix_5(tmp_path: Path) -> None:
    input_pdf = write_text_pdf(tmp_path / "afranga_sample.pdf", pages=afranga_sample_pages())

    with pytest.raises(analyzer.AfrangaAnalyzerError, match="not supported yet"):
        _ = analyzer.analyze_afranga_report(
            input_pdf=input_pdf,
            tax_year=2025,
            output_dir=tmp_path / "out",
            secondary_market_mode="appendix_5",
        )
