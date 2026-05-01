from __future__ import annotations

from integrations.shared.registry import discover_analyzer_registry

from . import cli as _cli
from .registry import list_analyzers

PROJECT_ROOT = _cli.PROJECT_ROOT
_prepare_output_dir = _cli._prepare_output_dir


def _sync_cli_overrides() -> None:
    _cli.discover_analyzer_registry = discover_analyzer_registry


def build_parser():
    _sync_cli_overrides()
    return _cli.build_parser()


def main(argv: list[str] | None = None) -> int:
    _sync_cli_overrides()
    return _cli.main(argv)

__all__ = [
    "PROJECT_ROOT",
    "build_parser",
    "discover_analyzer_registry",
    "list_analyzers",
    "main",
    "_prepare_output_dir",
]
