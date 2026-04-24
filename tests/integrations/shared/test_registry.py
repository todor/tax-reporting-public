from __future__ import annotations

from types import SimpleNamespace
from typing import Iterator

import pytest

from integrations.shared.contracts import AnalyzerDefinition, AnalyzerRunContext, TaxAnalysisResult
from integrations.shared.registry import AnalyzerRegistryError, discover_analyzer_registry


def _fake_definition(alias: str) -> AnalyzerDefinition:
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
        aliases=(),
        description=f"{alias} test analyzer",
        default_output_dir=contextlib_dummy_path(),
        input_suffixes=(".csv",),
        detection_token_sets=((alias,),),
        add_arguments=add_arguments,
        build_options=build_options,
        run=run,
    )


def contextlib_dummy_path():
    # keep it simple: only metadata is needed in registry tests
    from pathlib import Path

    return Path("/tmp")


def test_registry_discovers_analyzer_definition_and_suffix_variant(monkeypatch: pytest.MonkeyPatch) -> None:
    module_defs = {
        "integrations.alpha.analyzer_definition": SimpleNamespace(ANALYZER=_fake_definition("alpha")),
        "integrations.beta.spot_analyzer_definition": SimpleNamespace(ANALYZER=_fake_definition("beta")),
    }

    def fake_walk_packages(path, prefix) -> Iterator[SimpleNamespace]:  # noqa: ANN001
        _ = path
        _ = prefix
        yield SimpleNamespace(name="integrations.alpha.analyzer_definition")
        yield SimpleNamespace(name="integrations.beta.spot_analyzer_definition")

    def fake_import(name: str):  # noqa: ANN001
        return module_defs[name]

    monkeypatch.setattr("integrations.shared.registry.pkgutil.walk_packages", fake_walk_packages)
    monkeypatch.setattr("integrations.shared.registry.importlib.import_module", fake_import)

    registry = discover_analyzer_registry()
    assert sorted(registry.by_alias) == ["alpha", "beta"]


def test_registry_supports_analyzers_list(monkeypatch: pytest.MonkeyPatch) -> None:
    module_defs = {
        "integrations.bundle.multi_analyzer_definition": SimpleNamespace(
            ANALYZERS=[_fake_definition("a1"), _fake_definition("a2")]
        ),
    }

    def fake_walk_packages(path, prefix) -> Iterator[SimpleNamespace]:  # noqa: ANN001
        _ = path
        _ = prefix
        yield SimpleNamespace(name="integrations.bundle.multi_analyzer_definition")

    def fake_import(name: str):  # noqa: ANN001
        return module_defs[name]

    monkeypatch.setattr("integrations.shared.registry.pkgutil.walk_packages", fake_walk_packages)
    monkeypatch.setattr("integrations.shared.registry.importlib.import_module", fake_import)

    registry = discover_analyzer_registry()
    assert sorted(registry.by_alias) == ["a1", "a2"]


def test_registry_rejects_invalid_analyzers_container(monkeypatch: pytest.MonkeyPatch) -> None:
    module_defs = {
        "integrations.bundle.bad_analyzer_definition": SimpleNamespace(ANALYZERS="not-a-list"),
    }

    def fake_walk_packages(path, prefix) -> Iterator[SimpleNamespace]:  # noqa: ANN001
        _ = path
        _ = prefix
        yield SimpleNamespace(name="integrations.bundle.bad_analyzer_definition")

    def fake_import(name: str):  # noqa: ANN001
        return module_defs[name]

    monkeypatch.setattr("integrations.shared.registry.pkgutil.walk_packages", fake_walk_packages)
    monkeypatch.setattr("integrations.shared.registry.importlib.import_module", fake_import)

    with pytest.raises(AnalyzerRegistryError, match="ANALYZERS must be list/tuple"):
        discover_analyzer_registry()

