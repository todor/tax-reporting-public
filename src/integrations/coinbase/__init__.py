"""Coinbase integration modules."""

from __future__ import annotations


def __getattr__(name: str):
    if name == "analyze_coinbase_report":
        from .report_analyzer import analyze_coinbase_report

        return analyze_coinbase_report
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["analyze_coinbase_report"]
