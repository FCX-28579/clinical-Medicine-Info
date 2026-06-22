from __future__ import annotations

import re
from typing import Any


def _window(text: str, term: str, radius: int = 900) -> str:
    low = text.lower()
    i = low.find(term.lower())
    if i < 0:
        return text[:1800]
    start = max(0, i - radius)
    end = min(len(text), i + radius)
    return text[start:end]


def relevant_text(label_text: str, profile: dict[str, Any]) -> str:
    terms: list[str] = []
    for disease in profile.get("diseases", []):
        if disease == "NSCLC":
            terms.extend(["non-small cell lung", "NSCLC"])
        elif disease == "CRC":
            terms.extend(["colorectal", "colon"])
        elif disease == "BREAST":
            terms.append("breast")
        elif disease == "OVARIAN":
            terms.extend(["ovarian", "fallopian", "peritoneal"])
        elif disease == "HCC":
            terms.extend(["hepatocellular", "liver"])
    for target in profile.get("targets", []):
        if target == "EGFR":
            terms.extend(["EGFR", "T790M", "exon 19", "L858R"])
        elif target == "KRAS_G12C":
            terms.extend(["KRAS G12C", "G12C"])
        elif target == "HER2":
            terms.extend(["HER2", "ERBB2"])
        elif target == "BRCA_PARp":
            terms.extend(["BRCA", "HRD"])
        elif target == "MET":
            terms.extend(["MET", "exon 14", "amplification"])
    for term in terms:
        if term.lower() in label_text.lower():
            return _window(label_text, term)
    return label_text[:2200]


def parse_metrics_from_text(text: str, source: dict[str, str]) -> list[dict[str, Any]]:
    metrics: list[dict[str, Any]] = []
    patterns = [
        ("Median PFS", r"Median PFS in months[^0-9]{0,120}([0-9.]+)\s*\([^)]*\)\s*([0-9.]+)\s*\("),
        ("Median DFS", r"Median DFS in months[^0-9]{0,120}([0-9.]+)\s*\([^)]*\)\s*([0-9.]+)\s*\("),
        ("ORR", r"ORR,?\s*%\s*\([^)]*\)[^0-9]{0,80}([0-9.]+)\s*\([^)]*\)\s*([0-9.]+)\s*\("),
        ("ORR", r"Objective Response Rate\s*\([^)]*95%\s*CI[^)]*\)[^0-9]{0,80}([0-9.]+)\s*\("),
        ("Median PFS", r"median (?:progression-free survival|PFS)[^.;:]*?(?:was|of)?\s*([0-9.]+)\s*months(?:[^.;]*?(?:versus|vs|compared with|and)\s*([0-9.]+)\s*months)?"),
        ("Median DFS", r"median (?:disease-free survival|DFS)[^.;:]*?(?:was|of)?\s*([0-9.]+)\s*months(?:[^.;]*?(?:versus|vs|compared with|and)\s*([0-9.]+)\s*months)?"),
        ("Median OS", r"median (?:overall survival|OS)[^.;:]*?(?:was|of)?\s*([0-9.]+)\s*months(?:[^.;]*?(?:versus|vs|compared with|and)\s*([0-9.]+)\s*months)?"),
        ("ORR", r"(?:overall response rate|objective response rate|ORR)[^.;]{0,120}?(?:was|were|of|:)\s*([0-9.]+)\s*%"),
        ("Median DOR", r"Duration of Response\s+Median Estimate[^.;]{0,160}?,\s*months\s*\([^)]*\)\s*([0-9.]+)\s*\("),
        ("Median DOR", r"Duration of Response\s+Median Estimate[^.;]{0,160}?in months\s*\(95%\s*CI\)\s*([0-9.]+)\s*\("),
        ("Median DOR", r"Duration of Response\s+Median Estimate[^.;]{0,120}?(?:in\s+months|months)[^0-9]{0,80}([0-9.]+)\s*\("),
        ("Median DOR", r"median (?:duration of response|DOR)[^.;:]*?(?:was|of)?\s*([0-9.]+)\s*months"),
    ]
    for name, pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.I | re.S):
            if name in {"Median PFS", "Median DFS", "Median OS"} and match.lastindex and match.lastindex >= 2 and match.group(2):
                value = f"{match.group(1)} months vs {match.group(2)} months"
            elif name == "ORR":
                value = f"{match.group(1)}%"
            else:
                value = f"{match.group(1)} months"
            context = re.sub(r"\s+", " ", match.group(0))[:500]
            if not _is_plausible_metric(name, value, context):
                continue
            metrics.append(
                {
                    "name": name,
                    "value": value,
                    "comparison": "FDA label clinical studies section",
                    "source": source,
                    "snippet": context,
                }
            )
            break
    return metrics


def _is_plausible_metric(name: str, value: str, context: str) -> bool:
    low = context.lower()
    if name == "ORR":
        try:
            percent = float(value.rstrip("%"))
        except ValueError:
            return False
        if percent < 0 or percent > 100:
            return False
        if percent == 95 and "95% ci" in low:
            return False
        return True
    if name.startswith("Median "):
        numbers = [float(n) for n in re.findall(r"[0-9]+(?:\.[0-9]+)?", value)]
        return bool(numbers) and all(0 < n < 240 for n in numbers)
    return True
