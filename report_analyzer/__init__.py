from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "src"
_SRC_PACKAGE = _SRC / "report_analyzer"

if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

if str(_SRC_PACKAGE) not in __path__:
    __path__.append(str(_SRC_PACKAGE))

PROJECT_ROOT = _ROOT


def discover_analyzer_registry():
    from integrations.shared.registry import discover_analyzer_registry as _discover

    return _discover()


def _load_cli() -> ModuleType:
    from . import cli as _cli

    return _cli


def _sync_cli_overrides() -> None:
    _cli = _load_cli()
    _cli.discover_analyzer_registry = discover_analyzer_registry


def build_parser():
    _sync_cli_overrides()
    return _load_cli().build_parser()


def main(argv: list[str] | None = None) -> int:
    _sync_cli_overrides()
    return _load_cli().main(argv)


def _prepare_output_dir(*args, **kwargs):
    return _load_cli()._prepare_output_dir(*args, **kwargs)


__all__ = [
    "PROJECT_ROOT",
    "build_parser",
    "discover_analyzer_registry",
    "main",
    "_prepare_output_dir",
]
