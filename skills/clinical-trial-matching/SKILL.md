---
name: clinical-trial-matching
description: Use when matching an oncology patient (esp. Chinese patients) to clinical trials and generating a decision-grade HTML report. Triggers on phrases like "临床试验匹配", "trial matching", "shortlist trials for this patient", "MTB trial section". Coordinates dual-source retrieval (ClinicalTrials.gov + ChiCTR) and 4 LLM subskills for gating, risk, efficacy, and decision synthesis.
license: MIT
metadata:
  author: CancerDAO
  version: "2.0.0"
  inspired_by: NCBI TrialGPT (ncbi-nlp/TrialGPT)
  generalization_principle: "Code is mechanism. Knowledge is in subskills. Adding a new cancer/drug class = update a subskill rule file, never a code edit."
---

# Clinical Trial Matching (v2 — subskill architecture)

Orchestrator for end-to-end oncology clinical trial matching. v2 splits the v1.7.x monolith into a thin Python mechanism layer + 4 LLM subskills for the parts that need clinical judgment.

> ⚠️ **This tool provides information matching only. It does not constitute medical advice or treatment recommendations.** All enrollment decisions must be reviewed by a qualified clinical research team.

## What changed from v1.7.x

| | v1.7.x | v2 |
|---|---|---|
| Gating (R1–R5) | `scoring/gating.py` regex | `trial-gater` subskill (LLM reads parsed_criteria) |
| Risk narratives | `risk_taxonomy.json` lookup | `trial-risk-annotator` subskill (per mechanism × cancer) |
| Efficacy + SoC | `efficacy_database.json` + `soc_benchmarks.json` lookup | `trial-efficacy-contextualizer` subskill |
| Decision paths | `synthesis/decision_paths.py` | `decision-synthesizer` subskill |
| Cancer types supported | 3 (PDAC G12D/G12C, NSCLC EGFR — only ones with hardcoded data) | All — knowledge lives in LLM, no per-cancer data table to maintain |

**The bugs v2 was built to fix:**

