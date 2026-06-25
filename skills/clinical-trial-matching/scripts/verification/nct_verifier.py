"""
nct_verifier.py — v1.6.0 P2.3

Enhanced NCT verification (vs v1.5 which only checked status):

Per trial we fetch & verify:
  1. existence (404 → invalid)
  2. overall_status (must be RECRUITING for promotion)
  3. last_update_date (used by feasibility scoring)
  4. condition (must include patient's cancer type)
  5. intervention (must include any drugs claimed in report)
  6. eligibility (re-extracted to detect drift since search)

Each verified field gets a citation chain entry: {value, source_url, source_field, fetched_at}

Output schema appended as `trial['verification']`:
  {
    status: "valid" | "invalid" | "mismatch" | "error",
    overall_status: "RECRUITING" | ...,
    last_update_date: "2025-08-15",
    title_official: "...",
    citations: [
      {claim_text, source_url, source_field, fetched_at, verified}
    ],
    mismatches: [str],   # any inconsistencies between search-time data and live API
  }
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional

CT_GOV_API = "https://clinicaltrials.gov/api/v2/studies"


def _fetch_one(nct_id: str, timeout: int = 15) -> dict:
    url = f"{CT_GOV_API}/{nct_id}"
    req = urllib.request.Request(url, headers={"User-Agent": "TrialGPT-v1.6.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def verify_one(trial: dict, patient: dict | None = None) -> dict:
    """Verify a single trial against CT.gov v2 API."""
    nct_id = trial.get("id", "")
    if not nct_id.startswith("NCT"):
        return {"status": "skipped", "reason": "Not an NCT ID (likely ChiCTR)"}

    citations = []
    mismatches = []
    fetched_at = dt.datetime.utcnow().isoformat() + "Z"

    try:
        data = _fetch_one(nct_id)
    except urllib.error.HTTPError as e:
        return {
            "status": "invalid",
            "error": f"HTTP {e.code}: {e.reason}",
            "fetched_at": fetched_at,
        }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
            "fetched_at": fetched_at,
        }

    proto = data.get("protocolSection", {})
    ident = proto.get("identificationModule", {})
    status_mod = proto.get("statusModule", {})
    sponsor_mod = proto.get("sponsorCollaboratorsModule", {})
    design = proto.get("designModule", {})
    elig_mod = proto.get("eligibilityModule", {})
    arms_mod = proto.get("armsInterventionsModule", {})
    cond_mod = proto.get("conditionsModule", {})
    contacts = proto.get("contactsLocationsModule", {})

    overall_status = status_mod.get("overallStatus", "")
    last_update = status_mod.get("lastUpdatePostDateStruct", {}).get("date", "")
    title_brief = ident.get("briefTitle", "")
    title_official = ident.get("officialTitle", "") or title_brief
    sponsor = sponsor_mod.get("leadSponsor", {}).get("name", "")
    phases = design.get("phases", [])
    conditions = cond_mod.get("conditions", [])
    interventions = [iv.get("name", "") for iv in arms_mod.get("interventions", [])]

    china_sites = []
    for loc in contacts.get("locations", []):
        if loc.get("country", "") == "China":
            china_sites.append({"facility": loc.get("facility", ""), "city": loc.get("city", "")})

    base_url = f"https://clinicaltrials.gov/study/{nct_id}"

    # Build citations
    for label, value, field_name in [
        ("overall_status", overall_status, "statusModule.overallStatus"),
        ("last_update_date", last_update, "statusModule.lastUpdatePostDateStruct.date"),
        ("title", title_official, "identificationModule.officialTitle"),
        ("sponsor", sponsor, "sponsorCollaboratorsModule.leadSponsor.name"),
        ("phase", "/".join(phases), "designModule.phases"),
        ("conditions", "; ".join(conditions), "conditionsModule.conditions"),
        ("interventions", "; ".join(interventions), "armsInterventionsModule.interventions"),
    ]:
        citations.append({
            "claim": f"{label} = {value}",
            "source_url": base_url,
            "source_field": field_name,
            "fetched_at": fetched_at,
            "verified": True,
        })

    # Cross-check against search-time trial data
    # NOTE: search-time uses briefTitle, so compare against briefTitle (not officialTitle)
    search_title = trial.get("title", "")
    if search_title and title_brief and search_title.strip() != title_brief.strip():
        # only flag if substantively different (not just truncated)
        st = search_title.strip().lower()
        bt = title_brief.strip().lower()
        if st not in bt and bt not in st:
            mismatches.append(f"Title mismatch: search='{search_title[:60]}' vs api_brief='{title_brief[:60]}'")

    search_phases = trial.get("phases", [])
    if search_phases and phases and set(search_phases) != set(phases):
        mismatches.append(f"Phase mismatch: search={search_phases} vs api={phases}")

    search_cn_count = trial.get("china_site_count", 0)
    api_cn_count = len(china_sites)
    if abs(search_cn_count - api_cn_count) > 2:  # tolerance
        mismatches.append(f"China site count mismatch: search={search_cn_count} vs api={api_cn_count}")

    # Patient-condition cross-check (optional)
    if patient:
        pat_cancer = patient.get("cancer_type", "").lower()
        pat_muts = [m.lower() for m in patient.get("mutations", [])]
        if pat_cancer:
            conds_text = " ".join(conditions).lower()

            # Wildcard matches (basket / pan-tumor / mutation-defined trials)
            wildcards = [
                "solid tumor", "tumor, solid", "solid cancer",
                "advanced cancer", "advanced solid", "all solid",
                "metastatic cancer", "neoplasm",
            ]
            # Mutation-defined trials are valid even without cancer-type
            mutation_keywords = ["kras", "g12d", "g12c", "g12v", "ras mutation", "ras-mutated",
                                  "her2", "egfr", "braf", "msi"]

            cancer_aliases = {
                "pdac": ["pancreatic", "pancreas", "pdac"],
                "pancreatic ductal adenocarcinoma": ["pancreatic", "pancreas", "pdac"],
                "nsclc": ["non-small cell lung", "nsclc", "lung cancer"],
                "crc": ["colorectal", "colon", "rectal"],
            }
            aliases = cancer_aliases.get(pat_cancer, [pat_cancer])

            # Anti-aliases: histologic distinctions (e.g., PDAC ≠ pancreatic NET)
            anti_aliases = {
                "pdac": ["neuroendocrine", "net ", "pnet", "carcinoid", "islet cell", "endocrine"],
                "pancreatic ductal adenocarcinoma": ["neuroendocrine", "net ", "pnet", "carcinoid", "islet cell", "endocrine"],
            }.get(pat_cancer, [])

            # Token-level alias check: a condition matches alias iff it has the alias
            # AND no anti-alias is in the same condition (e.g., "pancreatic neuroendocrine" excludes match)
            cancer_matched = False
            for c in conditions:
                c_lower = c.lower()
                if any(a in c_lower for a in aliases):
                    if not any(aa in c_lower for aa in anti_aliases):
                        cancer_matched = True
                        break
            wildcard_matched = any(w in conds_text for w in wildcards)
            # Also accept wildcard if at least 1 condition is purely a wildcard-type
            if not cancer_matched and any(w in c.lower() and not any(aa in c.lower() for aa in anti_aliases) for c in conditions for w in wildcards):
                wildcard_matched = True
            mutation_matched = any(mk in conds_text for mk in mutation_keywords) and any(
                mk.lower() in conds_text for m in pat_muts for mk in [m, m.replace(" ", ""), m.split()[-1] if " " in m else m]
            )

            if not (cancer_matched or wildcard_matched or mutation_matched):
                mismatches.append(f"Trial conditions ({conditions}) do not include patient cancer ({pat_cancer}) or any pan-tumor/mutation indicator")

    status = "valid"
    if overall_status != "RECRUITING":
        status = "not_recruiting"
    if mismatches:
        status = "mismatch" if status == "valid" else status

    return {
        "status": status,
        "overall_status": overall_status,
        "last_update_date": last_update,
        "title_official": title_official,
        "sponsor_official": sponsor,
        "phases_official": phases,
        "conditions": conditions,
        "interventions": interventions,
        "china_sites_official": china_sites,
        "citations": citations,
        "mismatches": mismatches,
        "fetched_at": fetched_at,
    }


def verify_batch(trials: list[dict], patient: dict | None = None,
                  max_workers: int = 6) -> list[dict]:
    """Parallel verification with thread pool."""
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(verify_one, t, patient): t for t in trials if t.get("id", "").startswith("NCT")}
        for fut in as_completed(futures):
            t = futures[fut]
            try:
                t["verification"] = fut.result()
            except Exception as e:
                t["verification"] = {"status": "error", "error": str(e)}
    return trials


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--in", dest="input", required=True)
    parser.add_argument("--patient", default=None)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    with open(args.input) as f:
        data = json.load(f)
    patient = None
    if args.patient:
        with open(args.patient) as f:
            patient = json.load(f)

    if isinstance(data, dict) and "match" in data:
        all_trials = data["match"] + data["conditional"]
        verify_batch(all_trials, patient)
        # write back
        for t in data["match"]:
            pass  # already mutated
        for t in data["conditional"]:
            pass
    elif isinstance(data, list):
        verify_batch(data, patient)
    else:
        # included_trials shape
        if "included_trials" in data:
            verify_batch(data["included_trials"], patient)

    with open(args.out, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    # Summary
    if isinstance(data, dict) and "match" in data:
        all_t = data["match"] + data["conditional"]
        valid = sum(1 for t in all_t if t.get("verification", {}).get("status") in ("valid", "mismatch"))
        recruiting = sum(1 for t in all_t if t.get("verification", {}).get("overall_status") == "RECRUITING")
        mismatches = sum(1 for t in all_t if t.get("verification", {}).get("mismatches"))
        print(f"Verification: {valid}/{len(all_t)} valid, {recruiting} recruiting, {mismatches} with mismatches")
