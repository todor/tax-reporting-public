from __future__ import annotations

from integrations.ibkr.constants import COUNTRY_NAME_BY_ISO, COUNTRY_NAME_TO_ISO
from integrations.ibkr.constants import _normalize_country_lookup_key as normalize_country_lookup_key


def normalize_country_name(value: str) -> str:
    candidate = value.strip()
    if candidate == "":
        return ""
    iso = COUNTRY_NAME_TO_ISO.get(normalize_country_lookup_key(candidate))
    if iso is None:
        return ""
    return COUNTRY_NAME_BY_ISO[iso][1]
