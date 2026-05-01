from __future__ import annotations

from pathlib import Path

import pytest

from integrations.shared.contracts import AnalyzerDefinition, AnalyzerRunContext, TaxAnalysisResult
from integrations.shared.registry import AnalyzerRegistryError, build_analyzer_registry, discover_analyzer_registry
from report_analyzer.registry import BUILTIN_ANALYZERS, list_analyzers


def _fake_definition(alias: str, *, aliases: tuple[str, ...] = ()) -> AnalyzerDefinition:
    def add_arguments(parser, mode):  # noqa: ANN001
        _ = parser
        _ = mode

    def build_options(args, mode, group_options):  # noqa: ANN001
        _ = args
        _ = mode
        _ = group_options
        return {}

    def run(context: AnalyzerRunContext) -> TaxAnalysisResult:
        raise RuntimeError("not used in registry tests")

    return AnalyzerDefinition(
        alias=alias,
        group="test",
        aliases=aliases,
        description=f"{alias} test analyzer",
        default_output_dir=Path("/tmp"),
        input_suffixes=(".csv",),
        detection_token_sets=((alias,),),
        add_arguments=add_arguments,
        build_options=build_options,
        run=run,
    )


def test_registry_uses_static_builtin_analyzers() -> None:
    registry = discover_analyzer_registry()

    assert sorted(registry.by_alias) == list_analyzers()
    assert sorted(registry.by_alias) == sorted(analyzer.alias for analyzer in BUILTIN_ANALYZERS)
    assert "kraken" in registry.by_alias
    assert "ibkr" in registry.by_alias


def test_registry_resolves_alias_variants() -> None:
    registry = build_analyzer_registry([_fake_definition("canonical", aliases=("short",))])

    assert registry.resolve("short").alias == "canonical"


def test_registry_rejects_invalid_registration() -> None:
    with pytest.raises(AnalyzerRegistryError, match="non-AnalyzerDefinition"):
        build_analyzer_registry(["not-an-analyzer"])  # type: ignore[list-item]


def test_registry_rejects_duplicate_alias() -> None:
    with pytest.raises(AnalyzerRegistryError, match="duplicate analyzer alias"):
        build_analyzer_registry([_fake_definition("dup"), _fake_definition("dup")])


def test_registry_rejects_alias_collision() -> None:
    with pytest.raises(AnalyzerRegistryError, match="alias collision"):
        build_analyzer_registry([
            _fake_definition("left", aliases=("shared",)),
            _fake_definition("right", aliases=("shared",)),
        ])


def test_registry_empty_error_is_user_facing() -> None:
    with pytest.raises(AnalyzerRegistryError, match="No analyzers were discovered"):
        build_analyzer_registry([])
