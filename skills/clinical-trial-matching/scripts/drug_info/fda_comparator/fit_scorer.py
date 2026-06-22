from __future__ import annotations

from typing import Any


def score_label_fit(label_text: str, profile: dict[str, Any]) -> dict[str, Any]:
    text = label_text.lower()
    disease_hits = [d for d in profile.get("diseases", []) if _disease_match(d, text)]
    target_hits = [t for t in profile.get("targets", []) if _target_match(t, text)]
    score = 0
    score += 35 if disease_hits else 0
    score += 35 if target_hits else 0
    score += 15 if "clinical studies" in text or "14 clinical studies" in text else 0
    score += 15 if any(term in text for term in ["progression-free survival", "overall response rate", "objective response rate", "duration of response", "overall survival"]) else 0
    if score >= 80:
        level = "direct_or_near_comparator"
    elif score >= 55:
        level = "class_reference"
    elif score >= 35:
        level = "weak_reference"
    else:
        level = "not_suitable"
    return {
        "score": score,
        "level": level,
        "disease_hits": disease_hits,
        "target_hits": target_hits,
    }


def _disease_match(disease: str, text: str) -> bool:
    terms = {
        "NSCLC": ["non-small cell lung", "nsclc"],
        "CRC": ["colorectal", "colon", "rectal"],
        "BREAST": ["breast"],
        "OVARIAN": ["ovarian", "fallopian", "peritoneal"],
        "HCC": ["hepatocellular", "liver"],
        "SOLID_TUMOR": ["solid tumor"],
    }.get(disease, [])
    return any(term in text for term in terms)


def _target_match(target: str, text: str) -> bool:
    terms = {
        "EGFR": ["egfr", "t790m", "exon 19", "l858r"],
        "MET": ["met exon", "met amplification", "c-met"],
        "KRAS_G12C": ["kras g12c", "g12c"],
        "HER2": ["her2", "erbb2"],
        "BRCA_PARp": ["brca", "hrd", "parp"],
        "PD1_PDL1": ["pd-1", "pd-l1", "programmed death"],
        "ALK": ["alk"],
        "ROS1": ["ros1"],
        "RET": ["ret"],
        "BRAF": ["braf", "v600e"],
        "VEGF": ["vegf"],
        "ADC": ["antibody-drug conjugate", "antibody drug conjugate"],
    }.get(target, [])
    return any(term in text for term in terms)
