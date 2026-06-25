from __future__ import annotations

import unittest
from unittest.mock import patch

import build_openfda_drug_database as builder


MOCK_LABEL = {
    "set_id": "mock-set-id",
    "openfda": {
        "brand_name": ["LUMAKRAS"],
        "generic_name": ["SOTORASIB"],
        "manufacturer_name": ["Amgen Inc."],
        "route": ["ORAL"],
        "product_type": ["HUMAN PRESCRIPTION DRUG"],
        "pharm_class_epc": ["Antineoplastic Agent"],
    },
    "indications_and_usage": [
        "LUMAKRAS is indicated with panitumumab for adult patients with KRAS G12C-mutated metastatic colorectal cancer."
    ],
    "clinical_pharmacology": ["Sotorasib is an inhibitor of KRAS G12C."],
    "description": ["LUMAKRAS contains sotorasib."],
    "clinical_studies": [
        "Objective Response Rate (95% CI) Assessed by BICR. 36 (28, 45) "
        "Duration of Response Median Estimate using Kaplan-Meier method. , months (range) 10 (1.3+, 11.1)"
    ],
}


NON_ONCOLOGY_LABEL = {
    "openfda": {"brand_name": ["TESTDRUG"], "generic_name": ["test"], "pharm_class_epc": ["Analgesic"]},
    "indications_and_usage": ["Indicated for temporary relief of mild pain and fever."],
}

SUNSCREEN_LABEL = {
    "openfda": {"brand_name": ["SPF TEST"], "generic_name": ["ZINC OXIDE"], "product_type": ["HUMAN OTC DRUG"]},
    "indications_and_usage": ["Helps prevent sunburn. If used as directed with other sun protection measures, decreases the risk of skin cancer and early skin aging caused by the sun."],
}

MIDAZOLAM_LABEL = {
    "openfda": {"brand_name": ["MIDAZOLAM"], "generic_name": ["midazolam"], "pharm_class_epc": ["Benzodiazepine"]},
    "indications_and_usage": [
        "Midazolam hydrochloride injection is indicated for sedation/anxiolysis/amnesia prior to or during diagnostic, therapeutic or endoscopic procedures, such as oncology procedures."
    ],
}

HYPERTENSION_LABEL = {
    "openfda": {"brand_name": ["BP TEST"], "generic_name": ["olmesartan amlodipine hydrochlorothiazide"], "pharm_class_epc": ["Angiotensin 2 Receptor Blocker"]},
    "indications_and_usage": ["Indicated for the treatment of hypertension to lower blood pressure."],
    "clinical_studies": ["Clinical trials reported fatal and nonfatal cancer events in background safety summaries."],
}

NO_OPENFDA_LABEL = {
    "set_id": "4ab27d2f-e385-4e9c-b324-fa69c10b855a",
    "openfda": {},
    "package_label_principal_display_panel": ["DRUG: Vizimpro GENERIC: dacomitinib DOSAGE: TABLET ADMINSTRATION: ORAL"],
    "indications_and_usage": ["VIZIMPRO is indicated for patients with metastatic non-small cell lung cancer with EGFR exon 19 deletion or exon 21 L858R substitution mutations."],
    "mechanism_of_action": ["Dacomitinib is a kinase inhibitor of EGFR."],
}

PEMETREXED_EXCLUSION_LABEL = {
    "set_id": "pemetrexed-set-id",
    "openfda": {},
    "package_label_principal_display_panel": ["DRUG: Pemetrexed GENERIC: pemetrexed DOSAGE: INJECTION ADMINSTRATION: INTRAVENOUS"],
    "indications_and_usage": ["Pemetrexed is indicated with pembrolizumab and platinum chemotherapy for metastatic nonsquamous NSCLC with no EGFR or ALK genomic tumor aberrations."],
    "description": ["Pemetrexed is a folate analog metabolic inhibitor."],
}

ZOLEDRONIC_CALCIUM_LABEL = {
    "set_id": "zoledronic-set-id",
    "openfda": {"brand_name": ["Zoledronic acid"], "generic_name": ["ZOLEDRONIC ACID"]},
    "indications_and_usage": ["Zoledronic acid is indicated for hypercalcemia of malignancy."],
    "clinical_studies": ["The proportions of patients with normalization of corrected serum calcium by Day 10 were 88% and 70%."],
}


SEED = {
    "brand_name": "LUMAKRAS",
    "generic_name": "sotorasib",
    "drug_class": "KRAS G12C inhibitor",
    "cancer_context": ["CRC", "NSCLC"],
    "biomarkers": ["KRAS G12C"],
    "readable_fda_urls": [
        {
            "label": "FDA approval page",
            "url": "https://www.fda.gov/drugs/resources-information-approved-drugs/fda-approves-sotorasib-panitumumab-kras-g12c-mutated-colorectal-cancer",
            "context": "CRC KRAS G12C",
        }
    ],
}


