from __future__ import annotations

import datetime as dt
from typing import Any

from .fit_scorer import score_label_fit
from .parsers import parse_metrics_from_text, relevant_text
from .query_builder import build_candidate_brand_names, infer_profile
from .retriever import fetch_openfda_label_by_brand, label_text


FDA_READABLE_APPROVAL_PAGES = {
    "LUMAKRAS": {
        "label": "FDA approval page: sotorasib + panitumumab for KRAS G12C-mutated colorectal cancer",
        "url": "https://www.fda.gov/drugs/resources-information-approved-drugs/fda-approves-sotorasib-panitumumab-kras-g12c-mutated-colorectal-cancer",
    },
    "SOTORASIB": {
        "label": "FDA approval page: sotorasib + panitumumab for KRAS G12C-mutated colorectal cancer",
        "url": "https://www.fda.gov/drugs/resources-information-approved-drugs/fda-approves-sotorasib-panitumumab-kras-g12c-mutated-colorectal-cancer",
    },
}


def find_comparators(trial: dict[str, Any], core_drugs: list[dict[str, Any]], timeout: int = 25, max_candidates: int = 3) -> dict[str, Any]:
    profile = infer_profile(trial, core_drugs)
    candidate_names = build_candidate_brand_names(profile)
    comparators = []
    failures = []
    seen_sources = set()
    seen_drug_keys = set()

    for brand in candidate_names[: max_candidates + 4]:
        label = fetch_openfda_label_by_brand(brand, timeout=timeout)
        if not label:
            failures.append({"candidate": brand, "reason": "openFDA label not found"})
            continue
        text = label_text(label)
        if not text:
            failures.append({"candidate": brand, "reason": "FDA label text missing clinical context"})
            continue
        fit = score_label_fit(text, profile)
        if fit["level"] == "not_suitable":
            failures.append({"candidate": brand, "reason": "FDA label disease/target context did not fit", "fit": fit})
            continue
        source_url = label.get("_source_url", "")
        openfda = label.get("openfda", {})
        display_name = _first(openfda.get("brand_name")) or brand
        generic_name = _first(openfda.get("generic_name"))
        drug_key = (display_name or "").lower(), (generic_name or "").lower()
        if source_url in seen_sources or drug_key in seen_drug_keys:
            continue
        snippet = relevant_text(text, profile)
        source = _source_with_readable_page(brand, display_name, generic_name, source_url)
        metrics = parse_metrics_from_text(snippet, source)
        if not metrics:
            metrics = parse_metrics_from_text(text, source)
        if not metrics:
            failures.append({"candidate": brand, "reason": "FDA label found but no parseable efficacy metric in matched context", "fit": fit})
            continue
        comparators.append(
            {
                "id": brand.lower().replace(" ", "-"),
                "display_name": display_name,
                "generic_name": generic_name,
                "summary": f"从 FDA/openFDA 标签中找到 {display_name} 的同类参考数据；适配等级：{fit['level']}，适配分：{fit['score']}。",
                "metrics": metrics,
                "source": source,
                "fit": fit,
                "fetched_at": dt.datetime.now().isoformat(timespec="seconds"),
            }
        )
        seen_sources.add(source_url)
        seen_drug_keys.add(drug_key)
        if len(comparators) >= max_candidates:
            break

    if comparators:
        return {
            "available": True,
            "method": "generic_fda_comparator_pipeline",
            "profile": profile,
            "summary": "系统已根据当前试验药物的癌种、靶点/biomarker 和药物类别，实时检索 FDA/openFDA 官方标签并提取同类疗效指标。以下数据仅作同类参考，不代表当前试验药物疗效。",
            "comparators": comparators,
            "failures": failures[:8],
        }
    return {
        "available": False,
        "method": "generic_fda_comparator_pipeline",
        "profile": profile,
        "summary": "未找到足够可靠的 FDA 同类药物疗效对比。可能原因包括：当前试验药物机制无法稳定识别、FDA 标签没有同癌种/同 biomarker 场景、或标签中没有可解析疗效数字。",
        "comparators": [],
        "failures": failures[:12],
    }


def _first(value: Any) -> str | None:
    if isinstance(value, list) and value:
        return str(value[0])
    if isinstance(value, str):
        return value
    return None


def _source_with_readable_page(brand: str, display_name: str, generic_name: str | None, source_url: str) -> dict[str, str]:
    source = {"label": f"openFDA label: {display_name}", "url": source_url}
    for key in [brand, display_name, generic_name or ""]:
        readable = FDA_READABLE_APPROVAL_PAGES.get(str(key).upper())
        if readable:
            source["display_label"] = readable["label"]
            source["display_url"] = readable["url"]
            break
    return source
