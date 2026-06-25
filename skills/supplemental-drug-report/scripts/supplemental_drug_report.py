from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CTGOV_API = "https://clinicaltrials.gov/api/v2/studies/{nct_id}"
CTGOV_PAGE = "https://clinicaltrials.gov/study/{nct_id}"
DEFAULT_DB = Path(__file__).resolve().parents[2] / "fda-drug-evidence-builder" / "data" / "fda_drug_evidence_db.json"

BIOMARKER_PATTERNS = {
    "KRAS G12C": [r"kras\s*g12c"], "KRAS G12D": [r"kras\s*g12d"],
    "EGFR": [r"egfr", r"exon\s*19", r"l858r", r"t790m"], "ALK": [r"\balk\b"],
    "ROS1": [r"ros1"], "RET": [r"\bret\b"], "NTRK": [r"ntrk", r"trk fusion"],
    "HER2": [r"her2", r"erbb2"], "BRAF V600E": [r"braf\s*v600e"], "BRAF": [r"\bbraf\b"],
    "MSI-H": [r"msi[- ]?h", r"microsatellite instability"], "MMR": [r"dmmr", r"mismatch repair"],
    "PD-L1": [r"pd[- ]?l1"], "PD-1": [r"pd[- ]?1"], "VEGF": [r"\bvegf\b"],
    "VEGFR": [r"vegfr"], "MET": [r"\bmet\b", r"exon\s*14"], "PARP": [r"\bparp\b", r"brca1", r"brca2"],
    "CD19": [r"cd19"], "BCMA": [r"bcma"], "TROP2": [r"trop[- ]?2"],
}
CANCER_PATTERNS = {
    "CRC": ["colorectal", "colon cancer", "rectal cancer", "crc"],
    "NSCLC": ["non-small cell lung", "non small cell lung", "nsclc", "lung cancer"],
    "BREAST": ["breast cancer"], "HCC": ["hepatocellular", "liver cancer", "hcc"],
    "OVARIAN": ["ovarian", "fallopian tube", "primary peritoneal"], "PANCREATIC": ["pancreatic"],
    "GASTRIC": ["gastric cancer", "gastroesophageal", "stomach cancer"], "MELANOMA": ["melanoma"],
    "LYMPHOMA": ["lymphoma"], "LEUKEMIA": ["leukemia", "leukaemia", "acute lymphoblastic", "acute myeloid", "chronic lymphocytic"],
    "MYELOMA": ["multiple myeloma", "myeloma"], "PROSTATE": ["prostate cancer"],
    "BLADDER": ["bladder cancer", "urothelial"], "SOLID_TUMOR": ["solid tumor", "solid tumour", "advanced solid"],
}
CLASS_HINTS = {
    "KRAS G12C": ["kras g12c", "kras"], "EGFR": ["egfr", "epidermal growth factor receptor"],
    "ALK": ["alk"], "ROS1": ["ros1"], "RET": ["ret"], "NTRK": ["ntrk", "trk"],
    "HER2": ["her2", "erbb2"], "BRAF": ["braf"], "BRAF V600E": ["braf"],
    "PD-1": ["pd-1", "programmed death receptor"], "PD-L1": ["pd-l1", "programmed death ligand"],
    "VEGF": ["vegf", "vascular endothelial"], "VEGFR": ["vegfr", "vascular endothelial"],
    "PARP": ["parp"], "CD19": ["cd19", "car t"], "BCMA": ["bcma", "car t"],
    "TROP2": ["trop2", "antibody-drug conjugate", "adc"],
}
BACKGROUND_TERMS = {"placebo", "standard", "standard of care", "best supportive care", "radiation", "radiotherapy", "chemotherapy", "folfox", "folfiri", "leucovorin", "fluorouracil", "5-fu", "saline"}


def h(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def compact_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return " ".join(compact_text(v) for v in value)
    if isinstance(value, dict):
        return " ".join(compact_text(v) for v in value.values())
    return re.sub(r"\s+", " ", str(value)).strip()


def norm(value: Any) -> str:
    text = compact_text(value).lower()
    text = re.sub(r"[^a-z0-9+ -]", " ", text)
    text = re.sub(r"\b(tablets?|capsules?|injection|solution|oral|intravenous|iv|sc|mg|mcg|ml|usp|for|and|plus)\b", " ", text)
    return re.sub(r"\s+", " ", text).strip()


@dataclass
class FetchResult:
    nct_id: str
    source_url: str
    study: dict[str, Any]


def dedupe(items: list[Any]) -> list[Any]:
    seen = set(); out = []
    for item in items:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True) if isinstance(item, dict) else str(item)
        if key not in seen:
            seen.add(key); out.append(item)
    return out


def class_hits_for_context(drug_class: str, biomarkers: set[str]) -> list[str]:
    low = (drug_class or "").lower(); hits = []
    for biomarker in biomarkers:
        for hint in CLASS_HINTS.get(biomarker, []):
            if hint in low:
                hits.append(biomarker); break
    return sorted(set(hits))


