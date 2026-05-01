from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_normalize_report():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "normalize_example_report.py"
    spec = importlib.util.spec_from_file_location("normalize_example_report", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.normalize_report


def test_normalize_report_rewrites_repo_specific_file_uris() -> None:
    normalize_report = _load_normalize_report()
    text = "\n".join(
        [
            "- ibkr: file:///Users/example/tax-reporting/examples/inputs/ibkr.csv",
            "- kraken: file:///D:/a/tax-reporting/tax-reporting/examples/inputs/kraken.csv",
            "  declaration: file:///Users/example/tax-reporting/output/examples/ibkr/report.txt",
            "  declaration: file:///D:/a/tax-reporting/tax-reporting/output/examples/kraken/report.txt",
        ]
    )

    assert normalize_report(text) == "\n".join(
        [
            "- ibkr: file://<REPO>/examples/inputs/ibkr.csv",
            "- kraken: file://<REPO>/examples/inputs/kraken.csv",
            "  declaration: file://<OUTPUT>/ibkr/report.txt",
            "  declaration: file://<OUTPUT>/kraken/report.txt",
        ]
    )