1. PDAC risk text leaking onto CRC reports (`risk_taxonomy.json` keyed on mechanism only, not (mechanism × cancer))
2. KRAS G12D drug-class baseline applied to a KRAS G12C patient (efficacy lookup matched on "RAS mutation (any)" → wrong class)
3. CRC SoC database had only 1 entry (BRAF V600E 1L) → all CRC patients got "vs SoC: not available"
4. R1 (prior same-class drug → demote) silently failed on patients with prior anti-PD-1/anti-VEGF
5. GoC trigger logic compared `treatment_lines_completed` to thresholds without considering current ongoing line

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│ PARENT: clinical-trial-matching                                  │
│   1. Read patient summary → patient.json (LLM)                   │
│   2. Generate search_plan.json (LLM, 8-dim keyword strategy)     │
│   3. Python: dual_source_search.py → nct_results.json            │
│   4. MCP: ChiCTR query (via mcp__chictr__*) → merge              │
│   5. Python: nct_verifier.py → verified.json (45 → live API)     │
│   6. Python: feasibility.py → scored.json (5-dim deterministic)  │
│                                                                   │
│      ↓ for each candidate trial, dispatch subagent batches:      │
│                                                                   │
│   7a. → trial-gater         (gating verdict + R1–R5)             │
│   7b. → trial-risk-annotator (mechanism × cancer × patient)       │
│   7c. → trial-efficacy-contextualizer (efficacy + per-line SoC)   │
│                                                                   │
│      ↓ aggregate, then:                                          │
│                                                                   │
│   8. → decision-synthesizer  (Top-N paths + GoC + diversity)     │
│   9. Python: html_renderer.py → report.html                      │
└─────────────────────────────────────────────────────────────────┘
```

## Inputs

- Patient summary (structured JSON or free-text record / folder path)
- Optional: data source preference (`NCT` / `ChiCTR` / `both`, default both)
- Optional: patient core ask (most patients won't have one — skill infers from diagnosis + molecular + treatment history)

## Outputs

A self-contained `~/Downloads/临床试验匹配报告_{patient_id}_{date}.html` (no external assets, opens in any browser).

## Workflow

### Step 0 — Pre-flight check

1. Verify `mcp__chictr__search_trials` and `mcp__chictr__get_trial_detail` are available. If not:
   - Tell the user to run `bash scripts/setup-chictr-mcp.sh` then restart Claude Code.
   - If user declines, proceed with NCT-only and **annotate the final report**: "ChiCTR data source unavailable".
2. Verify Python 3.9+ on PATH (`python3 --version`).

### Step 1 — Patient profile extraction (LLM)

Read the input. If it's a folder of records, follow the `cancer-buddy-organize` skill conventions to produce a normalized `patient.json`. If it's structured JSON already, validate it has at minimum:

```
{
  "patient_id": "...",
  "cancer_type": "CRC|NSCLC|PDAC|...",
  "stage": "IV|III|...",
  "disease_stage": "metastatic|locally_advanced|resectable",
  "metastasis_sites": [...],
  "mutations": ["KRAS G12C", ...],
  "biomarkers_known": {"MSI": "MSS", "TMB": 7.7, ...},
  "treatment_lines_completed": <int>,
  "treatment_history": [{"line": N, "regimen": "...", "outcome": "PR|SD|PD"}, ...],
  "current_therapy_ongoing": <bool>,
  "prior_therapies": ["FOLFOX", "anti-PD-1", "anti-VEGF", ...],
  "ecog": <0..4>,
  "kps": <int>,
  "comorbidities": [...],
  "organ_function": "normal|borderline|impaired",
  "age": <int>,
  "country": "China",
  "patient_location": "...",
  "willing_to_travel_internationally": <bool>,
  "willing_to_travel_domestic": <bool>,
  "affordability_tier": "low|medium|high"
}
```

See `examples/PT-17CE02BC33-patient.json` for a worked example.

**Critical fields** (gating consumers will fail loud if missing):
- `cancer_type`, `mutations`, `treatment_lines_completed`, `prior_therapies`, `disease_stage`

**Note**: `treatment_lines_completed` is the count of *completed* lines. If patient is currently on an ongoing line, set `current_therapy_ongoing: true` — the GoC trigger logic uses this.

### Step 2 — Generate search plan (LLM)

Output a `search_plan.json` per the schema in `examples/PT-17CE02BC33-search-plan.json`. Required keyword groups (omit any one and the verifier subskill will flag the gap):

1. **Disease + mutation specific** — e.g. `colorectal cancer KRAS G12C`
2. **Generalized solid tumor** — e.g. `solid tumor KRAS G12C` (catches basket trials)
3. **Combination targets** — e.g. `KRAS G12C cetuximab` (CodeBreaK paradigm)
4. **Pathway / resistance strategy** — e.g. `RAS-ON inhibitor`, `pan-KRAS`, `KRAS G12C resistance`
5. **Exhaustive drug names** — list every known investigational + approved drug in the target class (sotorasib, adagrasib, divarasib, glecirasib, calderasib, D3S-001, PF-07934040, JAB-21822, BGB-53038, MK-1084, GDC-6036, and any current pipeline)
6. **Cell therapy (mutation-agnostic)** — CAR-T, TIL, TCR-T for the cancer type + targets (CEA, GUCY2C, etc.)
7. **Immunotherapy (post-PD-1, MSS-aware)** — bispecific, LAG-3, TIGIT for resistance contexts
8. **Chinese keywords (ChiCTR)** — single-token, no compound queries (ChiCTR's tokenizer is brittle — "结直肠癌 KRAS" returns empty; use "结直肠癌" + "KRAS" separately)

### Step 3 — Dual-source retrieval (Python + MCP)

```bash
python3 scripts/retrieval/dual_source_search.py \
  --plan search_plan.json \
  --out nct_results.json \
  --max-per-query 10
