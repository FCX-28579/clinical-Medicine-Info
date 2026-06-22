from __future__ import annotations

import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dynamic_ctgov_drug_info import extract_trial_drug_module, split_core_and_combination_drugs
from dynamic_ctgov_drug_info import FetchResult


def fake_fetch(nct_id: str, title: str, interventions: list[dict], has_results: bool = False) -> FetchResult:
    return FetchResult(
        nct_id=nct_id,
        source_url=f"https://clinicaltrials.gov/study/{nct_id}",
        study={
            "hasResults": has_results,
            "protocolSection": {
                "identificationModule": {"briefTitle": title, "officialTitle": title},
                "statusModule": {"overallStatus": "RECRUITING"},
                "designModule": {"phases": ["PHASE1", "PHASE2"]},
                "sponsorCollaboratorsModule": {"leadSponsor": {"name": "Example Sponsor"}},
                "conditionsModule": {"conditions": ["Colorectal Cancer"]},
                "armsInterventionsModule": {"interventions": interventions, "armGroups": []},
                "eligibilityModule": {"eligibilityCriteria": "Inclusion: KRAS G12C mutation; ECOG 0-1; measurable disease."},
                "outcomesModule": {},
            },
        },
    )


class DynamicCtgovDrugInfoTests(unittest.TestCase):
    def test_core_drug_comes_from_trial_title(self) -> None:
        interventions = [
            {"type": "DRUG", "name": "D3S-001", "description": "Oral"},
            {"type": "DRUG", "name": "Cetuximab", "description": "Intravenous"},
        ]
        core, background = split_core_and_combination_drugs(
            "A Study of D3S-001 Monotherapy or Combination Therapy",
            interventions,
        )
        self.assertEqual([d["name"] for d in core], ["D3S-001"])
        self.assertEqual([d["name"] for d in background], ["Cetuximab"])

    def test_combination_named_in_title_is_core_when_not_background_type(self) -> None:
        interventions = [
            {"type": "DRUG", "name": "JAB-21822", "description": "KRAS G12C inhibitor"},
            {"type": "DRUG", "name": "JAB-3312", "description": "SHP2 inhibitor"},
        ]
        module = extract_trial_drug_module(
            fake_fetch(
                "NCT05288205",
                "Phase 1/2a Study of JAB-21822 Plus JAB-3312",
                interventions,
            )
        )
        self.assertEqual([d["name"] for d in module["core_study_drugs"]], ["JAB-21822", "JAB-3312"])
        self.assertFalse(module["ctgov_results_summary"]["available"])

    def test_no_results_means_no_efficacy_numbers(self) -> None:
        module = extract_trial_drug_module(
            fake_fetch(
                "NCT06447662",
                "A Study to Learn About PF-07934040",
                [{"type": "DRUG", "name": "PF-07934040", "description": "panKRAS inhibitor"}],
                has_results=False,
            )
        )
        self.assertIn("不能从该页面生成本药/本组合的 ORR", module["ctgov_results_summary"]["summary"])
        self.assertEqual(module["ctgov_results_summary"]["metrics"], [])


if __name__ == "__main__":
    unittest.main()
