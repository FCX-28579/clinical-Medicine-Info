from __future__ import annotations

import datetime as dt
import html
import json
import re
import urllib.request
from dataclasses import dataclass
from typing import Any

from fda_comparator import find_comparators


CTGOV_API = "https://clinicaltrials.gov/api/v2/studies/{nct_id}"
CTGOV_PAGE = "https://clinicaltrials.gov/study/{nct_id}"

FDA_KRAS_G12C_CRC_SOURCES = [
    {
        "id": "sotorasib-panitumumab",
        "display_name": "Sotorasib + Panitumumab",
        "label": "FDA: sotorasib + panitumumab for KRAS G12C-mutated CRC",
        "url": "https://www.fda.gov/drugs/resources-information-approved-drugs/fda-approves-sotorasib-panitumumab-kras-g12c-mutated-colorectal-cancer",
    },
    {
        "id": "adagrasib-cetuximab",
        "display_name": "Adagrasib + Cetuximab",
        "label": "FDA: adagrasib + cetuximab for KRAS G12C-mutated CRC",
        "url": "https://www.fda.gov/drugs/resources-information-approved-drugs/fda-grants-accelerated-approval-adagrasib-cetuximab-kras-g12c-mutated-colorectal-cancer",
    },
]


@dataclass
class FetchResult:
    nct_id: str
    source_url: str
    study: dict[str, Any]


