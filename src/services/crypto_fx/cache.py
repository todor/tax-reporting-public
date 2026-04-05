from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .models import CacheError, SymbolYearCache


def default_cache_dir() -> Path:
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))

    return base / "tax_reporting" / "crypto_fx"


def resolve_cache_dir(cache_dir: str | Path | None = None) -> Path:
    root = Path(cache_dir).expanduser() if cache_dir is not None else default_cache_dir()
    root.mkdir(parents=True, exist_ok=True)
    return root


def symbol_year_cache_path(
    *,
    market: str = "spot",
    exchange: str,
    symbol: str,
    year: int,
    cache_dir: str | Path | None = None,
) -> Path:
    root = resolve_cache_dir(cache_dir)
    return root / market.lower() / exchange.lower() / symbol.upper() / f"{year}.json"


def load_symbol_year_cache(
    *,
    market: str = "spot",
    exchange: str,
    symbol: str,
    year: int,
    cache_dir: str | Path | None = None,
) -> SymbolYearCache | None:
    path = symbol_year_cache_path(
        market=market,
        exchange=exchange,
        symbol=symbol,
        year=year,
        cache_dir=cache_dir,
    )
    if not path.exists():
        return None

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return SymbolYearCache.from_dict(payload)
    except Exception as exc:  # noqa: BLE001
        raise CacheError(f"failed to read cache {path}") from exc


def save_symbol_year_cache(
    cache: SymbolYearCache,
    *,
    cache_dir: str | Path | None = None,
) -> Path:
    path = symbol_year_cache_path(
        market=cache.market,
        exchange=cache.exchange,
        symbol=cache.symbol,
        year=cache.year,
        cache_dir=cache_dir,
    )
    path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            delete=False,
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        ) as tmp:
            json.dump(cache.to_dict(), tmp, ensure_ascii=False, sort_keys=True, indent=2)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_path = Path(tmp.name)

        tmp_path.replace(path)
    except Exception as exc:  # noqa: BLE001
        raise CacheError(f"failed to write cache {path}") from exc

    return path
