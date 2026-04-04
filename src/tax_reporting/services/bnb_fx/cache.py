from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

from .models import CacheError, QuarterCacheData, QuarterKey
from .utils import ensure_directory

logger = logging.getLogger(__name__)


def default_cache_dir() -> Path:
    """Return platform-appropriate default cache location."""
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))

    return base / "tax_reporting" / "bnb_fx"


def resolve_cache_dir(cache_dir: str | Path | None = None) -> Path:
    path = Path(cache_dir).expanduser() if cache_dir is not None else default_cache_dir()
    ensure_directory(path)
    return path


def quarter_cache_path(quarter: QuarterKey, cache_dir: str | Path | None = None) -> Path:
    root = resolve_cache_dir(cache_dir)
    return root / quarter.cache_file_name


def quarter_is_cached(quarter: QuarterKey, cache_dir: str | Path | None = None) -> bool:
    return quarter_cache_path(quarter, cache_dir).exists()


def load_quarter_cache(
    quarter: QuarterKey,
    cache_dir: str | Path | None = None,
) -> QuarterCacheData | None:
    path = quarter_cache_path(quarter, cache_dir)
    if not path.exists():
        return None

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        data = QuarterCacheData.from_dict(payload)
    except Exception as exc:  # noqa: BLE001
        raise CacheError(f"failed to read quarter cache {path}") from exc

    logger.debug("Loaded quarter cache: %s", path)
    return data


def save_quarter_cache(data: QuarterCacheData, cache_dir: str | Path | None = None) -> Path:
    path = quarter_cache_path(data.quarter, cache_dir)
    ensure_directory(path.parent)

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            delete=False,
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        ) as tmp:
            json.dump(data.to_dict(), tmp, ensure_ascii=False, sort_keys=True, indent=2)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_path = Path(tmp.name)

        tmp_path.replace(path)
    except Exception as exc:  # noqa: BLE001
        raise CacheError(f"failed to write quarter cache {path}") from exc

    logger.debug("Saved quarter cache: %s", path)
    return path
