from __future__ import annotations

import argparse
import datetime as dt
import gzip
import io
import hashlib
import json
import re
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Iterable

OPENFDA_LABEL = "https://api.fda.gov/drug/label.json"
OPENFDA_DOWNLOAD = "https://api.fda.gov/download.json"
SCHEMA_VERSION = "1.2"

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)

ONCOLOGY_TERMS = [
    "cancer", "tumor", "tumour", "neoplasm", "carcinoma", "sarcoma",
    "leukemia", "lymphoma", "melanoma", "myeloma", "glioma", "glioblastoma",
    "metastatic", "malignancy", "malignant", "oncology", "antineoplastic",
    "NSCLC", "non-small cell", "colorectal", "breast cancer", "ovarian cancer",
    "hepatocellular", "pancreatic cancer", "prostate cancer", "gastric cancer",
    "renal cell", "urothelial", "cervical cancer", "endometrial cancer",
]

BIOMARKER_PATTERNS = {
    "KRAS G12C": r"\bKRAS\s+G12C\b|\bG12C\b",
    "EGFR": r"\bEGFR\b|exon 19|L858R|T790M",
    "ALK": r"\bALK\b",
    "ROS1": r"\bROS1\b",
    "RET": r"\bRET\b",
    "BRAF": r"\bBRAF\b|V600E",
    "HER2": r"\bHER2\b|\bERBB2\b",
    "MET": r"\bMET\b|c-MET|MET exon",
    "NTRK": r"\bNTRK[123]?\b|TRK fusion",
    "BRCA": r"\bBRCA1?\b|\bBRCA2?\b",
    "HRD": r"\bHRD\b|homologous recombination deficiency",
    "MSI": r"\bMSI-H\b|microsatellite instability",
    "MMR": r"\bdMMR\b|mismatch repair",
    "PD-L1": r"PD-L1",
    "PD-1": r"PD-1",
}

OBVIOUS_NON_ONCOLOGY_PRODUCT_TERMS = [
    "sunscreen", "suncreen", "spf", "sunburn", "lip balm", "foundation",
    "moisturizer", "moisturising", "zinc oxide", "titanium dioxide",
    "avobenzone", "octisalate", "octocrylene", "homosalate", "octinoxate",
]

ONCOLOGY_DISEASE_TERMS = [
    "cancer", "tumor", "tumour", "neoplasm", "carcinoma", "sarcoma",
    "leukemia", "lymphoma", "melanoma", "myeloma", "glioma", "glioblastoma",
    "malignancy", "malignant", "nsclc", "non-small cell", "small cell lung",
    "colorectal", "breast", "ovarian", "hepatocellular", "pancreatic",
    "prostate", "gastric", "renal cell", "urothelial", "cervical", "endometrial",
    "mantle cell", "acute lymphoblastic", "multiple myeloma",
]

ONCOLOGY_TREATMENT_TERMS = [
    "treatment of", "indicated for", "patients with", "for adult patients with",
    "for pediatric patients with", "metastatic", "unresectable", "relapsed",
    "refractory", "adjuvant", "neoadjuvant", "maintenance treatment",
]

ONCOLOGY_CLASS_TERMS = [
    "antineoplastic", "kinase inhibitor", "immune checkpoint", "pd-1", "pd-l1",
    "ctla-4", "car t", "chimeric antigen receptor", "monoclonal antibody",
    "antibody-drug conjugate", "parp inhibitor", "alk inhibitor", "egfr inhibitor",
    "her2", "braf inhibitor", "mek inhibitor", "proteasome inhibitor",
    "cd19-directed", "bcma-directed", "nucleoside metabolic inhibitor",
]

NON_ONCOLOGY_INDICATION_TERMS = [
    "hypertension", "menopause", "vasomotor symptoms", "sedation", "anxiolysis",
    "amnesia", "diagnostic", "endoscopic procedures", "radiologic procedures",
    "bronchoscopy", "gastroscopy", "cystoscopy", "pain and fever",
    "duodenal ulcer", "gastric ulcer", "gastroesophageal reflux", "gerd",
    "benign prostatic hyperplasia", "bph", "tuberculosis", "triglyceride",
    "hyperuricemia", "allergic states", "asthma", "atopic dermatitis",
    "local or regional anesthesia", "hypothyroidism", "constipation",
]

BIOMARKER_EXCLUSION_TERMS = [
    "negative", "no known", "without", "wild-type", "wild type", "absence of",
    "not have", "who have no", "excluding", "except", "other than",
]

BIOMARKER_POSITIVE_TERMS = [
    "positive", "mutated", "mutation", "mutant", "fusion", "rearrangement",
    "amplification", "overexpression", "expressing", "exon", "deficient",
]

GENERIC_NAME_STOPWORDS = {
    "indications", "usage", "description", "tablets", "tablet", "capsules",
    "capsule", "injection", "solution", "suspension", "for", "usp", "and",
}

DRUG_NAME_SALT_WORDS = {
    "sodium", "disodium", "hydrochloride", "hcl", "phosphate", "medoxomil",
    "besylate", "acetate", "citrate", "succinate", "potassium", "calcium",
}

