from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from pathlib import Path

from .contracts import AnalyzerDefinition
from .registry import AnalyzerRegistry, AnalyzerRegistryError

_TOKEN_SPLIT_RE = re.compile(r"[^A-Za-z0-9]+")


class InputDetectionError(Exception):
    """Raised when analyzer input detection/overrides are invalid."""


@dataclass(slots=True)
class DetectionItem:
    path: Path
    analyzer_alias: str | None
    reason: str


@dataclass(slots=True)
class DetectionResult:
    detected: dict[str, list[Path]]
    detected_items: list[DetectionItem] = field(default_factory=list)
    ignored_items: list[DetectionItem] = field(default_factory=list)


def _tokenize_filename(path: Path) -> set[str]:
    tokens = [token for token in _TOKEN_SPLIT_RE.split(path.stem.lower()) if token]
    return set(tokens)


def _matches_definition(*, definition: AnalyzerDefinition, path: Path, tokens: set[str]) -> bool:
    suffix = path.suffix.strip().lower()
    if definition.input_suffixes and suffix not in definition.input_suffixes:
        return False
    if not definition.detection_token_sets:
        return False
    for token_set in definition.detection_token_sets:
        if all(token in tokens for token in token_set):
            return True
    return False


def detect_analyzer_inputs(
    *,
    input_dir: Path,
    include_pattern: str | None,
    registry: AnalyzerRegistry,
) -> DetectionResult:
    if not input_dir.exists():
        raise InputDetectionError(f"input-dir does not exist: {input_dir}")
    if not input_dir.is_dir():
        raise InputDetectionError(f"input-dir is not a directory: {input_dir}")

    detected: dict[str, list[Path]] = {}
    detected_items: list[DetectionItem] = []
    ignored_items: list[DetectionItem] = []
    definitions = registry.definitions()

    for path in sorted(input_dir.iterdir()):
        if not path.is_file():
            ignored_items.append(
                DetectionItem(path=path, analyzer_alias=None, reason="not a regular file")
            )
            continue

        if include_pattern and not fnmatch.fnmatch(path.name, include_pattern):
            ignored_items.append(
                DetectionItem(
                    path=path,
                    analyzer_alias=None,
                    reason=f"does not match include-pattern {include_pattern!r}",
                )
            )
            continue

        tokens = _tokenize_filename(path)
        matches = [
            definition.alias
            for definition in definitions
            if _matches_definition(definition=definition, path=path, tokens=tokens)
        ]

        if not matches:
            ignored_items.append(
                DetectionItem(path=path, analyzer_alias=None, reason="no analyzer alias matched")
            )
            continue
        if len(matches) > 1:
            joined = ", ".join(sorted(matches))
            raise InputDetectionError(
                f"ambiguous analyzer mapping for {path.name!r}: matched [{joined}]"
            )

        alias = matches[0]
        detected.setdefault(alias, []).append(path)
        detected_items.append(
            DetectionItem(path=path, analyzer_alias=alias, reason="auto-detected from filename tokens")
        )

    return DetectionResult(
        detected=detected,
        detected_items=detected_items,
        ignored_items=ignored_items,
    )


def parse_analyzer_input_overrides(
    raw_values: list[str],
    *,
    registry: AnalyzerRegistry,
) -> dict[str, list[Path]]:
    overrides: dict[str, list[Path]] = {}
    for raw in raw_values:
        token = raw.strip()
        if token == "":
            raise InputDetectionError("empty --analyzer-input value")
        if "=" not in token:
            raise InputDetectionError(
                f"invalid --analyzer-input value {raw!r}; expected alias=path"
            )
        alias_raw, path_raw = token.split("=", 1)
        alias_candidate = alias_raw.strip()
        if alias_candidate == "":
            raise InputDetectionError(
                f"invalid --analyzer-input value {raw!r}; missing analyzer alias"
            )
        try:
            definition = registry.resolve(alias_candidate)
        except AnalyzerRegistryError as exc:
            raise InputDetectionError(str(exc)) from exc

        candidate_path = Path(path_raw.strip()).expanduser().resolve()
        if not candidate_path.exists():
            raise InputDetectionError(
                f"--analyzer-input for {definition.alias!r} does not exist: {candidate_path}"
            )
        if not candidate_path.is_file():
            raise InputDetectionError(
                f"--analyzer-input for {definition.alias!r} is not a file: {candidate_path}"
            )

        alias_paths = overrides.setdefault(definition.alias, [])
        if candidate_path in alias_paths:
            raise InputDetectionError(
                f"duplicate --analyzer-input override for analyzer {definition.alias!r}: {candidate_path}"
            )
        alias_paths.append(candidate_path)
    return overrides
