from __future__ import annotations

import re
from typing import Any


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


TARGET_PATTERNS = {
    "EGFR": ["egfr", "exon 19", "ex19del", "l858r", "t790m", "c797s", "osimertinib", "furmonertinib", "lazertinib", "amivantamab"],
    "MET": ["met amplification", "c-met", "capmatinib", "tepotinib", "vebreltinib", "savolitinib"],
    "KRAS_G12C": ["kras g12c", "g12c", "pankras", "pan-kras", "kras inhibitor", "sotorasib", "adagrasib", "divarasib", "glecirasib"],
    "HER2": ["her2", "erbb2", "trastuzumab", "pertuzumab", "t-dxd", "deruxtecan"],
    "BRCA_PARp": ["brca", "hrd", "parp", "olaparib", "niraparib", "rucaparib"],
    "PD1_PDL1": ["pd-1", "pd-l1", "pembrolizumab", "nivolumab", "cemiplimab", "atezolizumab", "durvalumab"],
    "ALK": ["alk", "alectinib", "lorlatinib", "brigatinib"],
    "ROS1": ["ros1", "entrectinib", "crizotinib", "repotrectinib"],
    "RET": ["ret", "selpercatinib", "pralsetinib"],
    "BRAF": ["braf", "v600e", "dabrafenib", "trametinib"],
    "VEGF": ["vegf", "bevacizumab", "ramucirumab"],
    "ADC": ["adc", "antibody drug conjugate", "deruxtecan", "sacituzumab", "datopotamab", "telisotuzumab", "brengitecan"],
}


DISEASE_PATTERNS = {
    "NSCLC": ["non-small cell lung", "nsclc", "lung adenocarcinoma", "lung cancer"],
    "CRC": ["colorectal", "colon cancer", "rectal cancer", "crc"],
    "BREAST": ["breast"],
    "OVARIAN": ["ovarian", "fallopian", "peritoneal"],
    "HCC": ["hepatocellular", "hcc", "liver cancer"],
    "PANCREATIC": ["pancreatic"],
    "SOLID_TUMOR": ["solid tumor", "solid tumors"],
}


FDA_COMPARATOR_CANDIDATES = {
    ("NSCLC", "EGFR"): ["TAGRISSO", "RYBREVANT", "GILOTRIF", "VIZIMPRO", "TARCEVA"],
    ("NSCLC", "MET"): ["TABRECTA", "TEPMETKO"],
    ("NSCLC", "ALK"): ["ALECENSA", "LORBRENA", "ALUNBRIG"],
    ("NSCLC", "ROS1"): ["ROZLYTREK", "XALKORI", "AUGTYRO"],
    ("NSCLC", "RET"): ["RETEVMO", "GAVRETO"],
    ("NSCLC", "BRAF"): ["TAFINLAR", "MEKINIST"],
    ("CRC", "KRAS_G12C"): ["LUMAKRAS", "KRAZATI"],
    ("NSCLC", "KRAS_G12C"): ["LUMAKRAS", "KRAZATI"],
    ("SOLID_TUMOR", "KRAS_G12C"): ["LUMAKRAS", "KRAZATI"],
    ("BREAST", "HER2"): ["ENHERTU", "HERCEPTIN", "PERJETA", "KADCYLA", "PHESGO"],
    ("BREAST", "ADC"): ["ENHERTU", "TRODELVY", "KADCYLA"],
    ("OVARIAN", "BRCA_PARp"): ["LYNPARZA", "ZEJULA", "RUBRACA"],
    ("CRC", "VEGF"): ["AVASTIN", "CYRAMZA"],
    ("SOLID_TUMOR", "PD1_PDL1"): ["KEYTRUDA", "OPDIVO", "LIBTAYO", "TECENTRIQ", "IMFINZI"],
}


def infer_profile(trial: dict[str, Any], core_drugs: list[dict[str, Any]]) -> dict[str, Any]:
    text = norm(
        " ".join(
            [
                trial.get("title", ""),
                " ".join(trial.get("conditions", []) or []),
                " ".join(drug.get("name", "") for drug in core_drugs),
                " ".join(drug.get("description", "") for drug in core_drugs),
                " ".join(" ".join(drug.get("other_names", []) or []) for drug in core_drugs),
            ]
        )
    )
    diseases = [name for name, terms in DISEASE_PATTERNS.items() if any(term in text for term in terms)]
    targets = [name for name, terms in TARGET_PATTERNS.items() if any(term in text for term in terms)]
    if "SOLID_TUMOR" not in diseases and not diseases and "solid tumor" in text:
        diseases.append("SOLID_TUMOR")
    return {
        "trial_id": trial.get("trial_id"),
        "title": trial.get("title", ""),
        "conditions": trial.get("conditions", []),
        "core_drugs": core_drugs,
        "diseases": diseases or ["UNKNOWN"],
        "targets": targets or ["UNKNOWN"],
        "search_text": text,
    }


def build_candidate_brand_names(profile: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for drug in profile.get("core_drugs", []):
        # Try the study drug first. If it is already FDA-approved, openFDA can
        # usually resolve either brand_name or generic_name.
        if drug.get("name"):
            names.append(drug["name"].split()[0])
    for disease in profile.get("diseases", []):
        for target in profile.get("targets", []):
            names.extend(FDA_COMPARATOR_CANDIDATES.get((disease, target), []))
    for target in profile.get("targets", []):
        names.extend(FDA_COMPARATOR_CANDIDATES.get(("SOLID_TUMOR", target), []))
    return list(dict.fromkeys(name for name in names if name))