```

For ChiCTR: call `mcp__chictr__search_trials(keyword=...)` per Chinese keyword, **degrade single-token if compound returns empty**. Merge results by registration_number into `nct_results.json`.

### Step 4 — NCT verification + feasibility (Python)

```bash
python3 scripts/verification/nct_verifier.py --in nct_results.json --patient patient.json --out verified.json
python3 scripts/scoring/feasibility.py --in verified.json --patient patient.json --out scored.json
```

Feasibility is intentionally deterministic (5 dims: recruiting status, geographic access, time cost, financial cost, slot availability). It's mechanism, not judgment, so it stays Python.

### Step 5 — Per-trial LLM analysis (subagent dispatch)

For the top ~30 candidates from `scored.json`, dispatch subagent batches in parallel. **Use Agent tool, not inline Skill invocation** — main context can't fit 30 × 3 detailed analyses.

Recommended batching: 5 trials per subagent, 3 dimensions per trial = ~6 subagents per dimension × 3 dimensions = ~18 subagents total. Run all subagents per dimension in parallel (single message with multiple Agent tool calls).

For each candidate trial:

```
Agent(subagent_type="general-purpose", description="trial-gater batch",
      prompt="Use the trial-gater skill to evaluate the following 5 trials against patient X.
              Trials: [...]. Patient: {...}. Output JSON per the schema in
              skills/trial-gater/rules/output-gating-verdict-schema.md.")
```

Same pattern for `trial-risk-annotator` and `trial-efficacy-contextualizer`. Aggregate outputs into `analyzed.json`:

```
{
  "trial_id": "NCT...",
  "gating": { ...from trial-gater },
  "risks": [ ...from trial-risk-annotator ],
  "efficacy_context": { ...from trial-efficacy-contextualizer }
}
```

### Step 6 — Decision synthesis (subagent dispatch)

Dispatch ONE subagent with `decision-synthesizer` skill loaded:

```
Agent(prompt="Use decision-synthesizer skill. Inputs: analyzed.json + patient.json.
              Output decision_report.json with Top 3 decision paths + Goals-of-Care section
              + vs-SoC head-to-head per the output schema.")
```

### Step 7 — HTML render (Python)

```bash
python3 scripts/render/html_renderer.py \
  --report decision_report.json \
  --analyzed analyzed.json \
  --patient patient.json \
  --out ~/Downloads/临床试验匹配报告_{patient_id}_{YYYY-MM-DD}.html
```

### Step 8 — Verification pass (LLM, in main context)

Read the rendered HTML. Cross-check:

1. Every NCT/ChiCTR ID in the report exists in `verified.json` with `valid=true`
2. No risk narrative mentions a cancer type other than the patient's (e.g. flag "PDAC" appearing in a CRC report)
3. No efficacy class baseline mismatched to patient's mutation (e.g. KRAS G12D class data on a G12C patient)
4. SoC comparison present when patient is on standard line where SoC is well-defined
5. Goals-of-Care section present when patient is on ≥3rd line OR predicted median OS at current line ≤ ontology threshold

If any check fails, regenerate the affected section and re-render.

## Compliance (patient-facing output)

- **No numerical scores shown.** Internal only.
- **No "推荐" / "recommend" wording.** Use "匹配理由" / "match rationale".
- **No priority ranking stars.** Top-N paths shown by feasibility + diversity, not by "rank".
- Provide investigator + center info so patients can self-contact, but do not direct them to a specific contact.
- Disclaimer present in footer.

## Subskill index

| Subskill | Replaces (v1.7.x) | When |
|---|---|---|
| [trial-gater](../trial-gater/SKILL.md) | `scoring/gating.py` + R1-R5 regex | Per-trial criterion-by-criterion eligibility |
| [trial-risk-annotator](../trial-risk-annotator/SKILL.md) | `risk_lookup.py` + `risk_taxonomy.json` | Per (mechanism × cancer × patient) risk narrative |
| [trial-efficacy-contextualizer](../trial-efficacy-contextualizer/SKILL.md) | `efficacy_lookup.py` + `efficacy_database.json` + `soc_benchmarks.json` | Trial efficacy + line-appropriate SoC for vs-SoC comparison |
| [decision-synthesizer](../decision-synthesizer/SKILL.md) | `synthesis/decision_paths.py` + `goals_of_care.py` + `consistency_check.py` | Top-N paths + diversity bucket + GoC trigger |

## File map

```
skills/clinical-trial-matching/
├── SKILL.md                          # this file
├── data/
│   └── clinical_ontology.json        # cancer aliases + chemo regimens + therapy classes (NO efficacy/risk/SoC)
├── scripts/                          # mechanism (deterministic Python)
│   ├── retrieval/
│   │   ├── dual_source_search.py     # parallel CT.gov v2 query (stdlib only)
│   │   └── chictr_resilient.py       # retry/circuit-breaker wrapper
│   ├── verification/
│   │   └── nct_verifier.py           # live NCT API + citation chain
│   ├── scoring/
│   │   └── feasibility.py            # 5-dim deterministic feasibility
│   ├── render/
│   │   ├── html_renderer.py          # JSON → self-contained HTML
│   │   └── template.html             # 8-section report template
│   └── setup-chictr-mcp.sh           # one-command ChiCTR MCP installer
└── examples/
    ├── PT-17CE02BC33-patient.json    # KRAS G12C mCRC, 3L example
    └── PT-17CE02BC33-search-plan.json
```

## Generalization principle

> **Code is mechanism. Knowledge is in subskills.**
>
> Adding a new cancer type, drug class, or risk narrative MUST be done by editing a rule file in the relevant subskill (`rules/*.md`), never by editing Python code or extending a JSON lookup table.

## References

- TrialGPT (NCBI): https://github.com/ncbi-nlp/TrialGPT (8-dim keyword strategy + criterion-level CoT origin)
- ChiCTR MCP server: https://github.com/PancrePal-xiaoyibao/chictr-mcp-server
- CancerDAO: https://github.com/CancerDAO
