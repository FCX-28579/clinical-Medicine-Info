---
name: fda-drug-evidence-builder
description: Use when building or refreshing a local FDA oncology drug evidence database for clinical-trial drug information reports. This subskill can scan openFDA bulk drug-label downloads, keep high-recall treatment-related oncology records, recover missing identities, parse scoped official label sections, extract source-bound efficacy metrics, deduplicate per partition, and write reusable local JSON modules.
license: MIT
metadata:
  author: CancerDAO
  version: "1.2.0"
  parent_skill: clinical-trial-matching
---

# fda-drug-evidence-builder

This subskill builds a local FDA oncology drug evidence database for downstream clinical-trial drug information reports.

## Goal

The subskill supports two workflows:

1. **Seed mode**: build records for a curated drug list.
2. **Bulk openFDA mode**: scan openFDA bulk drug label downloads, keep high-recall treatment-related oncology labels, and remove obvious non-oncology records or incidental cancer mentions.

The bulk mode is intentionally high-recall. It should retain records with treatment-related indication, oncology pharmacologic class, oncology endpoint, or oncology mechanism signals, while removing obviously unrelated labels. It also performs identity fallback, partition-safe deduplication, scoped biomarker extraction, and clinical-studies-only efficacy parsing.

## Output modules

Each local drug module contains:

- identity, including `excluded_biomarkers` when a biomarker appears only as an exclusion condition
- indications
- mechanism / drug class
- source-bound efficacy metrics
- sources
- quality flags

The local database does **not** generate patient-facing or clinician-facing narrative summaries. Those should be generated later by the final report layer after the patient, recommended trial, cancer type, line of therapy, and biomarker context are known.

## Query openFDA command

Use this mode for a smaller API-based refresh when a full bulk scan is not needed. For a complete local oncology database, prefer `--bulk-openfda`.

```bash
python skills/fda-drug-evidence-builder/scripts/build_openfda_drug_database.py \
  --discover-openfda \
  --per-query 50 \
  --limit 200
```

## Bulk openFDA command

Development smoke test:

```bash
python skills/fda-drug-evidence-builder/scripts/build_openfda_drug_database.py \
  --bulk-openfda \
  --max-partitions 1 \
  --limit 50
```

Full oncology build:

```bash
python skills/fda-drug-evidence-builder/scripts/build_openfda_drug_database.py \
  --bulk-openfda \
  --out skills/fda-drug-evidence-builder/data/fda_drug_evidence_db.json
```

## Seed command

```bash
python skills/fda-drug-evidence-builder/scripts/build_openfda_drug_database.py \
  --seed skills/fda-drug-evidence-builder/data/seed_oncology_drugs.json \
  --out skills/fda-drug-evidence-builder/data/fda_drug_evidence_db.json
```

## Oncology retention policy

Retain and parse a label when official openFDA evidence supports at least one high-recall oncology route:

- treatment-related cancer indication in `indications_and_usage` or `purpose`
- oncology pharmacologic class, such as antineoplastic, kinase inhibitor, immune checkpoint therapy, CAR-T, ADC, PARP inhibitor, EGFR/HER2/BRAF/MEK-directed therapy
- oncology efficacy endpoints in `clinical_studies`, such as ORR, DOR, PFS, OS, complete response, or partial response
- oncology mechanism signal in `clinical_pharmacology`, `description`, or `mechanism_of_action`

Remove records where cancer terms appear only incidentally, such as oncology-procedure sedation, hypertension safety text, menopause treatment, gastric/duodenal ulcer treatment, BPH treatment, sunscreen risk reduction, or other clearly non-oncology uses.

## Quality rules

- Do not invent drug efficacy data.
- Do not infer trial-drug efficacy from same-class FDA data.
- Do not create FDA readable-page links unless the URL is already verified or provided in the seed file.
- Every extracted metric must include source metadata and a label snippet.
- If no explicit efficacy metrics are found, write `metrics: []` and add a quality flag.
- Bulk filtering is an initial screen only; downstream matching must still confirm cancer type, biomarker, treatment line, and indication.

Additional parsing rules:

- Recover missing `brand_name` / `generic_name` from `package_label_principal_display_panel`, `spl_product_data_elements`, then `description` before falling back to `set_id`.
- Use openFDA pharm_class for `drug_class`; if absent, infer class only from mechanism fields. Do not infer drug class from arbitrary label text.
- Extract positive biomarkers separately from exclusion-only biomarkers. Index only positive `biomarkers`.
- Parse efficacy metrics only from `clinical_studies`; do not use `indications_and_usage` as an efficacy fallback.
- Deduplicate bulk records with normalized generic, brand, set_id, and indication hash while preserving partition-by-partition checkpoint writes.
