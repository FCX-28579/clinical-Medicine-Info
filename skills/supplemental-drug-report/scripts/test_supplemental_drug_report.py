from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from supplemental_drug_report import FdaEvidenceDatabase, FetchResult, extract_trial_drug_module, render_report


def write_fake_db(path: Path) -> None:
    payload = {
        "schema_version": "1.2-test",
        "drug_count": 2,
        "drugs": [
            {
                "drug_id": "lumakras",
                "identity": {
                    "drug_id": "lumakras",
                    "brand_name": "LUMAKRAS",
                    "generic_name": "SOTORASIB",
                    "manufacturer_name": "Amgen",
                    "drug_class": "KRAS G12C inhibitor",
                    "cancer_context": ["CRC", "NSCLC"],
                    "biomarkers": ["KRAS G12C"],
                    "excluded_biomarkers": [],
                },
                "evidence_modules": {
                    "efficacy": {
                        "available": True,
                        "metrics": [
                            {
                                "name": "ORR",
                                "value": "26%",
                                "comparison": "FDA label clinical studies section",
                                "source": {"label": "openFDA label: LUMAKRAS", "url": "https://api.fda.gov/drug/label.json?search=set_id:test"},
                                "snippet": "overall response rate was 26%",
                            }
                        ],
                    }
                },
                "sources": [{"label": "openFDA label: LUMAKRAS", "url": "https://api.fda.gov/drug/label.json?search=set_id:test"}],
                "quality_flags": ["test flag"],
            },
            {
                "drug_id": "pemetrexed",
                "identity": {
                    "drug_id": "pemetrexed",
                    "brand_name": "PEMETREXED",
                    "generic_name": "PEMETREXED",
                    "drug_class": "antifolate",
                    "cancer_context": ["NSCLC"],
                    "biomarkers": [],
                    "excluded_biomarkers": ["EGFR", "ALK"],
                },
                "evidence_modules": {"efficacy": {"available": False, "metrics": []}},
                "sources": [],
                "quality_flags": [],
            },
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def fake_fetch() -> FetchResult:
    return FetchResult(
        nct_id="NCT00000001",
        source_url="https://clinicaltrials.gov/study/NCT00000001",
        study={
            "protocolSection": {
                "identificationModule": {"briefTitle": "A Study of Sotorasib in KRAS G12C Colorectal Cancer"},
                "statusModule": {"overallStatus": "RECRUITING"},
                "designModule": {"phases": ["PHASE2"]},
                "sponsorCollaboratorsModule": {"leadSponsor": {"name": "Example Sponsor"}},
                "conditionsModule": {"conditions": ["Colorectal Cancer"]},
                "armsInterventionsModule": {
                    "interventions": [
                        {"type": "DRUG", "name": "Sotorasib", "description": "KRAS G12C inhibitor"},
                        {"type": "DRUG", "name": "Leucovorin", "description": "Background chemotherapy support"},
                    ]
                },
                "eligibilityModule": {"eligibilityCriteria": "Inclusion: KRAS G12C mutation; ECOG 0-1; measurable disease."},
            }
        },
    )


class SupplementalDrugReportTests(unittest.TestCase):
    def test_database_matching_and_rendering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "db.json"
            write_fake_db(db_path)
            db = FdaEvidenceDatabase(db_path)
            patient = {"patient_id": "P1", "cancer_type": "CRC", "mutations": ["KRAS G12C"], "biomarkers_known": {"KRAS": "G12C"}}
            module = extract_trial_drug_module(fake_fetch(), db, patient)
            self.assertEqual([d["name"] for d in module["core_study_drugs"]], ["Sotorasib"])
            self.assertEqual(module["fda_database_matches"][0]["brand_name"], "LUMAKRAS")
            self.assertTrue(module["same_class_comparison"]["available"])
            self.assertIn("本地 FDA 肿瘤药物证据库", module["source_policy"])
            report = render_report(
                patient,
                {"patient_summary": {"patient_id": "P1", "diagnosis": "CRC"}, "decision_paths": [{"rank": 1, "trial_id": "NCT00000001", "trial_title": "Trial", "sponsor": "Example", "phase": "PHASE2", "feasibility_score": 0.8, "blockers_pending": []}]},
                {"modules": {"NCT00000001": module}, "errors": {}},
            )
            self.assertIn("FDA 本地数据库匹配", report)
            self.assertIn("同类药物疗效对比", report)
            self.assertIn("LUMAKRAS", report)


if __name__ == "__main__":
    unittest.main()
