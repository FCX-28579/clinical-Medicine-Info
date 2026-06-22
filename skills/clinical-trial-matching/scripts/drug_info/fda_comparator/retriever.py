from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from typing import Any


OPENFDA_LABEL = "https://api.fda.gov/drug/label.json"
_LABEL_CACHE: dict[str, dict[str, Any] | None] = {}


def fetch_openfda_label_by_brand(brand_name: str, timeout: int = 25) -> dict[str, Any] | None:
    return fetch_openfda_label(brand_name, timeout=timeout)


def fetch_openfda_label(drug_name: str, timeout: int = 25) -> dict[str, Any] | None:
    clean = re.sub(r"[^A-Za-z0-9 -]", " ", drug_name).strip()
    if not clean:
        return None
    cache_key = clean.lower()
    if cache_key in _LABEL_CACHE:
        cached = _LABEL_CACHE[cache_key]
        return dict(cached) if cached else None
    for field in ["brand_name", "generic_name"]:
        label = _fetch_openfda_label_by_field(field, clean, timeout=timeout)
        if label:
            _LABEL_CACHE[cache_key] = dict(label)
            return label
    _LABEL_CACHE[cache_key] = None
    return None


def _fetch_openfda_label_by_field(field: str, clean_name: str, timeout: int = 25) -> dict[str, Any] | None:
    query = f'openfda.{field}:"{clean_name}"'
    url = OPENFDA_LABEL + "?" + urllib.parse.urlencode({"search": query, "limit": "1"})
    req = urllib.request.Request(url, headers={"User-Agent": "ClinicalTrialSKILL/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    results = data.get("results") or []
    if not results:
        return None
    label = results[0]
    label["_source_url"] = url
    label["_source_type"] = "openFDA drug label"
    label["_source_query_field"] = field
    return label


def label_text(label: dict[str, Any]) -> str:
    fields = [
        "indications_and_usage",
        "clinical_studies",
        "clinical_pharmacology",
        "description",
    ]
    parts: list[str] = []
    for field in fields:
        value = label.get(field)
        if isinstance(value, list):
            parts.extend(str(v) for v in value)
        elif value:
            parts.append(str(value))
    return re.sub(r"\s+", " ", " ".join(parts)).strip()