class FdaEvidenceDatabase:
    def __init__(self, path: Path):
        self.path = path
        self.payload = read_json(path)
        self.drugs: list[dict[str, Any]] = list(self.payload.get("drugs", []))
        self.alias_index: dict[str, list[dict[str, Any]]] = {}
        for drug in self.drugs:
            for alias in self.aliases_for(drug):
                self.alias_index.setdefault(alias, []).append(drug)

    @staticmethod
    def aliases_for(drug: dict[str, Any]) -> set[str]:
        ident = drug.get("identity", {})
        values = [ident.get("brand_name"), ident.get("generic_name"), ident.get("substance_name"), ident.get("drug_id"), drug.get("drug_id")]
        aliases = {norm(v) for v in values if norm(v)}
        for key in ("generic_name", "brand_name"):
            val = norm(ident.get(key))
            if val:
                aliases.add(val.split()[0])
        return {a for a in aliases if len(a) >= 3}

    def direct_matches(self, drug_name: str, limit: int = 5) -> list[dict[str, Any]]:
        name = norm(drug_name)
        if not name:
            return []
        candidates: dict[str, tuple[int, dict[str, Any]]] = {}
        probes = {name, name.split()[0] if name.split() else name}
        for probe in probes:
            for drug in self.alias_index.get(probe, []):
                candidates[drug.get("drug_id", id(drug))] = (100, drug)
        for drug in self.drugs:
            best = 0
            for alias in self.aliases_for(drug):
                if alias == name:
                    best = max(best, 100)
                elif len(alias) >= 5 and (alias in name or name in alias):
                    best = max(best, 78)
                elif name.split() and alias == name.split()[0]:
                    best = max(best, 65)
            if best:
                key = drug.get("drug_id", str(id(drug)))
                if key not in candidates or best > candidates[key][0]:
                    candidates[key] = (best, drug)
        return [d for _, d in sorted(candidates.values(), key=lambda x: x[0], reverse=True)[:limit]]

    def comparator_candidates(self, context: dict[str, Any], direct: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
        cancers = set(context.get("cancer_contexts", [])); biomarkers = set(context.get("biomarkers", []))
        direct_ids = {d.get("drug_id") for d in direct}; scored: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
        for drug in self.drugs:
            ident = drug.get("identity", {}); efficacy = drug.get("evidence_modules", {}).get("efficacy", {})
            metrics = efficacy.get("metrics", []) or []
            drug_cancers = set(ident.get("cancer_context", []) or []); drug_biomarkers = set(ident.get("biomarkers", []) or [])
            excluded = set(ident.get("excluded_biomarkers", []) or []); drug_class = compact_text(ident.get("drug_class"))
            score = 0; reasons: list[str] = []
            if drug.get("drug_id") in direct_ids:
                score += 45; reasons.append("试验药物名称与 FDA 数据库药品直接匹配")
            cancer_overlap = sorted((cancers & drug_cancers) - {"ONCOLOGY_UNSPECIFIED"})
            if cancer_overlap:
                score += 28; reasons.append("癌种场景一致：" + "/".join(cancer_overlap))
            elif "SOLID_TUMOR" in cancers and drug_cancers:
                score += 12; reasons.append("同属实体瘤/泛肿瘤场景")
            biomarker_overlap = sorted(biomarkers & drug_biomarkers)
            if biomarker_overlap:
                score += 38; reasons.append("biomarker/靶点一致：" + "/".join(biomarker_overlap))
            bad_overlap = sorted(biomarkers & excluded)
            if bad_overlap:
                score -= 80; reasons.append("相关 biomarker 在 FDA 标签中只作为排除条件出现：" + "/".join(bad_overlap))
            class_hits = class_hits_for_context(drug_class, biomarkers)
            if class_hits:
                score += 24; reasons.append("药物类别/机制方向一致：" + "/".join(class_hits))
            if metrics:
                score += 18; reasons.append("FDA label 中有可追溯疗效指标")
            else:
                score -= 8
            if score >= 38:
                fit = "direct" if drug.get("drug_id") in direct_ids else "target+cancer" if cancer_overlap and (biomarker_overlap or class_hits) else "target" if (biomarker_overlap or class_hits) else "cancer-context"
                scored.append((score, drug, {"score": score, "fit_level": fit, "reasons": reasons}))
        scored.sort(key=lambda x: (x[0], len(x[1].get("evidence_modules", {}).get("efficacy", {}).get("metrics", []) or [])), reverse=True)
        if any(fit.get("fit_level") in {"direct", "target+cancer", "target"} for _, _, fit in scored):
            scored = [row for row in scored if row[2].get("fit_level") in {"direct", "target+cancer", "target"}]
        out = []; seen = set()
        for _, drug, fit in scored:
            key = (norm(drug.get("identity", {}).get("generic_name")), norm(drug.get("identity", {}).get("brand_name")))
            if key in seen:
                continue
            seen.add(key); item = dict(drug); item["_fit"] = fit; out.append(item)
            if len(out) >= limit:
                break
        return out


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


def fetch_or_load_study(nct_id: str, studies_by_id: dict[str, Any] | None, timeout: int) -> FetchResult:
    if studies_by_id and nct_id in studies_by_id:
        return FetchResult(nct_id=nct_id, source_url=CTGOV_PAGE.format(nct_id=nct_id), study=studies_by_id[nct_id])
    return fetch_ctgov_study(nct_id, timeout=timeout)


def get_path(data: dict[str, Any], path: list[str], default: Any = None) -> Any:
    cur: Any = data
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def any_target_text(text: str) -> bool:
    low = text.lower()
    return any(re.search(pattern, low) for patterns in BIOMARKER_PATTERNS.values() for pattern in patterns)


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


def split_core_and_background_drugs(title: str, interventions: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    drug_interventions = [normalize_intervention(i) for i in interventions if (i.get("type") or "").upper() in {"DRUG", "BIOLOGICAL", "COMBINATION_PRODUCT"}]
    title_norm = norm(title); core: list[dict[str, Any]] = []; background: list[dict[str, Any]] = []
    for item in drug_interventions:
        name_norm = norm(item["name"]); desc_norm = norm(item.get("description")); aliases = [norm(a) for a in item.get("other_names", [])]
        mentioned = name_norm and name_norm in title_norm; alias_mentioned = any(alias and alias in title_norm for alias in aliases)
        looks_background = any(term in name_norm for term in BACKGROUND_TERMS) and not any_target_text(name_norm + " " + desc_norm)
        if (mentioned or alias_mentioned or any_target_text(name_norm + " " + desc_norm)) and not looks_background:
            core.append(item)
        else:
            background.append(item)
    if not core and drug_interventions:
        core = [drug_interventions[0]]; background = drug_interventions[1:]
    return dedupe(core), dedupe(background)


def infer_cancer_contexts(*texts: Any) -> list[str]:
    low = norm(" ".join(compact_text(t) for t in texts)); found = []
    for key, aliases in CANCER_PATTERNS.items():
        if any(alias in low for alias in aliases):
            found.append(key)
    return found or (["SOLID_TUMOR"] if "solid" in low and "tumor" in low else [])


def infer_biomarkers(*texts: Any) -> list[str]:
    low = compact_text(" ".join(compact_text(t) for t in texts)).lower(); found = []
    for key, patterns in BIOMARKER_PATTERNS.items():
        if any(re.search(pattern, low, flags=re.I) for pattern in patterns):
            found.append(key)
    return found


def is_informative_biomarker_value(value: Any) -> bool:
    if value is None:
        return False
    low = str(value).strip().lower()
    if low in {"", "none", "null", "unknown", "unk", "na", "n/a", "negative", "neg", "阴性", "未知", "未检测", "未见", "无"}:
        return False
    return True


def patient_biomarker_text(patient: dict[str, Any]) -> str:
    parts = []
    for key in ["mutations", "biomarkers", "biomarkers_known", "molecular_profile"]:
        value = patient.get(key)
        if isinstance(value, dict):
            parts.extend(f"{k} {v}" for k, v in value.items() if is_informative_biomarker_value(v))
        elif isinstance(value, list):
            parts.extend(str(v) for v in value if is_informative_biomarker_value(v))
        elif is_informative_biomarker_value(value):
            parts.append(str(value))
    return " ".join(parts)


def positive_criteria_text(criteria: str) -> str:
    if not criteria:
        return ""
    picked = []
    for raw in criteria.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        low = line.lower()
        if not line:
            continue
        if any(term in low for term in ["exclusion", "excluded", "excluding", "except", "without", "no known", "prior", "previous"]):
            continue
        if any(term in low for term in ["inclusion", "required", "must have", "harboring", "harbouring", "positive", "mutation", "amplification", "fusion", "rearrangement"]):
            picked.append(line)
    return " ".join(picked[:12])


def infer_trial_context(study: dict[str, Any], core_drugs: list[dict[str, Any]], patient: dict[str, Any]) -> dict[str, Any]:
    protocol = study.get("protocolSection", {})
    title = compact_text([get_path(protocol, ["identificationModule", "briefTitle"], ""), get_path(protocol, ["identificationModule", "officialTitle"], "")])
    conditions = get_path(protocol, ["conditionsModule", "conditions"], []) or []
    criteria = get_path(protocol, ["eligibilityModule", "eligibilityCriteria"], "")
    drug_text = compact_text(core_drugs)
    patient_text = compact_text([patient.get("cancer_type"), patient.get("diagnosis"), patient_biomarker_text(patient)])
    positive_criteria = positive_criteria_text(criteria)
    return {
        "title": title,
        "conditions": conditions,
        "cancer_contexts": infer_cancer_contexts(title, conditions, patient_text),
        "biomarkers": infer_biomarkers(title, conditions, positive_criteria, drug_text, patient_text),
    }


def summarize_eligibility_drug_context(criteria: str) -> list[str]:
    if not criteria:
        return ["试验页面未提供完整入排标准文本。"]
    lines = [re.sub(r"\s+", " ", line).strip() for line in criteria.splitlines()]
    terms = ["prior treatment", "previous treatment", "washout", "mutation", "kras", "egfr", "alk", "her2", "chemotherapy", "immunotherapy", "targeted therapy", "measurable disease", "ecog"]
    picked = [line for line in lines if len(line) >= 8 and any(term in line.lower() for term in terms)]
    return picked[:8] or ["入排标准中未自动提取到明确药物相关限制；仍需医生/CRC 复核完整 criteria。"]


def manufacturer_section(lead_sponsor: str, collaborators: list[str], source_url: str) -> dict[str, Any]:
    collaborator_text = f"；合作者：{', '.join(collaborators)}" if collaborators else ""
    return {
        "name": lead_sponsor or "页面未标明",
        "background": f"ClinicalTrials.gov 页面列出的申办方为 {lead_sponsor or '页面未标明'}{collaborator_text}。该信息用于说明试验责任方，不自动等同于药品上市许可持有人或商业化厂家。",
        "sources": [{"label": "ClinicalTrials.gov 试验页面", "url": source_url}],
    }


def source_for_metric(metric: dict[str, Any], fallback_sources: list[dict[str, Any]]) -> dict[str, Any]:
    source = metric.get("source") or {}
    if source.get("url"):
        return source
    return fallback_sources[0] if fallback_sources else {"label": "FDA 本地证据库", "url": ""}


def zh_join(items: list[str]) -> str:
    clean = [str(x) for x in items if x]
    if not clean:
        return ""
    if len(clean) <= 3:
        return "、".join(clean)
    return "、".join(clean[:3]) + "等"


def first_sentences(text: str, max_sentences: int = 2) -> str:
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    cleaned = re.sub(r"^(\d+\s*)?(INDICATIONS AND USAGE|CLINICAL PHARMACOLOGY|Mechanism of Action|12\.1\s*Mechanism of Action)\s*", "", cleaned, flags=re.I)
    pieces = re.split(r"(?<=[.!?])\s+|\s+(?=[A-Z][a-z]+\s+cancer\b)|\s+•\s+", cleaned)
    useful = []
    for piece in pieces:
        piece = piece.strip(" ;:-")
        if len(piece) >= 20:
            useful.append(piece)
        if len(useful) >= max_sentences:
            break
    return " ".join(useful) if useful else cleaned[:360]


def translate_medical_terms(text: str) -> str:
    replacements = [
        (r"Non-small cell lung cancer \(NSCLC\)", "非小细胞肺癌（NSCLC）"),
        (r"non-small cell lung cancer", "非小细胞肺癌"),
        (r"locally advanced or metastatic", "局部晚期或转移性"),
        (r"metastatic colorectal cancer \(mCRC\)", "转移性结直肠癌（mCRC）"),
        (r"colorectal cancer \(CRC\)", "结直肠癌（CRC）"),
        (r"colorectal cancer", "结直肠癌"),
        (r"adult patients", "成人患者"),
        (r"as determined by an FDA-approved test", "经 FDA 批准检测确认"),
        (r"FDA-approved test", "FDA 批准检测"),
        (r"KRAS G12C-mutated", "KRAS G12C 突变"),
        (r"wild-type RAS", "RAS 野生型"),
        (r"at least one prior systemic therapy", "至少接受过一次既往系统治疗"),
        (r"prior treatment with fluoropyrimidine-, oxaliplatin-, and irinotecan-based chemotherapy", "既往接受过氟嘧啶、奥沙利铂和伊立替康为基础的化疗"),
        (r"in combination with panitumumab", "与 panitumumab 联合使用"),
        (r"in combination with cetuximab", "与 cetuximab 联合使用"),
        (r"in combination with sotorasib", "与 sotorasib 联合使用"),
        (r"as a single agent", "作为单药"),
        (r"inhibitor of the RAS GTPase family", "RAS GTPase 家族抑制剂"),
        (r"epidermal growth factor receptor \(EGFR\) antagonist", "表皮生长因子受体（EGFR）拮抗剂"),
        (r"EGFR antagonist", "EGFR 拮抗剂"),
        (r"irreversible inhibitor of KRAS G12C", "KRAS G12C 不可逆抑制剂"),
        (r"covalently binds", "共价结合"),
        (r"locks the mutant KRAS protein in its inactive state", "将突变 KRAS 蛋白锁定在失活状态"),
        (r"prevents downstream signaling", "阻断下游信号传导"),
        (r"tumor cell growth", "肿瘤细胞生长"),
        (r"tumor regression", "肿瘤退缩"),
        (r"anti-tumor activity", "抗肿瘤活性"),
        (r"cell growth and survival, motility, and proliferation", "细胞生长、生存、运动和增殖"),
    ]
    out = text
    for pattern, repl in replacements:
        out = re.sub(pattern, repl, out, flags=re.I)
    out = re.sub(r"\s+", " ", out).strip()
    return out


def summarize_indications_excerpt(excerpt: str) -> str:
    text = compact_text(excerpt)
    if not text:
        return "适应症：FDA 数据库未提供可稳定摘要的适应症原文。"
    low = text.lower()
    clauses = []
    if "kras g12c" in low and "non-small cell lung" in low:
        clauses.append("KRAS G12C 突变的局部晚期或转移性非小细胞肺癌（NSCLC）成人患者")
    if "kras g12c" in low and "colorectal" in low:
        if "panitumumab" in low:
            clauses.append("与 panitumumab 联合，用于既往接受含氟嘧啶、奥沙利铂和伊立替康治疗后的 KRAS G12C 突变转移性结直肠癌（mCRC）成人患者")
        elif "cetuximab" in low:
            clauses.append("与 cetuximab 联合，用于既往接受含氟嘧啶、奥沙利铂和伊立替康治疗后的 KRAS G12C 突变局部晚期或转移性结直肠癌（CRC）成人患者")
        elif "sotorasib" in low:
            clauses.append("与 sotorasib 联合，用于 KRAS G12C 突变转移性结直肠癌（mCRC）成人患者")
    if "wild-type ras" in low and "colorectal" in low:
        clauses.append("RAS 野生型转移性结直肠癌（mCRC）成人患者")
    if clauses:
        phrases = [("可" + c if c.startswith("与 ") else "可用于 " + c) for c in clauses[:2]]
        return "适应症：FDA 标签适应症原文显示，该药" + "；也".join(phrases) + "。"
    translated = translate_medical_terms(first_sentences(text, 2))
    return "适应症：" + translated.rstrip("。.") + "。"


def summarize_mechanism_excerpt(excerpt: str) -> str:
    text = compact_text(excerpt)
    if not text:
        return "作用机制：FDA 数据库未提供可稳定摘要的作用机制原文。"
    low = text.lower()
    if "irreversible inhibitor of kras g12c" in low or ("inhibitor of kras g12c" in low and "covalent" in low):
        return "作用机制：FDA 标签机制原文显示，该药为 KRAS G12C 抑制剂，可与 KRAS G12C 突变位点共价结合，将突变 KRAS 蛋白维持在失活状态，从而阻断下游信号传导。原文还提到其在 KRAS G12C 突变肿瘤模型中抑制肿瘤细胞生长或产生肿瘤退缩。"
    if "egfr" in low and ("antagonist" in low or "transmembrane glycoprotein" in low):
        return "作用机制：FDA 标签机制原文显示，该药作用于 EGFR 通路；EGFR 信号与肿瘤细胞生长、生存、运动和增殖相关。该机制说明支持其作为 EGFR 靶向治疗药物使用。"
    translated = translate_medical_terms(first_sentences(text, 2))
    return "作用机制：" + translated.rstrip("。.") + "。"


def concise_fda_description(record: dict[str, Any]) -> str:
    ident = record.get("identity", {})
    evidence = record.get("evidence_modules", {})
    name = ident.get("brand_name") or ident.get("generic_name") or record.get("drug_id") or "该药"
    generic = ident.get("generic_name")
    display = f"{name}（{generic}）" if generic and generic.lower() != str(name).lower() else str(name)
    indication_excerpt = compact_text(evidence.get("indications", {}).get("excerpt"))
    mechanism_excerpt = compact_text(evidence.get("mechanism", {}).get("excerpt"))
    return f"{display}：{summarize_indications_excerpt(indication_excerpt)} {summarize_mechanism_excerpt(mechanism_excerpt)}"

def comparator_from_record(record: dict[str, Any]) -> dict[str, Any]:
    ident = record.get("identity", {}); evidence = record.get("evidence_modules", {}); efficacy = evidence.get("efficacy", {})
    sources = record.get("sources", []) or []; metrics = []
    for metric in efficacy.get("metrics", []) or []:
        metrics.append({"name": metric.get("name"), "value": metric.get("value"), "comparison": metric.get("comparison"), "source": source_for_metric(metric, sources), "snippet": metric.get("snippet")})
    name = ident.get("brand_name") or ident.get("generic_name") or record.get("drug_id"); fit = record.get("_fit", {})
    return {
        "drug_id": record.get("drug_id"), "display_name": name, "generic_name": ident.get("generic_name"),
        "drug_class": ident.get("drug_class"), "cancer_context": ident.get("cancer_context", []), "biomarkers": ident.get("biomarkers", []),
        "fit": fit, "summary": concise_fda_description(record),
        "metrics": metrics, "sources": sources, "quality_flags": record.get("quality_flags", []),
    }


def database_match_summary(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for record in records[:5]:
        ident = record.get("identity", {})
        out.append({"drug_id": record.get("drug_id"), "brand_name": ident.get("brand_name"), "generic_name": ident.get("generic_name"), "drug_class": ident.get("drug_class"), "cancer_context": ident.get("cancer_context", []), "biomarkers": ident.get("biomarkers", []), "excluded_biomarkers": ident.get("excluded_biomarkers", [])})
    return out


def build_same_class_comparison(db: FdaEvidenceDatabase, context: dict[str, Any], direct_matches: list[dict[str, Any]]) -> dict[str, Any]:
    candidates = db.comparator_candidates(context, direct_matches, limit=5)
    comparators = [comparator_from_record(c) for c in candidates if c.get("evidence_modules", {}).get("efficacy", {}).get("metrics")]
    if comparators:
        return {"available": True, "method": "local_fda_oncology_database_match", "summary": "系统根据当前试验的癌种、biomarker/靶点、药物类别和药名，在本地 FDA 肿瘤药物证据库中选择同类参考。以下数据仅作同类参考，不代表当前试验药物疗效。", "comparators": comparators, "context_used": context}
    return {"available": False, "method": "local_fda_oncology_database_match", "summary": "本地 FDA 肿瘤药物证据库中未找到足够可靠且带可追溯疗效指标的同类对照。可能原因包括：当前试验药物仍处早期研发、机制无法稳定识别、FDA 标签没有同癌种/同 biomarker 场景，或标签中没有可解析 ORR/PFS/OS/DOR。", "comparators": [], "context_used": context}


def patient_readable_notes(core_drugs: list[dict[str, Any]], comparison_available: bool) -> list[str]:
    names = "、".join(d.get("name", "") for d in core_drugs if d.get("name")) or "核心研究药物"
    notes = [f"本模块围绕推荐试验页面中识别到的核心研究药物：{names}。", "厂家/申办方信息来自该推荐试验页面；FDA 药物信息来自本地 FDA 肿瘤药物证据库。", "同类药物对比用于帮助理解治疗方向，不能替代研究中心对当前试验药物的解释。"]
    if not comparison_available:
        notes.append("没有找到可靠同类 FDA 疗效对照时，报告不会补写或推断疗效数字。")
    return notes


def quality_flags(core_drugs: list[dict[str, Any]], background_drugs: list[dict[str, Any]], comparison: dict[str, Any], db_path: Path) -> list[str]:
    flags = []
    if not core_drugs:
        flags.append("未能从试验页面稳定识别核心研究药物，需人工复核干预措施。")
    if background_drugs:
        flags.append("联合/背景治疗药物未作为主要模块展开，避免报告冗余；完整干预措施仍可在 CT.gov 页面复核。")
    flags.append("申办方信息按 ClinicalTrials.gov 页面展示，不自动等同于药品上市许可持有人。")
    flags.append(f"FDA 同类疗效数据来自本地 FDA 肿瘤药物证据库：{db_path.name}。")
    if not comparison.get("available"):
        flags.append("未找到可靠同类 FDA 疗效对照；系统保留空结果，避免虚构疗效。")
    return flags


def extract_trial_drug_module(fetch: FetchResult, db: FdaEvidenceDatabase, patient: dict[str, Any]) -> dict[str, Any]:
    study = fetch.study; protocol = study.get("protocolSection", {})
    identification = protocol.get("identificationModule", {}); status = protocol.get("statusModule", {}); design = protocol.get("designModule", {})
    sponsor_module = protocol.get("sponsorCollaboratorsModule", {}); arms_module = protocol.get("armsInterventionsModule", {})
    conditions_module = protocol.get("conditionsModule", {}); eligibility = protocol.get("eligibilityModule", {})
    title = identification.get("briefTitle") or identification.get("officialTitle") or ""
    interventions = arms_module.get("interventions", []) or []; lead_sponsor = get_path(sponsor_module, ["leadSponsor", "name"], "页面未标明")
    collaborators = [c.get("name") for c in sponsor_module.get("collaborators", []) if c.get("name")]
    phases = design.get("phases", []) or []; conditions = conditions_module.get("conditions", []) or []
    core_drugs, background_drugs = split_core_and_background_drugs(title, interventions)
    context = infer_trial_context(study, core_drugs, patient)
    direct_matches: list[dict[str, Any]] = []
    for drug in core_drugs:
        direct_matches.extend(db.direct_matches(drug.get("name", "")))
    direct_matches = dedupe(direct_matches)
    comparison = build_same_class_comparison(db, context, direct_matches)
    return {
        "trial_id": fetch.nct_id, "source_url": fetch.source_url, "title": title, "official_title": identification.get("officialTitle"),
        "status": status.get("overallStatus"), "phase": phases, "conditions": conditions, "lead_sponsor": lead_sponsor, "collaborators": collaborators,
        "core_study_drugs": core_drugs, "combination_or_background_drugs": background_drugs,
        "eligibility_drug_context": summarize_eligibility_drug_context(eligibility.get("eligibilityCriteria", "")),
        "manufacturer": manufacturer_section(lead_sponsor, collaborators, fetch.source_url), "fda_database_matches": database_match_summary(direct_matches),
        "same_class_comparison": comparison, "patient_readable_notes": patient_readable_notes(core_drugs, comparison.get("available", False)),
        "quality_flags": quality_flags(core_drugs, background_drugs, comparison, db.path),
        "source_policy": "当前试验药物信息来自推荐试验的 ClinicalTrials.gov 页面/API；同类药物疗效对比来自本地 FDA 肿瘤药物证据库，不进行实时 openFDA 检索。",
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
    }


def build_modules(nct_ids: list[str], db: FdaEvidenceDatabase, patient: dict[str, Any], studies_by_id: dict[str, Any] | None = None, timeout: int = 25) -> dict[str, Any]:
    modules: dict[str, Any] = {}; errors: dict[str, str] = {}
    for nct_id in nct_ids:
        try:
            fetch = fetch_or_load_study(nct_id, studies_by_id, timeout=timeout)
            modules[nct_id] = extract_trial_drug_module(fetch, db, patient)
        except Exception as exc:
            errors[nct_id] = str(exc)
    return {"modules": modules, "errors": errors, "database": str(db.path), "generated_at": dt.datetime.now().isoformat(timespec="seconds")}


def patient_summary(patient: dict[str, Any]) -> dict[str, Any]:
    return {"patient_id": patient.get("patient_id"), "diagnosis": patient.get("diagnosis") or patient.get("cancer_type"), "stage": patient.get("stage"), "biomarkers": patient.get("biomarkers_known") or patient.get("biomarkers") or {}, "treatment_lines_completed": patient.get("treatment_lines_completed"), "ecog": patient.get("ECOG") or patient.get("ecog")}


def fallback_decision_report(patient: dict[str, Any], scored: Any, top_n: int = 3) -> dict[str, Any]:
    included = scored.get("included_trials", []) if isinstance(scored, dict) else list(scored)
    ranked = sorted(included, key=lambda t: (t.get("feasibility", {}).get("composite") is None, -(t.get("feasibility", {}).get("composite") or 0)))[:top_n]
    paths = []
    for idx, trial in enumerate(ranked, start=1):
        paths.append({"rank": idx, "trial_id": trial.get("id") or trial.get("trial_id"), "trial_title": trial.get("title"), "sponsor": trial.get("sponsor"), "phase": "/".join(trial.get("phases", [])) if isinstance(trial.get("phases"), list) else trial.get("phase"), "feasibility_score": trial.get("feasibility", {}).get("composite"), "rationale_one_liner": "该试验来自 ClinicalTrials.gov 检索结果，与患者癌种、分子特征或治疗线索相关；仍需医生/CRC 正式筛选。", "blockers_pending": ["确认当前队列仍开放且可接收该患者人群。", "复核 RECIST 1.1 可测量病灶、ECOG 和近期实验室检查。", "复核既往治疗和 washout 时间窗。"]})
    return {"generated_at": dt.datetime.now().isoformat(timespec="seconds"), "patient_summary": patient_summary(patient), "source_scope": "ClinicalTrials.gov only", "decision_paths": paths}


def load_decision_report(patient: dict[str, Any], scored: Any, path: Path | None) -> dict[str, Any]:
    if path and path.exists():
        return read_json(path)
    return fallback_decision_report(patient, scored)


def trial_url(nct_id: str) -> str:
    return f"https://clinicaltrials.gov/study/{nct_id}"


def render_sources(sources: list[dict[str, Any]]) -> str:
    if not sources:
        return ""
    items = "".join(f'<li><a href="{h(src.get("url"))}" target="_blank" rel="noopener">{h(src.get("label") or src.get("url"))}</a></li>' for src in sources)
    return f'<ul class="sources">{items}</ul>'


def render_core_drugs(module: dict[str, Any]) -> str:
    drugs = module.get("core_study_drugs", []) or []
    if not drugs:
        return '<p class="muted">未能从试验页面稳定识别核心研究药物，需人工复核。</p>'
    cards = []
    for drug in drugs:
        other = ", ".join(drug.get("other_names", [])) or "无"
        cards.append(f'''<div class="core-drug"><strong>{h(drug.get("name"))}</strong><span>{h(drug.get("type"))}</span><p><b>中文介绍：</b>{h(drug.get("intro_zh") or drug.get("description"))}</p><p><b>English:</b> {h(drug.get("intro_en") or drug.get("description"))}</p><small>别名：{h(other)}</small></div>''')
    return f'<div class="core-grid">{"".join(cards)}</div>'


def render_database_matches(module: dict[str, Any]) -> str:
    matches = module.get("fda_database_matches", []) or []
    if not matches:
        return '<p class="muted">核心研究药物未直接匹配到 FDA 本地数据库药品；系统会根据癌种、biomarker 和药物类别寻找同类参考。</p>'
    items = []
    for match in matches[:5]:
        items.append(f'<li>{h(match.get("brand_name") or match.get("generic_name") or match.get("drug_id"))} <span class="muted">{h(match.get("generic_name"))} | {h(match.get("drug_class"))}</span></li>')
    return f'<ul class="match-list">{"".join(items)}</ul>'


def render_metric_table(metrics: list[dict[str, Any]]) -> str:
    if not metrics:
        return '<p class="muted">暂无可引用的公开 ORR/PFS/OS/DOR 数字。</p>'
    rows = []
    for metric in metrics:
        src = metric.get("source", {}) or {}
        rows.append("<tr>" f"<td>{h(metric.get('name'))}</td>" f"<td>{h(metric.get('value'))}</td>" f"<td>{h(metric.get('comparison'))}</td>" f'<td><a href="{h(src.get("url"))}" target="_blank" rel="noopener">{h(src.get("label") or src.get("url") or "FDA 本地证据库")}</a></td>' "</tr>")
    return '<table class="metric-table"><thead><tr><th>指标</th><th>结果</th><th>比较方式/场景</th><th>来源</th></tr></thead>' f'<tbody>{"".join(rows)}</tbody></table>'


def render_comparators(module: dict[str, Any]) -> str:
    comparison = module.get("same_class_comparison", {}); comparators = comparison.get("comparators", []) or []
    if not comparators:
        return f'<p class="muted">{h(comparison.get("summary") or "未获得同类药物对照数据。")}</p>'
    blocks = []
    for comp in comparators:
        blocks.append(f'''<div class="comparator"><h5>{h(comp.get("display_name"))}</h5><p>{h(comp.get("summary"))}</p>{render_metric_table(comp.get("metrics", []))}{render_sources(comp.get("sources", []))}</div>''')
    return f'<p>{h(comparison.get("summary"))}</p>{"".join(blocks)}'


def render_drug_module(module: dict[str, Any]) -> str:
    manufacturer = module.get("manufacturer", {})
    notes = "".join(f"<li>{h(note)}</li>" for note in module.get("patient_readable_notes", []))
    flags = "".join(f"<li>{h(flag)}</li>" for flag in module.get("quality_flags", []))
    core_names = " + ".join(d.get("name", "") for d in module.get("core_study_drugs", []) if d.get("name")) or "核心研究药物"
    return f'''<section class="drug-card"><div class="drug-head"><h4>{h(core_names)}</h4><span>药品说明书式模块</span></div><h5>核心研究药物</h5>{render_core_drugs(module)}<h5>FDA 本地数据库匹配</h5>{render_database_matches(module)}<div><h5>厂家/申办方背景</h5><p><strong>{h(manufacturer.get("name"))}</strong></p><p>{h(manufacturer.get("background"))}</p>{render_sources(manufacturer.get("sources", []))}</div><h5>同类药物疗效对比</h5><div class="comparators">{render_comparators(module)}</div><h5>给患者看的解释</h5><ul>{notes}</ul><h5>质量提示</h5><ul>{flags}</ul><p class="source-policy">{h(module.get("source_policy"))}</p></section>'''


def render_report(patient: dict[str, Any], decision_report: dict[str, Any], dynamic: dict[str, Any]) -> str:
    ps = decision_report.get("patient_summary") or patient_summary(patient)
    modules = dynamic.get("modules", {}); errors = dynamic.get("errors", {})
    biomarkers = ps.get("biomarkers") if isinstance(ps.get("biomarkers"), dict) else {}
    biomarker_text = ", ".join(f"{k}: {v}" for k, v in biomarkers.items()) or patient_biomarker_text(patient) or "未结构化"
    path_blocks = []
    for path in decision_report.get("decision_paths", []):
        trial_id = path.get("trial_id"); pending = "".join(f"<li>{h(item)}</li>" for item in path.get("blockers_pending", []))
        module_html = render_drug_module(modules[trial_id]) if trial_id in modules else f'<div class="error">药品模块生成失败：{h(errors.get(trial_id, "未知错误"))}</div>'
        path_blocks.append(f'''<section class="trial-card"><div class="trial-head"><div><div class="rank">匹配路径 {h(path.get("rank"))}</div><h3>{h(path.get("trial_title"))}</h3></div><a class="nct" href="{trial_url(trial_id)}" target="_blank" rel="noopener">{h(trial_id)}</a></div><div class="trial-meta"><span>申办方：{h(path.get("sponsor"))}</span><span>分期：{h(path.get("phase"))}</span><span>可行性分：{h(path.get("feasibility_score"))}</span></div><p class="rationale">{h(path.get("rationale_one_liner") or path.get("rationale"))}</p><div class="pending"><h4>正式筛选前还要确认</h4><ul>{pending}</ul></div>{module_html}</section>''')
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8" /><meta name="viewport" content="width=device-width, initial-scale=1" /><title>ClinicalTrialSKILL 药品信息增强报告</title><style>:root{{--ink:#172026;--muted:#5c6975;--line:#d9e0e6;--bg:#f6f8fa;--card:#fff;--accent:#106c74;--accent2:#8b5a00;--soft:#e7f3f4}}*{{box-sizing:border-box}}body{{margin:0;font-family:"Microsoft YaHei","Noto Sans CJK SC",Arial,sans-serif;color:var(--ink);background:var(--bg);line-height:1.55}}header{{background:#0f2d33;color:white;padding:28px 38px}}header h1{{margin:0 0 8px;font-size:28px}}header p{{margin:0;color:#d4e4e6}}main{{max-width:1180px;margin:0 auto;padding:24px}}.summary,.trial-card,.drug-card{{background:var(--card);border:1px solid var(--line);border-radius:8px;box-shadow:0 1px 2px rgba(20,30,40,.04)}}.summary{{padding:18px 20px;display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:18px}}.summary div{{border-left:3px solid var(--accent);padding-left:10px}}.label{{font-size:12px;color:var(--muted)}}.value{{font-size:15px;font-weight:650}}.trial-card{{padding:22px;margin:18px 0}}.trial-head{{display:flex;justify-content:space-between;gap:16px;align-items:flex-start}}.trial-head h3{{margin:4px 0 0;font-size:22px;line-height:1.3}}.rank{{color:var(--accent);font-weight:700}}.nct{{white-space:nowrap;color:white;background:var(--accent);padding:7px 10px;border-radius:6px;text-decoration:none;font-weight:700}}.trial-meta{{display:flex;flex-wrap:wrap;gap:8px;margin:14px 0}}.trial-meta span{{background:#eef2f5;border:1px solid var(--line);border-radius:6px;padding:5px 8px;color:#2f3d48}}.rationale{{background:#fff8ea;border-left:4px solid var(--accent2);padding:10px 12px}}.pending{{background:#f8fafb;border:1px solid var(--line);border-radius:6px;padding:12px 14px;margin-bottom:16px}}h4,h5{{margin:14px 0 8px}}.drug-card{{padding:18px;margin:16px 0 4px;border-color:#b8d7da}}.drug-head{{display:flex;justify-content:space-between;gap:12px;align-items:center;padding-bottom:8px;border-bottom:1px solid var(--line)}}.drug-head h4{{font-size:21px;margin:0}}.drug-head span{{background:var(--soft);color:#0d5860;border-radius:6px;padding:5px 8px;font-size:12px;font-weight:700}}.core-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px}}.core-drug,.comparator{{border:1px solid var(--line);border-radius:6px;padding:12px;margin:10px 0;background:#fbfcfd}}.core-drug strong{{display:block;font-size:16px}}.core-drug span{{display:inline-block;color:#0d5860;background:var(--soft);border-radius:5px;padding:2px 6px;font-size:12px;margin:6px 0}}.core-drug p{{margin:4px 0}}.core-drug small,.muted,.source-line,.source-policy{{color:var(--muted);font-size:13px}}.metric-table{{width:100%;border-collapse:collapse;margin-top:8px;font-size:14px}}.metric-table th,.metric-table td{{border:1px solid var(--line);padding:7px;text-align:left;vertical-align:top}}.metric-table th{{background:#eef5f6}}.sources,.match-list{{padding-left:18px}}.source-policy{{border-top:1px solid var(--line);padding-top:10px}}.error{{background:#fff0f0;border:1px solid #e2b8b8;border-radius:6px;padding:12px;color:#7a1f1f}}a{{color:#0b6975}}footer{{max-width:1180px;margin:0 auto;padding:8px 24px 28px;color:var(--muted);font-size:13px}}@media(max-width:820px){{.summary{{grid-template-columns:1fr}}.trial-head,.drug-head{{flex-direction:column;align-items:flex-start}}}}</style></head><body><header><h1>ClinicalTrialSKILL 药品信息增强报告</h1><p>检索范围：ClinicalTrials.gov only | 当前试验信息来自 CT.gov | FDA 同类对照来自本地 FDA 肿瘤药物证据库 | 生成时间：{h(dt.datetime.now().isoformat(timespec="seconds"))}</p></header><main><section class="summary"><div><div class="label">患者</div><div class="value">{h(ps.get("patient_id"))}</div></div><div><div class="label">诊断/分期</div><div class="value">{h(ps.get("diagnosis"))} / {h(ps.get("stage"))}</div></div><div><div class="label">关键 biomarker</div><div class="value">{h(biomarker_text)}</div></div><div><div class="label">治疗线数/ECOG</div><div class="value">{h(ps.get("treatment_lines_completed"))} / {h(ps.get("ecog"))}</div></div></section>{"".join(path_blocks)}</main><footer>本报告用于医生/CRC 复核前的信息整理。当前推荐试验药物信息来自对应 CT.gov 页面；FDA 同类药物数据只作为参考，不等同于当前试验药物疗效。</footer></body></html>'''


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--patient", required=True, type=Path); parser.add_argument("--scored", required=True, type=Path); parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--decision-report", type=Path); parser.add_argument("--database", type=Path, default=DEFAULT_DB); parser.add_argument("--studies-json", type=Path)
    parser.add_argument("--html-name", default="dynamic_drug_report_zh.html"); parser.add_argument("--modules-name", default="dynamic_drug_modules_zh.json"); parser.add_argument("--timeout", default=25, type=int)
    args = parser.parse_args(); args.out_dir.mkdir(parents=True, exist_ok=True)
    patient = read_json(args.patient); scored = read_json(args.scored); decision_report = load_decision_report(patient, scored, args.decision_report)
    nct_ids = [p.get("trial_id") for p in decision_report.get("decision_paths", []) if p.get("trial_id")]
    db = FdaEvidenceDatabase(args.database); studies_by_id = read_json(args.studies_json) if args.studies_json else None
    dynamic = build_modules(nct_ids, db, patient, studies_by_id=studies_by_id, timeout=args.timeout)
    modules_path = args.out_dir / args.modules_name; report_path = args.out_dir / args.html_name
    write_json(modules_path, dynamic); report_path.write_text(render_report(patient, decision_report, dynamic), encoding="utf-8")
    print(json.dumps({"report": str(report_path), "modules": str(modules_path), "module_count": len(dynamic.get("modules", {})), "error_count": len(dynamic.get("errors", {})), "database": str(args.database)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()









