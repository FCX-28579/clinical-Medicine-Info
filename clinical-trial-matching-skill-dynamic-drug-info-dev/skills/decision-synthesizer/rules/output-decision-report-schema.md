# decision-synthesizer output schema

The full `decision_report.json` produced by this subskill. This is consumed directly by `scripts/render/html_renderer.py` (which is kept Python — deterministic template fill).

```json
{
  "report_version": "v2.0.0",
  "generated_at": "2026-05-07T12:34:56Z",

  "patient_summary": {
    "patient_id": "PT-17CE02BC33",
    "summary_text": "69M sigmoid colon adenocarcinoma stage IV (rpT4aN2aM1), liver/lung/LN mets, KRAS G12C, MSS, TMB 7.7, 2 prior lines (mFOLFOX6 PR; KELOX+Camrelizumab+Apatinib PD), currently L3 KELOX+Camrelizumab+Bevacizumab; KRAS G12C inhibitor naive; severe CV comorbidity; recent renal dysfunction.",
    "cancer_type": "CRC",
    "stage": "IV",
    "mutations": ["KRAS G12C"],
    "biomarkers": {"MSI": "MSS", "TMB": 7.7},
    "treatment_lines_completed": 2,
    "current_therapy_ongoing": true,
    "key_comorbidities": ["T2DM", "HTN3", "CAD", "post-stenting", "renal dysfunction"],
    "ecog": 1,
    "kps": 80,
    "country": "China",
    "patient_location": "山西省长治市",
    "treating_hospital": "北京市朝阳区三环肿瘤医院"
  },

  "consistency_flags": [
    {
      "flag": "Patient on bevacizumab (anti-VEGF) with active CAD + post-stenting; recent renal dysfunction may be partially anti-VEGF related",
      "severity": "warn",
      "evidence": "patient.comorbidities lists CAD + post-stent + renal dysfunction; current_therapy includes bevacizumab"
    },
    {
      "flag": "Transient subclinical hypothyroidism (TSH 6.49 → 0.80) likely irAE from camrelizumab — resolved but monitor on future PD-1 exposure",
      "severity": "info",
      "evidence": "key_lab_trends.TSH + treatment_history L2 includes camrelizumab"
    }
  ],

  "goals_of_care": {
    "triggered": true,
    "reasons": [
      "Effective L3 (ongoing) → L4 next; CRC ontology median OS at L3+ ≤ 6 months",
      "Cumulative comorbidity burden: 4 serious items (T2DM, HTN3, CAD, post-stenting, recent renal dysfunction)"
    ],
    "discussion_recommendation": "string per synthesis-goals-of-care-trigger.md template"
  },

  "decision_paths": [
    {
      "rank": 1,
      "role": "primary",
      "trial_id": "NCT07209111",
      "trial_title": "A Clinical Study of Calderasib (MK-1084) in People With Advanced Solid Tumors",
      "sponsor": "Merck Sharp & Dohme LLC",
      "phase": "PHASE2",
      "china_sites_count": 11,
      "feasibility_score": 0.961,
      "feasibility_dims": {
        "recruiting_status": 1.0,
        "geographic_access": 1.0,
        "time_cost": 0.855,
        "financial_cost": 1.0,
        "slot_availability": 0.95
      },
      "rationale": "string — 1-2 sentences",
      "efficacy_snapshot": { /* trial-efficacy-contextualizer output */ },
      "vs_soc": { /* trial-efficacy-contextualizer output */ },
      "risks": [ /* trial-risk-annotator output */ ],
      "blockers_satisfied": [...],
      "blockers_pending": [...],
      "alternatives_comparison": [
        {
          "trial_id": "NCT05410145",
          "trial_title": "D3S-001 in KRAS G12C solid tumors",
          "reason_not_chosen": "Trial-specific Phase 2 readout for MK-1084 published more recently; D3S-001 still maturing"
        }
      ],
      "consequences_of_skipping": "string — what patient gives up by skipping",
      "estimated_timeline": {
        "screening_window": "2026-05-07 to 2026-05-21",
        "earliest_first_dose": "2026-05-28",
        "critical_path_steps": [
          "Last bevacizumab dose washout (typically 28 days)",
          "Updated CT for measurable disease per RECIST 1.1",
          "Brain MRI to rule out CNS mets",
          "HBV/HCV/HIV serology",
          "Updated eGFR / renal panel",
          "Screening visit at site",
          "C1D1"
        ]
      }
    },
    { /* path 2 */ },
    { /* path 3 — or null if no qualifying candidate */ }
  ],

  "soc_benchmarks": [
    {
      "regimen": "Sotorasib + panitumumab (CodeBreaK 300, FDA approved)",
      "expected_orr": 0.26,
      "expected_orr_range": "20-30%",
      "expected_pfs_months": 5.6,
      "expected_os_months": 11.9,
      "evidence": "CodeBreaK 300 NEJM 2024",
      "patient_eligibility_note": "Eligible — patient has prior anti-VEGF but not anti-EGFR"
    },
    { /* regorafenib */ },
    { /* TAS-102 + bev */ },
    { /* fruquintinib */ }
  ],

  "match_inventory_size": {"match": 30, "conditional": 15, "exclude": 49},

  "v2_summary": {
    "total_trials_analyzed": 45,
    "verified_real_nct_ids": 45,
    "decision_paths_emitted": 3,
    "goc_triggered": true,
    "consistency_flags_count": 2,
    "redundancy_flags_count": 1,
    "redundancy_notes": [
      "NCT07209111 (calderasib + presumably anti-EGFR combo) is mechanistically similar to FDA-approved sotorasib+panitumumab — patient should weigh trial vs off-trial approved combo"
    ]
  }
}
```

## Field requirements

- `report_version` MUST be present (HTML renderer dispatches on this for v1 vs v2 layout)
- `patient_summary.summary_text` MUST be populated (v1.7.x bug: this was empty in JSON despite being printed to stdout)
- `decision_paths[*].feasibility_score` MUST be a number (v1.7.x bug: was None in JSON)
- `decision_paths[*].china_sites_count` MUST be a number (v1.7.x bug: was None in JSON)
- `goals_of_care.discussion_recommendation` MUST be populated when `triggered=true`
- `consistency_flags` MUST be an array (can be empty if patient profile is clean)
- `redundancy_notes` MUST list off-trial alternatives if any path overlaps with FDA-approved options

## HTML renderer compatibility

The `html_renderer.py` script reads this JSON and fills `template.html`. Renderer uses these path fields directly:

- `path.rank, role, trial_id, trial_title, sponsor, phase, china_sites_count, feasibility_score, feasibility_dims`
- `path.efficacy_snapshot` → "疗效估算" block
- `path.vs_soc` → "对照标准治疗" block
- `path.risks` → "风险标记" block
- `path.alternatives_comparison` → "为什么选这条 ≠ X / Y" block
- `path.consequences_of_skipping` → "如果不走这条会怎样?" block
- `path.estimated_timeline` → "时间表" block

If renderer breaks on a v2 report, check the JSON against this schema first.
