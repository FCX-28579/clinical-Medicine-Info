---
name: supplemental-drug-report
description: Use when adding a Chinese supplemental drug information report to ClinicalTrialSKILL outputs after recommended ClinicalTrials.gov trials are selected. This subskill reads the recommended trial pages, identifies core study drugs and sponsors, matches them against the local FDA oncology drug evidence database built by fda-drug-evidence-builder, and renders patient-facing HTML plus JSON modules without live openFDA querying.
---

# supplemental-drug-report

Generate a Chinese supplemental drug information report for already selected ClinicalTrials.gov trials.

## Position

Use this after the base `clinical-trial-matching` workflow has produced patient, scored trial, and decision report JSON files. Keep the original matching and ranking flow unchanged. This subskill only adds a drug-information layer.

## Evidence policy

- Use ClinicalTrials.gov study API/page only to identify the current recommended trial, core study drug/intervention, sponsor, phase, status, conditions, and eligibility drug context.
- Use the local FDA oncology database from `../fda-drug-evidence-builder/data/fda_drug_evidence_db.json` for FDA drug identity, indication, class, biomarker, and efficacy comparison data.
- Do not perform live openFDA searches during report generation. Refresh the local database with `fda-drug-evidence-builder` when FDA evidence needs updating.
- Do not claim the FDA comparator data is efficacy for the current trial drug. Label it as same-class or same-context reference only.
- If no reliable local comparator is found, output a quality flag and keep the trial module rather than inventing data.

## Default command

```bash
python skills/supplemental-drug-report/scripts/supplemental_drug_report.py \
  --patient patient.json \
  --scored scored.json \
  --decision-report decision_report.json \
  --out-dir output_dir
```

Optional parameters:

- `--database`: path to `fda_drug_evidence_db.json`; defaults to the sibling FDA builder data file.
- `--studies-json`: offline ClinicalTrials.gov study cache for testing; keys should be NCT IDs.
- `--html-name`: defaults to `dynamic_drug_report_zh.html` for compatibility with the previous report flow.
- `--modules-name`: defaults to `dynamic_drug_modules_zh.json` for compatibility.

## Output

The script writes:

- `dynamic_drug_modules_zh.json`: structured per-trial drug modules.
- `dynamic_drug_report_zh.html`: self-contained Chinese HTML report preserving the existing report sections:
  - core study drug
  - manufacturer / sponsor background
  - same-class FDA drug efficacy comparison
  - patient-readable explanation
  - quality flags

## Matching principles

1. Directly match current study drugs by normalized brand, generic, and drug ID.
2. Infer trial context from title, conditions, eligibility, intervention names/descriptions, and patient biomarkers.
3. Score FDA database candidates by cancer context, positive biomarkers, drug class, direct drug-name match, and availability of source-bound efficacy metrics.
4. Penalize records where the relevant biomarker appears only in `excluded_biomarkers`.
5. Prefer exact target+cancer matches; fall back to target-only or cancer-context comparators when exact matches are unavailable.
6. Keep all generated statements source-bound and mark uncertain sections for physician/CRC review.