CANCER_CONTEXT_TERMS = {
    "NSCLC": ["NSCLC", "non-small cell lung"],
    "CRC": ["colorectal", "colon cancer", "rectal cancer"],
    "BREAST": ["breast cancer"],
    "OVARIAN": ["ovarian", "fallopian", "primary peritoneal"],
    "HCC": ["hepatocellular"],
    "PANCREATIC": ["pancreatic cancer"],
    "PROSTATE": ["prostate cancer"],
    "GASTRIC": ["gastric cancer", "gastroesophageal cancer", "gastroesophageal junction"],
    "UROTHELIAL": ["urothelial", "bladder cancer"],
    "RENAL_CELL": ["renal cell carcinoma"],
    "MELANOMA": ["melanoma"],
    "LEUKEMIA": ["leukemia", "acute myeloid"],
    "LYMPHOMA": ["lymphoma"],
    "MYELOMA": ["myeloma"],
    "SOLID_TUMOR": ["solid tumor", "solid tumour"],
}


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")


def first(value: Any) -> str | None:
    if isinstance(value, list) and value:
        return str(value[0])
    if isinstance(value, str) and value:
        return value
    return None


def list_value(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value if v is not None]
    if value:
        return [str(value)]
    return []


def as_text(label: dict[str, Any], fields: list[str]) -> str:
    parts: list[str] = []
    for field in fields:
        parts.extend(list_value(label.get(field)))
    return normalize_space(" ".join(parts))


def label_search_text(label: dict[str, Any]) -> str:
    openfda = label.get("openfda", {}) if isinstance(label.get("openfda"), dict) else {}
    fields = [
        "indications_and_usage", "clinical_studies", "clinical_pharmacology",
        "description", "purpose", "mechanism_of_action",
    ]
    parts = [as_text(label, fields)]
    for key in ["pharm_class_epc", "pharm_class_moa", "pharm_class_cs", "brand_name", "generic_name", "substance_name"]:
        parts.extend(list_value(openfda.get(key)))
    return normalize_space(" ".join(parts))


def clean_drug_name(value: str | None) -> str | None:
    if not value:
        return None
    value = normalize_space(re.sub(r"\b(?:USP|HCL|HYDROCHLORIDE)\b[,.]?", " ", value, flags=re.I))
    value = re.sub(r"\s+(?:tablets?|capsules?|injections?|solution|suspension|for injection).*", "", value, flags=re.I)
    value = normalize_space(value.strip(" :-,;()[]"))
    if not value or value.lower() in GENERIC_NAME_STOPWORDS or UUID_RE.match(value):
        return None
    if len(value) > 90:
        return None
    return value


def first_openfda_value(label: dict[str, Any], key: str) -> str | None:
    openfda = label.get("openfda", {}) if isinstance(label.get("openfda"), dict) else {}
    return clean_drug_name(first(openfda.get(key)))


def fallback_identity_from_package_label(label: dict[str, Any]) -> tuple[str | None, str | None]:
    text = as_text(label, ["package_label_principal_display_panel"])
    if not text:
        return None, None
    match = re.search(r"\bDRUG:\s*(.*?)\s+GENERIC:\s*(.*?)\s+DOSAGE:", text, flags=re.I)
    if not match:
        return None, None
    return clean_drug_name(match.group(1)), clean_drug_name(match.group(2))


def fallback_identity_from_spl(label: dict[str, Any]) -> tuple[str | None, str | None]:
    text = as_text(label, ["spl_product_data_elements"])
    if not text:
        return None, None
    words = re.findall(r"[A-Za-z][A-Za-z0-9'-]*", text)
    kept: list[str] = []
    for word in words[:8]:
        if word.lower() in GENERIC_NAME_STOPWORDS:
            break
        if len(word) <= 2:
            break
        if word.isupper() and kept:
            break
        kept.append(word)
        if len(kept) >= 4:
            break
    if not kept:
        return None, None
    if len(kept) >= 2 and kept[0].lower() == kept[1].lower():
        return clean_drug_name(kept[0]), clean_drug_name(kept[1])
    if len(kept) >= 3 and kept[1].lower() in DRUG_NAME_SALT_WORDS and kept[2].lower() == kept[0].lower():
        salt_name = clean_drug_name(" ".join(kept[:2]))
        base_name = clean_drug_name(kept[2])
        return salt_name, base_name
    if len(kept) >= 2 and kept[1].lower() not in DRUG_NAME_SALT_WORDS:
        return clean_drug_name(kept[0]), clean_drug_name(kept[1])
    name = clean_drug_name(" ".join(kept[:2]))
    return name, name


