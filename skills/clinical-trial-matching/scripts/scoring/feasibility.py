"""
feasibility.py — v1.6.0 P0.2

5-dimension feasibility score for each trial × patient pairing:

  1. recruiting_status     (weight 0.25): RECRUITING + recently updated → 1.0
                                          ACTIVE_NOT_RECRUITING → 0.3
                                          stale > 18 months → progressive decay
  2. geographic_access     (weight 0.20): China sites count & travel cost
  3. time_cost             (weight 0.20): screening + manufacture + cooldown
  4. financial_cost        (weight 0.15): patient affordability_tier × trial route
  5. slot_availability     (weight 0.20): inferred from update freshness + cohort note

Composite = Σ (weight_i × score_i)
Anti-pattern: any dimension < 0.30 demotes trial out of Decision Report Top N
              (still kept in Match List).

The scorer reads `patient.affordability_tier` ∈ {"low","medium","high"} so the
financial dimension is context-aware (proposal section 1, P0.2 调整建议).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field
from typing import Optional


WEIGHTS = {
    "recruiting_status": 0.25,
    "geographic_access": 0.20,
    "time_cost":         0.20,
    "financial_cost":    0.15,
    "slot_availability": 0.20,
}


@dataclass
class FeasibilityScore:
    composite: float = 0.0
    sub_scores: dict[str, float] = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)
    promote_to_decision_report: bool = True


# ---------------------------------------------------------------------------
# 1. Recruiting status (0-1)
# ---------------------------------------------------------------------------
def score_recruiting_status(trial: dict) -> tuple[float, list[str]]:
    flags = []
    status = trial.get("overall_status") or trial.get("verification", {}).get("overall_status", "")
    last_update = trial.get("last_update_date") or trial.get("verification", {}).get("last_update_date", "")

    if status == "RECRUITING":
        base = 1.0
    elif status == "ACTIVE_NOT_RECRUITING":
        base = 0.3
        flags.append("Trial not actively recruiting")
    elif status in ("ENROLLING_BY_INVITATION",):
        base = 0.5
        flags.append("Enrollment by invitation only")
    else:
        base = 0.1
        flags.append(f"Trial status not RECRUITING ({status})")

    # Decay if stale
    if last_update:
        try:
            last = dt.datetime.fromisoformat(last_update.replace("Z", "+00:00")).replace(tzinfo=None)
            months_old = (dt.datetime.utcnow() - last).days / 30.0
            if months_old > 18:
                base *= 0.5
                flags.append(f"Last update {months_old:.0f} months ago (stale)")
            elif months_old > 12:
                base *= 0.7
                flags.append(f"Last update {months_old:.0f} months ago (somewhat stale)")
        except Exception:
            pass

    return min(1.0, max(0.0, base)), flags


# ---------------------------------------------------------------------------
# 2. Geographic access (0-1)
# ---------------------------------------------------------------------------
def score_geographic_access(trial: dict, patient: dict) -> tuple[float, list[str]]:
    flags = []
    cn_sites = trial.get("china_site_count", 0)
    pat_country = patient.get("country", "China")
    willing_travel = patient.get("willing_to_travel_internationally", False)

    if pat_country == "China":
        if cn_sites >= 10:
            return 1.0, []
        elif cn_sites >= 3:
            return 0.85, []
        elif cn_sites == 1 or cn_sites == 2:
            flags.append(f"{cn_sites} China site(s) — limited slots")
            return 0.7, flags
        else:
            # No China sites
            if willing_travel:
                flags.append("No China sites; international travel required")
                return 0.5, flags
            else:
                flags.append("No China sites; patient not willing to travel")
                return 0.15, flags
    else:
        # Patient outside China — different logic; default to high if any sites
        return 0.85, []


# ---------------------------------------------------------------------------
# 3. Time cost (0-1)
# ---------------------------------------------------------------------------
def score_time_cost(trial: dict, patient: dict) -> tuple[float, list[str]]:
    """
    Lower is worse. Scoring:
      - Standard small-molecule trial: ~2 weeks screening → 0.95
      - Cell therapy (TCR-T / CAR-T / TIL): 4-8 weeks manufacture → 0.50
      - International trial requiring relocation: 0.40
      - Drug requires 4-week washout + screen: 0.85
    """
    flags = []
    interventions = " ".join(trial.get("interventions", [])).lower()
    title = trial.get("title", "").lower()

    is_cell_therapy = any(k in (interventions + " " + title)
                           for k in ["car-t", "car t", "tcr", "til ", "tils", "tcr-t", "engineered t cell",
                                     "cell injection", "t cell therapy", "celullar"])
    is_overseas = trial.get("china_site_count", 0) == 0 and patient.get("country") == "China"

    base = 0.9
    if is_cell_therapy:
        base = 0.5
        flags.append("Cell therapy: 4-8 week manufacture window")
    if is_overseas:
        base *= 0.7
        flags.append("Overseas: relocation + visa adds 4+ weeks")

    # If patient just finished chemo (washout requirement)
    if patient.get("treatment_lines_completed", 0) >= 1:
        flags.append("Recent chemo: 4-week washout typical")
        base *= 0.95

    return min(1.0, max(0.0, base)), flags


# ---------------------------------------------------------------------------
# 4. Financial cost (0-1)
# ---------------------------------------------------------------------------
AFFORDABILITY_TIERS = {
    "low":    {"domestic": 1.0, "domestic_travel": 0.8, "overseas": 0.05},
    "medium": {"domestic": 1.0, "domestic_travel": 1.0, "overseas": 0.4},
    "high":   {"domestic": 1.0, "domestic_travel": 1.0, "overseas": 0.85},
}


def score_financial_cost(trial: dict, patient: dict) -> tuple[float, list[str]]:
    flags = []
    tier = patient.get("affordability_tier", "medium")
    pat_country = patient.get("country", "China")
    cn_sites = trial.get("china_site_count", 0)
    sites = trial.get("china_sites", [])

    pat_city = patient.get("city", "")
    same_city_sites = sum(1 for s in sites if pat_city and pat_city in s.get("city", ""))

    weights = AFFORDABILITY_TIERS.get(tier, AFFORDABILITY_TIERS["medium"])

    if cn_sites == 0 and pat_country == "China":
        score = weights["overseas"]
        flags.append(f"Overseas trial, affordability_tier={tier}")
    elif same_city_sites > 0:
        score = weights["domestic"]
    elif cn_sites > 0:
        score = weights["domestic_travel"]
        flags.append("Domestic but cross-city travel")
    else:
        score = weights["domestic"]

    return score, flags


# ---------------------------------------------------------------------------
# 5. Slot availability (0-1)
# ---------------------------------------------------------------------------
def score_slot_availability(trial: dict) -> tuple[float, list[str]]:
    """
    Without ground-truth enrollment data, infer from:
      - Recent registration / update → likely open
      - Multiple sites with different roles (multi-cohort) → likely open
      - Phase 3 mature → may be filling fast
    Default to "unknown" → 0.6 (mid-low confidence).
    """
    flags = []
    last_update = trial.get("last_update_date") or trial.get("verification", {}).get("last_update_date", "")
    cn_sites = trial.get("china_site_count", 0)

    if last_update:
        try:
            last = dt.datetime.fromisoformat(last_update.replace("Z", "+00:00")).replace(tzinfo=None)
            months_old = (dt.datetime.utcnow() - last).days / 30.0
            if months_old < 3:
                base = 0.85
            elif months_old < 6:
                base = 0.7
            elif months_old < 12:
                base = 0.55
            else:
                base = 0.4
                flags.append(f"Updated {months_old:.0f} months ago — slot availability unknown")
        except Exception:
            base = 0.6
            flags.append("Slot availability unknown (no update_date)")
    else:
        base = 0.6
        flags.append("Slot availability unknown")

    # multi-site adjustment — many sites suggest active recruitment infrastructure
    if cn_sites >= 10:
        base = min(1.0, base + 0.10)

    return base, flags


# ---------------------------------------------------------------------------
# Composite
# ---------------------------------------------------------------------------
def compute_feasibility(trial: dict, patient: dict) -> FeasibilityScore:
    fs = FeasibilityScore()

    rs, f1 = score_recruiting_status(trial)
    ga, f2 = score_geographic_access(trial, patient)
    tc, f3 = score_time_cost(trial, patient)
    fc, f4 = score_financial_cost(trial, patient)
    sa, f5 = score_slot_availability(trial)

    fs.sub_scores = {
        "recruiting_status": round(rs, 3),
        "geographic_access": round(ga, 3),
        "time_cost":         round(tc, 3),
        "financial_cost":    round(fc, 3),
        "slot_availability": round(sa, 3),
    }
    fs.flags = f1 + f2 + f3 + f4 + f5
    fs.composite = round(sum(WEIGHTS[k] * v for k, v in fs.sub_scores.items()), 3)

    # Anti-pattern: any dim < 0.3 → demote
    if any(v < 0.30 for v in fs.sub_scores.values()):
        fs.promote_to_decision_report = False
        fs.flags.append("Demoted from Decision Report Top N: at least one dimension < 0.30")
    return fs


def score_all(trials: list[dict], patient: dict) -> list[dict]:
    for t in trials:
        if "error" in t:
            continue
        fs = compute_feasibility(t, patient)
        t["feasibility"] = asdict(fs)
    return trials


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse, json
    parser = argparse.ArgumentParser()
    parser.add_argument("--in", dest="input", required=True)
    parser.add_argument("--patient", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    with open(args.input) as f:
        data = json.load(f)
    with open(args.patient) as f:
        patient = json.load(f)

    if isinstance(data, dict) and "match" in data:
        data["match"] = score_all(data["match"], patient)
        data["conditional"] = score_all(data["conditional"], patient)
        data["exclude"] = score_all(data["exclude"], patient)
    elif isinstance(data, dict) and "included_trials" in data:
        data["included_trials"] = score_all(data["included_trials"], patient)
    elif isinstance(data, list):
        data = score_all(data, patient)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    # Quick summary
    if isinstance(data, dict) and "match" in data:
        match_promoted = sum(1 for t in data["match"] if t.get("feasibility", {}).get("promote_to_decision_report", False))
        print(f"Feasibility scored: {len(data['match'])} match ({match_promoted} promoted to Decision Report)")