def fetch_ctgov_study(nct_id: str, timeout: int = 25) -> FetchResult:
    if not re.fullmatch(r"NCT\d{8}", nct_id or ""):
        raise ValueError(f"Invalid NCT id: {nct_id!r}")
    api_url = CTGOV_API.format(nct_id=nct_id)
    req = urllib.request.Request(api_url, headers={"User-Agent": "ClinicalTrialSKILL/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if resp.status != 200:
            raise RuntimeError(f"ClinicalTrials.gov returned HTTP {resp.status} for {nct_id}")
        study = json.loads(resp.read().decode("utf-8"))
    return FetchResult(nct_id=nct_id, source_url=CTGOV_PAGE.format(nct_id=nct_id), study=study)


def fetch_public_page_text(url: str, timeout: int = 25) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "ClinicalTrialSKILL/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status} for {url}")
        raw = resp.read().decode("utf-8", errors="ignore")
    text = re.sub(r"<script[\s\S]*?</script>", " ", raw, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return html.unescape(re.sub(r"\s+", " ", text)).strip()


def _get(data: dict[str, Any], path: list[str], default: Any = None) -> Any:
    cur: Any = data
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def _dedupe(items: list[Any]) -> list[Any]:
    seen = set()
    out = []
    for item in items:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True) if isinstance(item, dict) else str(item)
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def extract_trial_drug_module(fetch: FetchResult, timeout: int = 25) -> dict[str, Any]:
    study = fetch.study
    protocol = study.get("protocolSection", {})
    identification = protocol.get("identificationModule", {})
    status = protocol.get("statusModule", {})
    design = protocol.get("designModule", {})
    sponsor_module = protocol.get("sponsorCollaboratorsModule", {})
    arms_module = protocol.get("armsInterventionsModule", {})
    conditions_module = protocol.get("conditionsModule", {})
    eligibility = protocol.get("eligibilityModule", {})
    outcomes = protocol.get("outcomesModule", {})
    results = study.get("resultsSection", {})

    title = identification.get("briefTitle") or identification.get("officialTitle") or ""
    interventions = arms_module.get("interventions", []) or []
    lead_sponsor = _get(sponsor_module, ["leadSponsor", "name"], "页面未标明")
    collaborators = [c.get("name") for c in sponsor_module.get("collaborators", []) if c.get("name")]
    phases = design.get("phases", []) or []
    conditions = conditions_module.get("conditions", []) or []
    core_drugs, background_drugs = split_core_and_combination_drugs(title, interventions)
    posted_results = bool(study.get("hasResults"))

    module = {
        "trial_id": fetch.nct_id,
        "source_url": fetch.source_url,
        "title": title,
        "official_title": identification.get("officialTitle"),
        "status": status.get("overallStatus"),
        "phase": phases,
        "conditions": conditions,
        "lead_sponsor": lead_sponsor,
        "collaborators": collaborators,
        "core_study_drugs": core_drugs,
        "combination_or_background_drugs": background_drugs,
        "eligibility_drug_context": summarize_eligibility_drug_context(eligibility.get("eligibilityCriteria", "")),
        "posted_results": posted_results,
        "manufacturer": manufacturer_section(lead_sponsor, collaborators, fetch.source_url),
        "mechanism": mechanism_section(core_drugs, fetch.source_url),
        "ctgov_results_summary": summarize_results_section(fetch.nct_id, fetch.source_url, results, outcomes, posted_results),
        "same_class_comparison": same_class_comparison(fetch, title, conditions, core_drugs, timeout=timeout),
        "patient_readable_notes": patient_readable_notes(core_drugs, posted_results),
        "quality_flags": quality_flags(posted_results, core_drugs, background_drugs),
        "source_policy": "本模块的当前试验药物信息来自推荐试验的 ClinicalTrials.gov 页面/API；同类药物疗效对比仅来自实时读取的 FDA 官方页面。",
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
    }
    return module


def split_core_and_combination_drugs(title: str, interventions: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    drug_interventions = [
        normalize_intervention(i)
        for i in interventions
        if (i.get("type") or "").upper() in {"DRUG", "BIOLOGICAL", "COMBINATION_PRODUCT"}
    ]
    title_norm = _norm(title)
    core: list[dict[str, Any]] = []
    background: list[dict[str, Any]] = []

    for item in drug_interventions:
        name_norm = _norm(item["name"])
        aliases = [_norm(a) for a in item.get("other_names", [])]
        mentioned_in_title = name_norm and name_norm in title_norm
        alias_in_title = any(alias and alias in title_norm for alias in aliases)
        is_named_study_medicine = mentioned_in_title or alias_in_title
        is_combo_product = item.get("type") == "COMBINATION_PRODUCT"
        if is_named_study_medicine and not is_combo_product:
            core.append(item)
        else:
            background.append(item)

    if not core and drug_interventions:
        first = drug_interventions[0]
        core = [first]
        background = [item for item in drug_interventions[1:]]

    return _dedupe(core), _dedupe(background)


def normalize_intervention(intervention: dict[str, Any]) -> dict[str, Any]:
    name = intervention.get("name") or "未命名药物"
    description = intervention.get("description") or "试验页面未提供药物机制/说明。"
    return {
        "name": name,
        "type": intervention.get("type"),
        "description": description,
        "intro_zh": f"{name} 是 ClinicalTrials.gov 当前推荐试验页面列出的核心研究药物/干预措施。页面说明为：{description}",
        "intro_en": f"{name} is a core investigational intervention listed on the current recommended ClinicalTrials.gov study page. Page description: {description}",
        "other_names": intervention.get("otherNames", []) or [],
        "arm_group_labels": intervention.get("armGroupLabels", []) or [],
    }


def manufacturer_section(lead_sponsor: str, collaborators: list[str], source_url: str) -> dict[str, Any]:
    collaborator_text = f"；合作者：{', '.join(collaborators)}" if collaborators else ""
    return {
        "name": lead_sponsor,
        "background": f"ClinicalTrials.gov 页面列出的申办方为 {lead_sponsor}{collaborator_text}。该信息用于说明试验责任方，不自动等同于药品上市许可持有人或商业化厂家。",
        "sources": [{"label": "ClinicalTrials.gov 试验页面", "url": source_url}],
    }


def mechanism_section(core_drugs: list[dict[str, Any]], source_url: str) -> dict[str, Any]:
    if not core_drugs:
        summary = "未能从试验页面稳定识别核心研究药物，需人工复核干预措施。"
    else:
        parts = []
        for drug in core_drugs:
            desc = drug.get("description") or "页面未提供机制说明"
            parts.append(f"{drug.get('name')}: {desc}")
        summary = "；".join(parts)
    return {
        "summary": summary,
        "certainty": "来自 ClinicalTrials.gov intervention 字段",
        "sources": [{"label": "ClinicalTrials.gov 试验页面", "url": source_url}],
    }


def summarize_eligibility_drug_context(criteria: str) -> list[str]:
    if not criteria:
        return ["试验页面未提供完整入排标准文本。"]
    lines = [re.sub(r"\s+", " ", line).strip() for line in criteria.splitlines()]
    terms = [
        "prior treatment",
        "previous treatment",
        "washout",
        "kras",
        "egfr",
        "shp2",
        "chemotherapy",
        "immunotherapy",
        "targeted therapy",
        "measurable disease",
        "ecog",
    ]
    picked = []
    for line in lines:
        low = line.lower()
        if len(line) >= 8 and any(term in low for term in terms):
            picked.append(line)
    return picked[:8] or ["入排标准中未自动提取到明确药物相关限制；仍需医生/CRC 复核完整 criteria。"]


def summarize_results_section(nct_id: str, source_url: str, results: dict[str, Any], outcomes: dict[str, Any], posted_results: bool) -> dict[str, Any]:
    if not posted_results:
        return {
            "available": False,
            "summary": "该 ClinicalTrials.gov 页面当前未显示 posted results；不能从该页面生成本药/本组合的 ORR、PFS、OS、DOR 等疗效数字。",
            "metrics": [],
            "source": {"label": f"ClinicalTrials.gov {nct_id}", "url": source_url},
        }

    metrics: list[dict[str, Any]] = []
    for level, items in [("主要终点", outcomes.get("primaryOutcomes", []) or []), ("次要终点", outcomes.get("secondaryOutcomes", []) or [])]:
        for item in items[:8]:
            metrics.append(
                {
                    "name": item.get("measure"),
                    "value": item.get("timeFrame") or "页面 outcome 字段未给出数值",
                    "comparison": level,
                    "description": item.get("description"),
                    "source": {"label": f"ClinicalTrials.gov {nct_id}", "url": source_url},
                }
            )
    return {
        "available": True,
        "summary": "该页面标记为有 posted results。当前模块展示可解析的 outcome 字段；具体疗效数字仍需打开 CT.gov Results 表格复核。",
        "metrics": metrics,
        "source": {"label": f"ClinicalTrials.gov {nct_id}", "url": source_url},
    }


def is_kras_or_ras_crc_context(title: str, conditions: list[str], core_drugs: list[dict[str, Any]]) -> bool:
    text = _norm(
        " ".join(
            [
                title,
                " ".join(conditions),
                " ".join(d.get("name", "") for d in core_drugs),
                " ".join(d.get("description", "") for d in core_drugs),
            ]
        )
    )
    has_kras_or_ras = any(term in text for term in ["kras", "ras", "pankras", "pan kras", "pan-kras"])
    has_crc = any(term in text for term in ["colorectal", "colon", "rectal", "crc"])
    has_solid_kras = "solid tumor" in text and has_kras_or_ras
    return has_kras_or_ras and (has_crc or has_solid_kras)


def same_class_comparison(fetch: FetchResult, title: str, conditions: list[str], core_drugs: list[dict[str, Any]], timeout: int = 25) -> dict[str, Any]:
    trial = {
        "trial_id": fetch.nct_id,
        "title": title,
        "conditions": conditions,
        "source_url": fetch.source_url,
    }
    return find_comparators(trial, core_drugs, timeout=timeout)


def parse_fda_comparator(source: dict[str, str], text: str) -> dict[str, Any]:
    lower_id = source["id"]
    if lower_id == "sotorasib-panitumumab":
        pfs = re.search(
            r"Median PFS was\s*([0-9.]+)\s*months.*?and\s*([0-9.]+)\s*months.*?ORR was\s*([0-9]+)%.*?and\s*0",
            text,
            flags=re.I | re.S,
        )
        if not pfs:
            raise ValueError("未能从 FDA 页面解析 sotorasib + panitumumab 的 PFS/ORR 字段")
        metrics = [
            {
                "name": "Median PFS",
                "value": f"{pfs.group(1)} months vs {pfs.group(2)} months",
                "comparison": "sotorasib 960 mg + panitumumab vs standard of care",
                "source": {"label": source["label"], "url": source["url"]},
            },
            {
                "name": "ORR",
                "value": f"{pfs.group(3)}% vs 0%",
                "comparison": "sotorasib 960 mg + panitumumab vs standard of care",
                "source": {"label": source["label"], "url": source["url"]},
            },
        ]
        summary = f"FDA 页面显示，中位 PFS 为 {pfs.group(1)} 个月 vs {pfs.group(2)} 个月，ORR 为 {pfs.group(3)}% vs 0%。"
    elif lower_id == "adagrasib-cetuximab":
        orr = re.search(r"ORR was\s*([0-9]+)%.*?median DOR was\s*([0-9.]+)\s*months", text, flags=re.I | re.S)
        if not orr:
            raise ValueError("未能从 FDA 页面解析 adagrasib + cetuximab 的 ORR/DOR 字段")
        metrics = [
            {
                "name": "ORR",
                "value": f"{orr.group(1)}%",
                "comparison": "single-arm adagrasib + cetuximab cohort",
                "source": {"label": source["label"], "url": source["url"]},
            },
            {
                "name": "Median DOR",
                "value": f"{orr.group(2)} months",
                "comparison": "single-arm adagrasib + cetuximab cohort",
                "source": {"label": source["label"], "url": source["url"]},
            },
        ]
        summary = f"FDA 页面显示，ORR 为 {orr.group(1)}%，中位 DOR 为 {orr.group(2)} 个月。"
    else:
        raise ValueError(f"Unknown FDA comparator source: {source['id']}")

    return {
        "id": source["id"],
        "display_name": source["display_name"],
        "summary": summary,
        "metrics": metrics,
        "source": {"label": source["label"], "url": source["url"]},
        "fetched_at": dt.datetime.now().isoformat(timespec="seconds"),
    }


def patient_readable_notes(core_drugs: list[dict[str, Any]], posted_results: bool) -> list[str]:
    names = "、".join(d.get("name", "") for d in core_drugs if d.get("name")) or "核心研究药物"
    notes = [
        f"本模块围绕推荐试验页面中识别到的核心研究药物：{names}。",
        "厂家/申办方信息来自该推荐试验页面；同类药物对照来自实时读取的 FDA/openFDA 官方信息。",
    ]
    if not posted_results:
        notes.append("当前报告不展开本试验药物自身疗效数据，重点展示同类 FDA 药物参考信息。")
    notes.append("同类药物对比只用于帮助理解治疗方向，不能替代研究中心对当前试验药物的解释。")
    return notes


def quality_flags(posted_results: bool, core_drugs: list[dict[str, Any]], background_drugs: list[dict[str, Any]]) -> list[str]:
    flags = []
    if not core_drugs:
        flags.append("未能从试验页面稳定识别核心研究药物，需人工复核干预措施。")
    if background_drugs:
        flags.append("联合/背景治疗药物未作为主要模块展开，避免报告冗余；完整干预措施仍可在 CT.gov 页面复核。")
    flags.append("申办方信息按 ClinicalTrials.gov 页面展示，不自动等同于药品上市许可持有人。")
    flags.append("FDA 同类疗效数据为实时读取的官方页面信息，仅作为同类参考。")
    return flags


def build_dynamic_modules(nct_ids: list[str], timeout: int = 25) -> dict[str, Any]:
    modules: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for nct_id in nct_ids:
        try:
            fetch = fetch_ctgov_study(nct_id, timeout=timeout)
            modules[nct_id] = extract_trial_drug_module(fetch, timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            errors[nct_id] = str(exc)
    return {"modules": modules, "errors": errors}
