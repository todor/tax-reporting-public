from __future__ import annotations

from dataclasses import dataclass

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
    from report_analyzer.registry import BUILTIN_ANALYZERS

    return build_analyzer_registry(BUILTIN_ANALYZERS)


def build_analyzer_registry(analyzers: list[AnalyzerDefinition]) -> AnalyzerRegistry:
    by_alias: dict[str, AnalyzerDefinition] = {}
    alias_lookup: dict[str, str] = {}

    for item in analyzers:
        if not isinstance(item, AnalyzerDefinition):
            raise AnalyzerRegistryError("built-in analyzer registry contains non-AnalyzerDefinition registration")
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
        raise AnalyzerRegistryError(
            "No analyzers were discovered.\n\n"
            "If you are running a packaged executable, this likely means the application was built incorrectly."
        )

    return AnalyzerRegistry(by_alias=by_alias, alias_lookup=alias_lookup)