def fallback_identity_from_description(label: dict[str, Any]) -> tuple[str | None, str | None]:
    text = as_text(label, ["description"])
    if not text:
        return None, None
    text = re.sub(r"^\s*\d+(?:\.\d+)?\s+DESCRIPTION\s+", "", text, flags=re.I)
    patterns = [
        r"^([A-Z][A-Za-z0-9' -]{2,90}?)(?:,\s*USP)?\s+(?:is|are|contains|has)",
        r"^([A-Z][A-Za-z0-9' -]{2,90}?)(?: tablets?| capsules?| injection| solution)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            name = clean_drug_name(match.group(1))
            return name, name
    return None, None


def fallback_identity_from_label(label: dict[str, Any]) -> tuple[str | None, str | None]:
    brand, generic = fallback_identity_from_package_label(label)
    if brand or generic:
        return brand, generic
    brand, generic = fallback_identity_from_spl(label)
    if brand or generic:
        return brand, generic
    return fallback_identity_from_description(label)


def normalized_identity_value(value: str | None) -> str | None:
    value = clean_drug_name(value)
    if not value:
        return None
    return normalize_space(re.sub(r"[^a-z0-9]+", " ", value.lower()))


def indication_hash(label: dict[str, Any]) -> str | None:
    text = normalize_space(as_text(label, ["indications_and_usage"]).lower())
    if not text:
        return None
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def dedupe_keys_for_label(label: dict[str, Any]) -> list[str]:
    brand = first_openfda_value(label, "brand_name")
    generic = first_openfda_value(label, "generic_name")
    if not brand and not generic:
        brand, generic = fallback_identity_from_label(label)
    keys: list[str] = []
    for prefix, value in [("generic", generic), ("brand", brand)]:
        normalized = normalized_identity_value(value)
        if normalized:
            keys.append(f"{prefix}:{normalized}")
    set_id = label.get("set_id")
    if set_id:
        keys.append(f"set:{str(set_id).lower()}")
    ihash = indication_hash(label)
    if ihash:
        keys.append(f"ind:{ihash}")
    return keys


def has_any_term(text: str, terms: list[str]) -> bool:
    return any(term.lower() in text for term in terms)


def is_oncology_related(label: dict[str, Any]) -> bool:
    text = label_search_text(label).lower()
    if not has_any_term(text, ONCOLOGY_TERMS):
        return False
    if is_obvious_non_oncology_product(label, text):
        return False

    indications = as_text(label, ["indications_and_usage", "purpose"]).lower()
    clinical = as_text(label, ["clinical_studies"]).lower()
    mechanism = as_text(label, ["clinical_pharmacology", "description", "mechanism_of_action"]).lower()
    openfda = label.get("openfda", {}) if isinstance(label.get("openfda"), dict) else {}
    class_text = " ".join(
        list_value(openfda.get("pharm_class_epc"))
        + list_value(openfda.get("pharm_class_moa"))
        + list_value(openfda.get("pharm_class_cs"))
    ).lower()

    if has_any_term(class_text, ONCOLOGY_CLASS_TERMS):
        return True

    if has_any_term(indications, NON_ONCOLOGY_INDICATION_TERMS):
        return False

    indication_has_disease = has_any_term(indications, ONCOLOGY_DISEASE_TERMS)
    indication_has_treatment = has_any_term(indications, ONCOLOGY_TREATMENT_TERMS)
    indication_is_clearly_non_oncology = has_any_term(indications, NON_ONCOLOGY_INDICATION_TERMS) and not has_any_term(class_text + " " + mechanism, ONCOLOGY_CLASS_TERMS)
    if indication_has_disease and indication_has_treatment and not indication_is_clearly_non_oncology:
        return True

    clinical_has_oncology_endpoint = has_any_term(clinical, ONCOLOGY_DISEASE_TERMS) and has_any_term(clinical, [
        "overall response", "objective response", "duration of response",
        "progression-free", "event-free survival", "overall survival",
        "tumor response", "tumour response", "complete response", "partial response",
    ])
    if clinical_has_oncology_endpoint:
        return True

    mechanism_has_oncology_action = has_any_term(mechanism, ONCOLOGY_CLASS_TERMS) and has_any_term(mechanism + " " + indications, ONCOLOGY_DISEASE_TERMS)
    if mechanism_has_oncology_action:
        return True

    return False


def is_obvious_non_oncology_product(label: dict[str, Any], lowered_text: str | None = None) -> bool:
    text = lowered_text or label_search_text(label).lower()
    openfda = label.get("openfda", {}) if isinstance(label.get("openfda"), dict) else {}
    product_type = " ".join(list_value(openfda.get("product_type"))).lower()
    pharm = " ".join(list_value(openfda.get("pharm_class_epc")) + list_value(openfda.get("pharm_class_moa"))).lower()
    has_non_oncology_product_signal = any(term in text for term in OBVIOUS_NON_ONCOLOGY_PRODUCT_TERMS)
    has_antineoplastic_signal = "antineoplastic" in pharm or "antineoplastic" in text
    has_treatment_signal = any(phrase in text for phrase in [
        "treatment of", "indicated for the treatment", "patients with",
        "metastatic", "unresectable", "refractory", "relapsed", "adjuvant treatment",
    ])
    if has_non_oncology_product_signal and not has_antineoplastic_signal and not has_treatment_signal:
        return True
    if "otc" in product_type and has_non_oncology_product_signal and not has_antineoplastic_signal:
        return True
    return False


def infer_cancer_context(text: str) -> list[str]:
    low = text.lower()
    contexts = []
    for context, terms in CANCER_CONTEXT_TERMS.items():
        if any(term.lower() in low for term in terms):
            contexts.append(context)
    if not contexts and any(term in low for term in ["cancer", "tumor", "tumour", "neoplasm", "carcinoma", "malignant"]):
        contexts.append("ONCOLOGY_UNSPECIFIED")
    return contexts


def biomarker_contexts(text: str, pattern: str, window: int = 120) -> Iterable[str]:
    for match in re.finditer(pattern, text, flags=re.I):
        start = max(0, match.start() - window)
        end = min(len(text), match.end() + window)
        yield text[start:end]


def is_positive_biomarker_context(name: str, low: str) -> bool:
    if name == "KRAS G12C":
        return "kras g12c" in low
    if name == "EGFR":
        return has_any_term(low, ["egfr exon", "egfr t790m", "egfr mutation", "egfr-mutated", "egfr-mutant", "l858r", "exon 19"])
    if name == "ALK":
        return re.search(r"\balk\b.{0,60}(?:fusion|rearrangement|positive|mutation|alteration)|(?:fusion|rearrangement|positive|mutation|alteration).{0,60}\balk\b", low) is not None
    if name == "ROS1":
        return re.search(r"\bros1\b.{0,60}(?:fusion|rearrangement|positive)|(?:fusion|rearrangement|positive).{0,60}\bros1\b", low) is not None
    if name == "RET":
        return re.search(r"\bret\b.{0,60}(?:fusion|rearrangement|positive|mutation)|(?:fusion|rearrangement|positive|mutation).{0,60}\bret\b", low) is not None
    if name == "BRAF":
        return has_any_term(low, ["braf v600e", "v600e", "braf mutation", "braf-mutated"])
    if name == "HER2":
        return re.search(r"(?:\bher2\b|\berbb2\b).{0,80}(?:positive|overexpress|amplification|amplified|mutation|mutated)|(?:positive|overexpress|amplification|amplified|mutation|mutated).{0,80}(?:\bher2\b|\berbb2\b)", low) is not None
    if name == "MET":
        return re.search(r"\bmet\b.{0,60}(?:exon|amplification|amplified|positive|mutation|alteration)|(?:exon|amplification|amplified|positive|mutation|alteration).{0,60}\bmet\b", low) is not None
    if name == "NTRK":
        return "ntrk" in low and has_any_term(low, ["fusion", "rearrangement", "positive"])
    if name == "BRCA":
        return "brca" in low and has_any_term(low, ["mutation", "mutated", "deleterious", "positive"])
    if name == "HRD":
        return "hrd" in low or "homologous recombination deficiency" in low
    if name == "MSI":
        return "msi-h" in low or "microsatellite instability-high" in low
    if name == "MMR":
        return "dmmr" in low or "mismatch repair deficient" in low or "mismatch repair deficiency" in low
    if name == "PD-L1":
        return "pd-l1" in low and has_any_term(low, ["expressing", "expression", "positive", "tps", "cps"])
    if name == "PD-1":
        return False
    return has_any_term(low, BIOMARKER_POSITIVE_TERMS)


def infer_biomarker_roles(label: dict[str, Any]) -> tuple[list[str], list[str]]:
    scoped_text = as_text(label, [
        "indications_and_usage", "patient_selection", "clinical_pharmacology",
        "mechanism_of_action", "description",
    ])
    positive: list[str] = []
    excluded: list[str] = []
    for name, pattern in BIOMARKER_PATTERNS.items():
        for context in biomarker_contexts(scoped_text, pattern):
            low = context.lower()
            has_exclusion = has_any_term(low, BIOMARKER_EXCLUSION_TERMS) or re.search(r"\bno\b", low) is not None
            has_positive = is_positive_biomarker_context(name, low)
            if has_exclusion and not has_positive:
                if name not in excluded:
                    excluded.append(name)
            elif has_positive and name not in positive:
                positive.append(name)
    excluded = [name for name in excluded if name not in positive]
    return positive, excluded


def infer_biomarkers(text: str) -> list[str]:
    return [name for name, pattern in BIOMARKER_PATTERNS.items() if re.search(pattern, text, flags=re.I)]


def infer_drug_class(label: dict[str, Any], mechanism_text: str) -> str | None:
    openfda = label.get("openfda", {}) if isinstance(label.get("openfda"), dict) else {}
    for key in ["pharm_class_epc", "pharm_class_moa", "pharm_class_cs"]:
        value = first(openfda.get(key))
        if value:
            return value
    low = mechanism_text.lower()
    if re.search(r"\bkras\s+g12c\b.{0,80}\binhibitor\b|\binhibitor\b.{0,80}\bkras\s+g12c\b", low):
        return "KRAS G12C inhibitor or KRAS G12C-directed therapy"
    if re.search(r"\begfr\b.{0,80}\binhibitor\b|\binhibitor\b.{0,80}\begfr\b", low):
        return "EGFR-directed therapy"
    if re.search(r"(?:\bher2\b|\berbb2\b).{0,80}(?:inhibitor|antibody|directed)|(?:inhibitor|antibody|directed).{0,80}(?:\bher2\b|\berbb2\b)", low):
        return "HER2-directed therapy"
    if re.search(r"\bparp\b.{0,80}\binhibitor\b|\binhibitor\b.{0,80}\bparp\b", low):
        return "PARP inhibitor"
    if re.search(r"\bpd-?1\b|\bpd-l1\b", low) and has_any_term(low, ["antibody", "blocking", "inhibitor"]):
        return "PD-1/PD-L1 immunotherapy"
    if "antineoplastic" in low:
        return "Antineoplastic agent"
    return None


def source_for_label(label: dict[str, Any], fallback_name: str = "") -> dict[str, str]:
    openfda = label.get("openfda", {}) if isinstance(label.get("openfda"), dict) else {}
    brand = first(openfda.get("brand_name")) or fallback_name
    set_id = label.get("set_id")
    if set_id:
        query = f'set_id:"{set_id}"'
        url = OPENFDA_LABEL + "?" + urllib.parse.urlencode({"search": query, "limit": "1"})
    elif brand:
        query = f'openfda.brand_name:"{brand}"'
        url = OPENFDA_LABEL + "?" + urllib.parse.urlencode({"search": query, "limit": "1"})
    else:
        url = OPENFDA_LABEL
    return {"label": f"openFDA label: {brand or set_id or 'unknown'}", "url": url}


def fetch_url_bytes(url: str, timeout: int = 60, retries: int = 3) -> bytes:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(url, headers={"User-Agent": "ClinicalTrialSKILL-FDAEvidenceBuilder/1.2"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(min(2 * attempt, 10))
    assert last_error is not None
    raise last_error


def fetch_json(url: str, timeout: int = 60) -> dict[str, Any]:
    return json.loads(fetch_url_bytes(url, timeout=timeout).decode("utf-8"))


def fetch_openfda_label(seed: dict[str, Any], timeout: int = 30) -> tuple[dict[str, Any] | None, dict[str, str]]:
    for field, value in [("brand_name", seed.get("brand_name")), ("generic_name", seed.get("generic_name"))]:
        clean = re.sub(r"[^A-Za-z0-9 -]", " ", str(value or "")).strip()
        if not clean:
            continue
        query = f'openfda.{field}:"{clean}"'
        url = OPENFDA_LABEL + "?" + urllib.parse.urlencode({"search": query, "limit": "1"})
        try:
            data = fetch_json(url, timeout=timeout)
        except Exception:
            continue
        results = data.get("results") or []
        if results:
            return results[0], {"label": f"openFDA label: {seed.get('brand_name') or clean}", "url": url, "query_field": field}
    return None, {}


def parse_metric_candidates(text: str, source: dict[str, str]) -> list[dict[str, Any]]:
    patterns = [
        ("ORR", r"ORR,?\s*%\s*\([^)]*\)[^0-9]{0,80}([0-9.]+)\s*\([^)]*\)"),
        ("ORR", r"Objective Response Rate\s*\([^)]*95%\s*CI[^)]*\)[^0-9]{0,80}([0-9.]+)\s*\("),
        ("ORR", r"(?:overall response rate|objective response rate|ORR)[^.;]{0,120}?(?:was|were|of|:)\s*([0-9.]+)\s*%"),
        ("Median PFS", r"Median PFS in months[^0-9]{0,120}([0-9.]+)\s*\([^)]*\)\s*([0-9.]+)\s*\("),
        ("Median PFS", r"median (?:progression-free survival|PFS)[^.;:]*?(?:was|of)?\s*([0-9.]+)\s*months(?:[^.;]*?(?:versus|vs|compared with|and)\s*([0-9.]+)\s*months)?"),
        ("Median DFS", r"Median DFS in months[^0-9]{0,120}([0-9.]+)\s*\([^)]*\)\s*([0-9.]+)\s*\("),
        ("Median DFS", r"median (?:disease-free survival|DFS)[^.;:]*?(?:was|of)?\s*([0-9.]+)\s*months(?:[^.;]*?(?:versus|vs|compared with|and)\s*([0-9.]+)\s*months)?"),
        ("Median OS", r"median (?:overall survival|OS)[^.;:]*?(?:was|of)?\s*([0-9.]+)\s*months(?:[^.;]*?(?:versus|vs|compared with|and)\s*([0-9.]+)\s*months)?"),
        ("Median DOR", r"Duration of Response\s+Median Estimate[^.;]{0,160}?,\s*months\s*\([^)]*\)\s*([0-9.]+)\s*\("),
        ("Median DOR", r"Duration of Response\s+Median Estimate[^.;]{0,160}?in months\s*\(95%\s*CI\)\s*([0-9.]+)\s*\("),
        ("Median DOR", r"median (?:duration of response|DOR)[^.;:]*?(?:was|of)?\s*([0-9.]+)\s*months"),
    ]
    metrics: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for name, pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.I | re.S):
            if name.startswith("Median ") and match.lastindex and match.lastindex >= 2 and match.group(2):
                value = f"{match.group(1)} months vs {match.group(2)} months"
            elif name == "ORR":
                value = f"{match.group(1)}%"
            else:
                value = f"{match.group(1)} months"
            snippet = normalize_space(match.group(0))[:600]
            if not plausible_metric(name, value, snippet):
                continue
            key = (name, value)
            if key in seen:
                continue
            metrics.append({"name": name, "value": value, "comparison": "FDA label clinical studies section", "source": source, "snippet": snippet})
            seen.add(key)
            break
    return metrics


def plausible_metric(name: str, value: str, context: str) -> bool:
    low = context.lower()
    non_tumor_response_terms = [
        "serum calcium", "corrected calcium", "hypercalcemia", "nausea", "vomiting",
        "antiemetic", "emesis", "pain score", "blood pressure", "triglyceride",
    ]
    if has_any_term(low, non_tumor_response_terms):
        return False
    if name == "ORR":
        try:
            percent = float(value.rstrip("%"))
        except ValueError:
            return False
        has_response_endpoint = has_any_term(low, ["objective response", "overall response", "orr"])
        return 0 <= percent <= 100 and has_response_endpoint and not (percent == 95 and "95% ci" in low)
    if name.startswith("Median "):
        numbers = [float(n) for n in re.findall(r"[0-9]+(?:\.[0-9]+)?", value)]
        endpoint = name.replace("Median ", "").lower()
        endpoint_context = {
            "pfs": ["progression-free survival", "pfs"],
            "dfs": ["disease-free survival", "dfs"],
            "os": ["overall survival", " os"],
            "dor": ["duration of response", "dor"],
        }
        return bool(numbers) and all(0 < n < 240 for n in numbers) and has_any_term(low, endpoint_context.get(endpoint, []))
    return True


def readable_sources(seed: dict[str, Any]) -> list[dict[str, str]]:
    sources = []
    for item in seed.get("readable_fda_urls") or []:
        if item.get("url"):
            sources.append({"label": item.get("label") or "FDA approval page", "url": item["url"], "context": item.get("context", ""), "source_type": "FDA human-readable approval page"})
    return sources


def seed_from_label(label: dict[str, Any]) -> dict[str, Any]:
    brand = first_openfda_value(label, "brand_name")
    generic = first_openfda_value(label, "generic_name")
    if not brand and not generic:
        brand, generic = fallback_identity_from_label(label)
    mechanism_text = as_text(label, ["clinical_pharmacology", "mechanism_of_action", "description"])
    context_text = as_text(label, ["indications_and_usage", "clinical_studies"])
    positive_biomarkers, excluded_biomarkers = infer_biomarker_roles(label)
    return {
        "brand_name": brand,
        "generic_name": generic,
        "drug_class": infer_drug_class(label, mechanism_text),
        "cancer_context": infer_cancer_context(context_text),
        "biomarkers": positive_biomarkers,
        "excluded_biomarkers": excluded_biomarkers,
        "readable_fda_urls": [],
    }


def build_identity(seed: dict[str, Any], label: dict[str, Any]) -> dict[str, Any]:
    openfda = label.get("openfda", {}) if label else {}
    brand = first_openfda_value(label, "brand_name") or seed.get("brand_name")
    generic = first_openfda_value(label, "generic_name") or seed.get("generic_name")
    if label and not brand and not generic:
        brand, generic = fallback_identity_from_label(label)
    drug_id_source = brand or generic or label.get("set_id") or "unknown"
    return {
        "drug_id": slug(str(drug_id_source)),
        "brand_name": brand,
        "generic_name": generic,
        "manufacturer_name": first(openfda.get("manufacturer_name")),
        "substance_name": first(openfda.get("substance_name")),
        "product_type": first(openfda.get("product_type")),
        "route": openfda.get("route") or [],
        "drug_class": seed.get("drug_class"),
        "cancer_context": seed.get("cancer_context") or [],
        "biomarkers": seed.get("biomarkers") or [],
        "excluded_biomarkers": seed.get("excluded_biomarkers") or [],
        "set_id": label.get("set_id"),
        "application_number": first(openfda.get("application_number")),
    }


def summarize_indications(text: str) -> dict[str, Any]:
    return {"available": bool(text), "summary_zh": "FDA 标签适应症摘要来自 indications_and_usage 字段，需结合具体癌种、治疗线和 biomarker 复核。", "excerpt": text[:1800]}


def summarize_mechanism(text: str, seed: dict[str, Any]) -> dict[str, Any]:
    drug_class = seed.get("drug_class") or "未分类药物"
    if text:
        summary = f"该药在本地库中标注为 {drug_class}；机制摘要以 FDA label 的 clinical_pharmacology/description 字段为依据。"
        certainty = "label_text_available"
    else:
        summary = f"该药在本地库中标注为 {drug_class}；openFDA label 未提供足够机制文本。"
        certainty = "seed_or_inferred_class_only"
    return {"summary_zh": summary, "excerpt": text[:1400], "certainty": certainty}


def quality_flags(label: dict[str, Any] | None, metrics: list[dict[str, Any]], seed: dict[str, Any], discovery_mode: str) -> list[str]:
    flags = []
    if not label:
        flags.append("openFDA 未找到该药物 label；未生成疗效模块。")
    if label and not metrics:
        flags.append("openFDA label 存在，但未稳定解析到 ORR/PFS/OS/DOR 等疗效指标。")
    if discovery_mode == "bulk_openfda":
        flags.append("该记录来自 openFDA 全量 label 的高召回肿瘤相关性初筛，已剔除明显非肿瘤适应症，后续仍需人工复核。")
    if not seed.get("readable_fda_urls"):
        flags.append("未提供已验证的人类可读 FDA 审批页面，默认使用 openFDA API 来源。")
    flags.append("FDA 药物数据仅作本地证据库参考，不代表任何当前推荐试验药物疗效。")
    return flags


def build_drug_module_from_label(label: dict[str, Any], seed: dict[str, Any] | None = None, source: dict[str, str] | None = None, discovery_mode: str = "seed") -> dict[str, Any]:
    seed = seed or seed_from_label(label)
    source = source or source_for_label(label, seed.get("brand_name") or seed.get("generic_name") or "")
    identity = build_identity(seed, label)
    indications = as_text(label, ["indications_and_usage"])
    mechanism_text = as_text(label, ["clinical_pharmacology", "description"])
    clinical_text = as_text(label, ["clinical_studies"])
    metrics = parse_metric_candidates(clinical_text, source) if clinical_text else []
    sources = [source] + readable_sources(seed)
    return {
        "drug_id": identity["drug_id"],
        "identity": identity,
        "evidence_modules": {
            "indications": summarize_indications(indications),
            "mechanism": summarize_mechanism(mechanism_text, seed),
            "efficacy": {"available": bool(metrics), "metrics": metrics, "summary_zh": "已从 FDA label clinical studies 字段提取可复核疗效指标。" if metrics else "FDA label 存在，但未稳定解析到疗效数字。"},
        },
        "sources": sources,
        "quality_flags": quality_flags(label, metrics, seed, discovery_mode),
    }


def build_drug_module(seed: dict[str, Any], timeout: int = 30) -> dict[str, Any]:
    label, source = fetch_openfda_label(seed, timeout=timeout)
    if not label:
        identity = build_identity(seed, {})
        return {"drug_id": identity["drug_id"], "identity": identity, "evidence_modules": {"indications": {"available": False, "summary_zh": "openFDA 未找到该药物 label。", "excerpt": ""}, "mechanism": summarize_mechanism("", seed), "efficacy": {"available": False, "metrics": [], "summary_zh": "未生成 FDA 疗效数据。"}}, "sources": readable_sources(seed), "quality_flags": quality_flags(None, [], seed, "seed")}
    return build_drug_module_from_label(label, seed=seed, source=source, discovery_mode="seed")


def fetch_openfda_download_manifest(timeout: int = 60) -> list[dict[str, Any]]:
    manifest = fetch_json(OPENFDA_DOWNLOAD, timeout=timeout)
    label_info = manifest.get("results", {}).get("drug", {}).get("label", {})
    return label_info.get("partitions") or []


def load_partition_labels(file_url: str, timeout: int = 120) -> Iterable[dict[str, Any]]:
    payload = fetch_url_bytes(file_url, timeout=timeout, retries=3)
    if file_url.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(payload)) as zf:
            for name in zf.namelist():
                if name.endswith(".json"):
                    data = json.loads(zf.read(name).decode("utf-8"))
                    for item in data.get("results") or []:
                        yield item
    elif file_url.endswith(".gz"):
        data = json.loads(gzip.decompress(payload).decode("utf-8"))
        for item in data.get("results") or []:
            yield item
    else:
        data = json.loads(payload.decode("utf-8"))
        for item in data.get("results") or []:
            yield item


def iter_bulk_oncology_labels(timeout: int = 120, max_partitions: int | None = None, max_records: int | None = None) -> Iterable[dict[str, Any]]:
    seen: set[str] = set()
    yielded = 0
    partitions = fetch_openfda_download_manifest(timeout=timeout)
    if max_partitions is not None:
        partitions = partitions[:max_partitions]
    for partition in partitions:
        file_url = partition.get("file")
        if not file_url:
            continue
        for label in load_partition_labels(file_url, timeout=timeout):
            if not is_oncology_related(label):
                continue
            keys = dedupe_keys_for_label(label)
            if not keys or any(key in seen for key in keys):
                continue
            seen.update(keys)
            yielded += 1
            yield label
            if max_records is not None and yielded >= max_records:
                return


def _query_value(term: str) -> str:
    if " " in term or "-" in term:
        return f'"{term}"'
    return term


def iter_query_oncology_labels(timeout: int = 60, per_query: int = 100, max_records: int | None = None) -> Iterable[dict[str, Any]]:
    """Fast high-recall oncology discovery through openFDA search queries.

    This does not replace --bulk-openfda for a full offline corpus scan. It is a
    practical refresh mode that searches broad oncology terms across high-yield
    label fields and keeps only records that still pass the local oncology filter.
    """
    seen: set[str] = set()
    yielded = 0
    fields = ["indications_and_usage", "clinical_studies", "description", "openfda.pharm_class_epc"]
    terms = [
        "cancer", "carcinoma", "neoplasm", "tumor", "leukemia", "lymphoma",
        "melanoma", "myeloma", "sarcoma", "glioblastoma", "metastatic",
        "antineoplastic", "NSCLC", "colorectal", "breast cancer", "ovarian cancer",
        "hepatocellular", "pancreatic cancer", "prostate cancer", "urothelial",
    ]
    page_size = min(max(per_query, 1), 100)
    for term in terms:
        for field in fields:
            fetched_for_query = 0
            skip = 0
            while fetched_for_query < per_query:
                limit = min(page_size, per_query - fetched_for_query)
                query = f"{field}:{_query_value(term)}"
                url = OPENFDA_LABEL + "?" + urllib.parse.urlencode({"search": query, "limit": str(limit), "skip": str(skip)})
                try:
                    data = fetch_json(url, timeout=timeout)
                except Exception:
                    break
                results = data.get("results") or []
                if not results:
                    break
                for label in results:
                    if not is_oncology_related(label):
                        continue
                    keys = dedupe_keys_for_label(label)
                    if not keys or any(key in seen for key in keys):
                        continue
                    seen.update(keys)
                    yielded += 1
                    yield label
                    if max_records is not None and yielded >= max_records:
                        return
                fetched_for_query += len(results)
                if len(results) < limit:
                    break
                skip += len(results)


def build_index(drugs: list[dict[str, Any]]) -> dict[str, dict[str, list[str]]]:
    index: dict[str, dict[str, list[str]]] = {"by_brand": {}, "by_generic": {}, "by_biomarker": {}, "by_cancer_context": {}, "by_drug_class": {}}
    for drug in drugs:
        identity = drug.get("identity", {})
        drug_id = drug["drug_id"]
        add_index(index["by_brand"], identity.get("brand_name"), drug_id)
        add_index(index["by_generic"], identity.get("generic_name"), drug_id)
        add_index(index["by_drug_class"], identity.get("drug_class"), drug_id)
        for biomarker in identity.get("biomarkers") or []:
            add_index(index["by_biomarker"], biomarker, drug_id)
        for context in identity.get("cancer_context") or []:
            add_index(index["by_cancer_context"], context, drug_id)
    return index


def add_index(bucket: dict[str, list[str]], key: Any, drug_id: str) -> None:
    if not key:
        return
    normalized = str(key).upper()
    bucket.setdefault(normalized, [])
    if drug_id not in bucket[normalized]:
        bucket[normalized].append(drug_id)


def build_database(seed_items: list[dict[str, Any]], timeout: int = 30, pause_seconds: float = 0.2) -> dict[str, Any]:
    drugs = []
    for seed in seed_items:
        drugs.append(build_drug_module(seed, timeout=timeout))
        if pause_seconds > 0:
            time.sleep(pause_seconds)
    return database_payload(drugs, discovery_mode="seed")


def build_bulk_oncology_database(timeout: int = 120, max_partitions: int | None = None, max_records: int | None = None) -> dict[str, Any]:
    drugs = [build_drug_module_from_label(label, discovery_mode="bulk_openfda") for label in iter_bulk_oncology_labels(timeout=timeout, max_partitions=max_partitions, max_records=max_records)]
    return database_payload(drugs, discovery_mode="bulk_openfda")


def build_bulk_oncology_database_streaming(out_path: Path, timeout: int = 120, max_partitions: int | None = None, max_records: int | None = None) -> dict[str, Any]:
    partitions = fetch_openfda_download_manifest(timeout=timeout)
    if max_partitions is not None:
        partitions = partitions[:max_partitions]
    drugs: list[dict[str, Any]] = []
    seen: set[str] = set()
    total = len(partitions)
    yielded = 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    for idx, partition in enumerate(partitions, start=1):
        file_url = partition.get("file")
        if not file_url:
            continue
        print(json.dumps({"event": "partition_start", "partition": idx, "total_partitions": total, "file": file_url, "current_drug_count": len(drugs)}, ensure_ascii=False), flush=True)
        partition_kept = 0
        for label in load_partition_labels(file_url, timeout=timeout):
            if not is_oncology_related(label):
                continue
            keys = dedupe_keys_for_label(label)
            if not keys or any(key in seen for key in keys):
                continue
            seen.update(keys)
            yielded += 1
            partition_kept += 1
            drugs.append(build_drug_module_from_label(label, discovery_mode="bulk_openfda"))
            if max_records is not None and yielded >= max_records:
                db = database_payload(drugs, discovery_mode="bulk_openfda")
                db["bulk_progress"] = {"completed": True, "processed_partitions": idx, "total_partitions": total, "last_partition_file": file_url, "stopped_by_limit": True}
                write_database(out_path, db)
                print(json.dumps({"event": "bulk_complete", "drug_count": db["drug_count"], "out": str(out_path)}, ensure_ascii=False), flush=True)
                return db
        db = database_payload(drugs, discovery_mode="bulk_openfda")
        db["bulk_progress"] = {"completed": idx == total, "processed_partitions": idx, "total_partitions": total, "last_partition_file": file_url, "last_partition_kept": partition_kept, "stopped_by_limit": False}
        write_database(out_path, db)
        print(json.dumps({"event": "partition_done", "partition": idx, "kept": partition_kept, "drug_count": db["drug_count"], "out": str(out_path)}, ensure_ascii=False), flush=True)
    return db


def write_database(out_path: Path, db: dict[str, Any]) -> None:
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(out_path)


def build_query_oncology_database(timeout: int = 60, per_query: int = 100, max_records: int | None = None) -> dict[str, Any]:
    drugs = [build_drug_module_from_label(label, discovery_mode="query_openfda") for label in iter_query_oncology_labels(timeout=timeout, per_query=per_query, max_records=max_records)]
    return database_payload(drugs, discovery_mode="query_openfda")


def database_payload(drugs: list[dict[str, Any]], discovery_mode: str) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "generated_at": dt.datetime.now().isoformat(timespec="seconds"), "source": "openFDA drug label API", "discovery_mode": discovery_mode, "filter_policy": "High-recall oncology retention with identity fallback, scoped drug-class inference, scoped biomarker extraction, partition-safe deduplication, and clinical-studies-only efficacy parsing.", "drug_count": len(drugs), "drugs": drugs, "index": build_index(drugs)}


