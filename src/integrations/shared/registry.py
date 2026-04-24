from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass

import integrations

from .contracts import AnalyzerDefinition


class AnalyzerRegistryError(Exception):
    """Raised when analyzer registration/discovery fails."""


@dataclass(slots=True)
class AnalyzerRegistry:
    by_alias: dict[str, AnalyzerDefinition]
    alias_lookup: dict[str, str]

    def resolve(self, alias: str) -> AnalyzerDefinition:
        normalized = alias.strip().lower()
        canonical = self.alias_lookup.get(normalized)
        if canonical is None:
            available = ", ".join(sorted(self.by_alias))
            raise AnalyzerRegistryError(
                f"unknown analyzer alias: {alias!r} (available: {available})"
            )
        return self.by_alias[canonical]

    def definitions(self) -> list[AnalyzerDefinition]:
        return [self.by_alias[key] for key in sorted(self.by_alias)]


def discover_analyzer_registry() -> AnalyzerRegistry:
    by_alias: dict[str, AnalyzerDefinition] = {}
    alias_lookup: dict[str, str] = {}

    for module_info in pkgutil.walk_packages(
        integrations.__path__,
        prefix=f"{integrations.__name__}.",
    ):
        module_leaf = module_info.name.rsplit(".", 1)[-1]
        if module_leaf != "analyzer_definition" and not module_leaf.endswith("_analyzer_definition"):
            continue
        module = importlib.import_module(module_info.name)
        candidate_items: list[AnalyzerDefinition] = []

        definition = getattr(module, "ANALYZER", None)
        if definition is not None:
            candidate_items.append(definition)

        definitions = getattr(module, "ANALYZERS", None)
        if definitions is not None:
            if not isinstance(definitions, (list, tuple)):
                raise AnalyzerRegistryError(
                    f"{module_info.name}.ANALYZERS must be list/tuple of AnalyzerDefinition"
                )
            candidate_items.extend(definitions)

        if not candidate_items:
            continue

        for item in candidate_items:
            if not isinstance(item, AnalyzerDefinition):
                raise AnalyzerRegistryError(
                    f"{module_info.name} contains non-AnalyzerDefinition registration"
                )
            alias = item.alias.strip().lower()
            if alias in by_alias:
                raise AnalyzerRegistryError(f"duplicate analyzer alias: {alias}")
            by_alias[alias] = item

            all_aliases = {alias, *(token.strip().lower() for token in item.aliases)}
            for raw_alias in all_aliases:
                if raw_alias == "":
                    continue
                existing = alias_lookup.get(raw_alias)
                if existing is not None and existing != alias:
                    raise AnalyzerRegistryError(
                        f"alias collision: {raw_alias!r} ({existing}, {alias})"
                    )
                alias_lookup[raw_alias] = alias

    if not by_alias:
        raise AnalyzerRegistryError("no analyzers with ANALYZER definition were discovered")

    return AnalyzerRegistry(by_alias=by_alias, alias_lookup=alias_lookup)
