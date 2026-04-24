from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Literal

CliMode = Literal["single", "aggregate"]


def _alias_prefix(alias: str) -> str:
    return alias.replace("_", "-")


def add_mode_argument(
    parser: argparse.ArgumentParser,
    *,
    mode: CliMode,
    analyzer_alias: str,
    single_flag: str,
    aggregate_flag: str | None = None,
    **kwargs: Any,
) -> None:
    """
    Add an analyzer argument once and project it to both CLI modes.

    single mode:
      --<single_flag>
    aggregate mode:
      --<analyzer-alias>-<aggregate_flag or single_flag>
    """
    if mode == "single":
        parser.add_argument(f"--{single_flag}", **kwargs)
        return
    suffix = aggregate_flag or single_flag
    parser.add_argument(f"--{_alias_prefix(analyzer_alias)}-{suffix}", **kwargs)


def option_value(
    args: argparse.Namespace,
    *,
    mode: CliMode,
    single_attr: str,
    aggregate_attr: str | None = None,
    group_options: dict[str, Any] | None = None,
    group_key: str | None = None,
    default: Any = None,
) -> Any:
    if mode == "single":
        value = getattr(args, single_attr, None)
        return default if value is None else value

    if aggregate_attr is not None:
        value = getattr(args, aggregate_attr, None)
        if value is not None:
            return value

    if group_options is not None and group_key is not None:
        value = group_options.get(group_key)
        if value is not None:
            return value

    return default


def resolved_cache_dir(
    args: argparse.Namespace,
    *,
    mode: CliMode,
    group_options: dict[str, Any],
) -> str | None:
    value = option_value(
        args,
        mode=mode,
        single_attr="cache_dir",
        group_options=group_options,
        group_key="cache_dir",
        default=None,
    )
    if value is None:
        return None
    if isinstance(value, Path):
        return str(value)
    return str(value)