def default_paths() -> tuple[Path, Path]:
    root = Path(__file__).resolve().parents[1]
    return root / "data" / "seed_oncology_drugs.json", root / "data" / "fda_drug_evidence_db.json"


def parse_args(argv: list[str]) -> argparse.Namespace:
    seed_default, out_default = default_paths()
    parser = argparse.ArgumentParser(description="Build a local FDA oncology drug evidence database from openFDA labels.")
    parser.add_argument("--seed", type=Path, default=seed_default)
    parser.add_argument("--out", type=Path, default=out_default)
    parser.add_argument("--bulk-openfda", action="store_true", help="Scan openFDA bulk drug label downloads and keep broad oncology-related records.")
    parser.add_argument("--discover-openfda", action="store_true", help="Fast oncology discovery through broad openFDA search queries. Use this for development or lightweight refreshes.")
    parser.add_argument("--limit", type=int, default=None, help="Seed mode: limit seed rows. Discovery modes: max oncology records to keep.")
    parser.add_argument("--max-partitions", type=int, default=None, help="Bulk mode development guardrail: scan only first N openFDA download partitions.")
    parser.add_argument("--per-query", type=int, default=100, help="Query discovery mode: max records to fetch per term/field query.")
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--pause-seconds", type=float, default=0.2)
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if args.bulk_openfda:
        db = build_bulk_oncology_database_streaming(args.out, timeout=args.timeout, max_partitions=args.max_partitions, max_records=args.limit)
        print(json.dumps({"out": str(args.out), "drug_count": db["drug_count"], "discovery_mode": db["discovery_mode"]}, ensure_ascii=False, indent=2))
        return 0
    elif args.discover_openfda:
        db = build_query_oncology_database(timeout=args.timeout, per_query=args.per_query, max_records=args.limit)
    else:
        seed_items = json.loads(args.seed.read_text(encoding="utf-8"))
        if args.limit is not None:
            seed_items = seed_items[: args.limit]
        db = build_database(seed_items, timeout=args.timeout, pause_seconds=args.pause_seconds)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    write_database(args.out, db)
    print(json.dumps({"out": str(args.out), "drug_count": db["drug_count"], "discovery_mode": db["discovery_mode"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

