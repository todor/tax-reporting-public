"""Backwards-compatible module wrapper for the unified report_analyzer CLI."""

from __future__ import annotations

from report_analyzer.cli import main as unified_main


def main() -> int:
    return unified_main()


if __name__ == "__main__":
    raise SystemExit(main())