class BuildOpenFdaDrugDatabaseTest(unittest.TestCase):
    def test_parse_metrics_from_label_text(self) -> None:
        metrics = builder.parse_metric_candidates(MOCK_LABEL["clinical_studies"][0], {"label": "openFDA label", "url": "https://api.fda.gov"})
        self.assertIn(("ORR", "36%"), [(m["name"], m["value"]) for m in metrics])

    def test_build_drug_module_has_structured_sections_without_narrative_summaries(self) -> None:
        with patch.object(builder, "fetch_openfda_label", return_value=(MOCK_LABEL, {"label": "openFDA label: LUMAKRAS", "url": "https://api.fda.gov"})):
            module = builder.build_drug_module(SEED)
        self.assertEqual(module["drug_id"], "lumakras")
        self.assertEqual(module["identity"]["brand_name"], "LUMAKRAS")
        self.assertTrue(module["evidence_modules"]["indications"]["available"])
        self.assertTrue(module["evidence_modules"]["efficacy"]["available"])
        self.assertNotIn("patient_summary_zh", module["evidence_modules"])
        self.assertNotIn("clinician_summary_zh", module["evidence_modules"])
        self.assertEqual(module["sources"][1]["source_type"], "FDA human-readable approval page")

    def test_oncology_filter_keeps_cancer_and_removes_obvious_non_cancer(self) -> None:
        self.assertTrue(builder.is_oncology_related(MOCK_LABEL))
        self.assertFalse(builder.is_oncology_related(NON_ONCOLOGY_LABEL))
        self.assertFalse(builder.is_oncology_related(SUNSCREEN_LABEL))
        self.assertFalse(builder.is_oncology_related(MIDAZOLAM_LABEL))
        self.assertFalse(builder.is_oncology_related(HYPERTENSION_LABEL))

    def test_build_database_index(self) -> None:
        with patch.object(builder, "fetch_openfda_label", return_value=(MOCK_LABEL, {"label": "openFDA label: LUMAKRAS", "url": "https://api.fda.gov"})):
            db = builder.build_database([SEED], pause_seconds=0)
        self.assertEqual(db["drug_count"], 1)
        self.assertEqual(db["schema_version"], "1.2")
        self.assertEqual(db["index"]["by_brand"]["LUMAKRAS"], ["lumakras"])
        self.assertEqual(db["index"]["by_biomarker"]["KRAS G12C"], ["lumakras"])
        self.assertEqual(db["index"]["by_cancer_context"]["CRC"], ["lumakras"])

    def test_identity_fallback_from_package_label_when_openfda_empty(self) -> None:
        module = builder.build_drug_module_from_label(NO_OPENFDA_LABEL, discovery_mode="bulk_openfda")
        self.assertEqual(module["drug_id"], "vizimpro")
        self.assertEqual(module["identity"]["brand_name"], "Vizimpro")
        self.assertEqual(module["identity"]["generic_name"], "dacomitinib")
        self.assertEqual(module["identity"]["drug_class"], "EGFR-directed therapy")
        self.assertIn("EGFR", module["identity"]["biomarkers"])

    def test_biomarker_exclusion_does_not_become_positive_marker_or_class(self) -> None:
        module = builder.build_drug_module_from_label(PEMETREXED_EXCLUSION_LABEL, discovery_mode="bulk_openfda")
        self.assertEqual(module["drug_id"], "pemetrexed")
        self.assertIsNone(module["identity"]["drug_class"])
        self.assertNotIn("EGFR", module["identity"]["biomarkers"])
        self.assertNotIn("ALK", module["identity"]["biomarkers"])
        self.assertIn("EGFR", module["identity"]["excluded_biomarkers"])
        self.assertIn("ALK", module["identity"]["excluded_biomarkers"])

    def test_non_tumor_response_metrics_are_not_parsed_as_orr(self) -> None:
        module = builder.build_drug_module_from_label(ZOLEDRONIC_CALCIUM_LABEL, discovery_mode="bulk_openfda")
        self.assertFalse(module["evidence_modules"]["efficacy"]["available"])
        self.assertEqual(module["evidence_modules"]["efficacy"]["metrics"], [])

    def test_dedupe_keys_include_identity_and_indication_hash(self) -> None:
        label_a = {"set_id": "a", "openfda": {}, "indications_and_usage": ["Drug A is indicated for metastatic breast cancer."]}
        label_b = {"set_id": "b", "openfda": {}, "indications_and_usage": ["Drug A is indicated for metastatic breast cancer."]}
        keys_a = builder.dedupe_keys_for_label(label_a)
        keys_b = builder.dedupe_keys_for_label(label_b)
        self.assertTrue(set(keys_a) & set(keys_b))



if __name__ == "__main__":
    unittest.main()
