# Clinical Trial Matching Skill

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)

A Claude Code skill for matching oncology patients (especially Chinese patients) to clinical trials. Dual-source retrieval across [ClinicalTrials.gov](https://clinicaltrials.gov) + [ChiCTR](https://www.chictr.org.cn), criterion-by-criterion eligibility evaluation, mechanism-aware risk and efficacy contextualization, and a self-contained HTML report.

> ⚠️ Information matching only. Not medical advice. All enrollment decisions must be reviewed by a qualified clinical research team.

---

## Quick start

### Install

Using the [`skills` CLI](https://github.com/vercel-labs/skills) (recommended):

```bash
# Install all 5 skills (parent + 4 subskills)
npx skills add CancerDAO/clinical-trial-matching-skill

# Or install a specific subskill standalone (e.g. for testing)
npx skills add CancerDAO/clinical-trial-matching-skill --skill trial-gater

# Or install globally (user-level instead of project-level)
npx skills add CancerDAO/clinical-trial-matching-skill -g
```

The CLI auto-detects your agent (Claude Code, Codex, Cursor, etc.) and symlinks the skills into the right directory. See the [skills CLI docs](https://github.com/vercel-labs/skills) for all options.

Then register the ChiCTR MCP server (one-time):

```bash
bash ~/.claude/skills/clinical-trial-matching/scripts/setup-chictr-mcp.sh
```

Restart Claude Code.

<details>
<summary>Manual install (without the CLI)</summary>

```bash
git clone https://github.com/CancerDAO/clinical-trial-matching-skill.git
cp -r clinical-trial-matching-skill/skills/* ~/.claude/skills/
bash ~/.claude/skills/clinical-trial-matching/scripts/setup-chictr-mcp.sh
```
</details>

### Use

In any conversation, describe the patient in natural language:

```
帮我做临床试验匹配:
诊断: 乙状结肠中分化腺癌 IV 期, 双肺/肝转移
分子特征: KRAS G12C, MSS, TMB 7.7
治疗线数: 已完成 2 线 (mFOLFOX6 PR; KELOX+卡瑞利珠+阿帕替尼 PD), 当前三线 KELOX+卡瑞利珠+贝伐进行中
合并症: HTN3 + CAD + 支架术后, 近期肾功能异常
```

Or in English:

```
Shortlist trials for: 69M sigmoid colon adenocarcinoma stage IV (rpT4aN2aM1),
KRAS G12C MSS, post-FOLFOX/KELOX+ICI, currently on 3L KELOX+camrelizumab+bevacizumab.
Severe CV comorbidity. Patient in Shanxi, treated in Beijing.
```

The skill produces `~/Downloads/临床试验匹配报告_{patient_id}_{date}.html` — a self-contained file with patient profile, treatment timeline, top-3 decision paths (each with feasibility, expected efficacy, vs-SoC comparison, risk profile, alternatives, timeline), Goals-of-Care section when triggered, full match inventory, and information-gap action items.

A worked example is at [`skills/clinical-trial-matching/examples/PT-17CE02BC33-*.json`](skills/clinical-trial-matching/examples/).

---

## How it works

1 parent skill orchestrates 4 LLM subskills + a thin Python mechanism layer:

```
┌─ clinical-trial-matching (parent) ─────────────────────────┐
│   Python: dual_source_search → nct_verifier → feasibility  │
│                                                             │
│   For each candidate trial (subagent-dispatched):           │
│     → trial-gater                  (R1–R5 eligibility)     │
│     → trial-risk-annotator         (mechanism × cancer)    │
│     → trial-efficacy-contextualizer (efficacy + per-line SoC)│
│                                                             │
│     → decision-synthesizer          (Top-N + GoC + diversity)│
│                                                             │
│   Python: html_renderer → report.html                       │
└─────────────────────────────────────────────────────────────┘
```

Mechanism stays in Python (deterministic, fast, stdlib-only). Clinical knowledge lives in the LLM subskills as markdown rule files.

---

## Repository layout

Following the [vercel-labs/agent-skills](https://github.com/vercel-labs/agent-skills) convention:

```
skills/
  clinical-trial-matching/         # parent orchestrator + scripts/ + data/ + examples/
  trial-gater/                     # criterion eligibility + R1-R5 hard rules
  trial-risk-annotator/            # per (mechanism × cancer) risk narratives
  trial-efficacy-contextualizer/   # efficacy + per-line SoC comparison
  decision-synthesizer/            # Top-N decision paths + GoC trigger + diversity
```

Each skill has its own `SKILL.md` and a `rules/` directory with prefix-named markdown files (e.g. `R1-prior-same-class-drug.md`, `risk-kras-g12c-by-cancer.md`).

For agents working on the codebase, see [`AGENTS.md`](./AGENTS.md).
## FDA drug evidence database and supplemental drug report

This version adds a reusable drug-information layer on top of the original clinical trial matching workflow. The base matching flow still produces patient profiles, trial retrieval, eligibility analysis, decision paths, and the standard HTML report. The new layer runs after trial selection and adds a Chinese, patient-readable drug information supplement for each selected ClinicalTrials.gov trial.

### What is added

Two new subskills are included:

```
skills/
  fda-drug-evidence-builder/       # builds a local FDA oncology drug evidence database from openFDA labels
  supplemental-drug-report/        # generates Chinese supplemental drug modules for selected CT.gov trials
```

The generated local FDA database is stored at:

```
skills/fda-drug-evidence-builder/data/fda_drug_evidence_db.json
```

The current database uses schema `1.2`, was built from the openFDA drug-label bulk partitions, and contains high-recall oncology-related FDA label records. The builder keeps likely oncology drugs, removes clearly non-oncology labels, recovers missing drug identity fields, separates positive biomarkers from exclusion-only biomarkers, deduplicates records, and only parses efficacy metrics from the `clinical_studies` section.

### Refresh the FDA oncology drug database

Use the FDA builder subskill when the local database needs to be rebuilt or audited:

```bash
python skills/fda-drug-evidence-builder/scripts/build_openfda_drug_database.py \
  --bulk-openfda \
  --out skills/fda-drug-evidence-builder/data/fda_drug_evidence_db.json
```

For a small smoke test:

```bash
python skills/fda-drug-evidence-builder/scripts/build_openfda_drug_database.py \
  --bulk-openfda \
  --max-partitions 1 \
  --limit 50
```

### Generate the supplemental drug report

After the original trial matching workflow has produced `patient.json`, `scored.json`, and `decision_report.json`, run:

```bash
python skills/supplemental-drug-report/scripts/supplemental_drug_report.py \
  --patient patient.json \
  --scored scored.json \
  --decision-report decision_report.json \
  --out-dir output_dir
```

The script writes:

```
output_dir/dynamic_drug_modules_zh.json
output_dir/dynamic_drug_report_zh.html
```

The report preserves the current Chinese report format and adds, for each selected trial:

- core study drug identified from the ClinicalTrials.gov study page/API
- sponsor / manufacturer-context note from ClinicalTrials.gov
- local FDA database match details
- same-class FDA efficacy comparison table
- Chinese indication summary extracted from `indications.excerpt`
- Chinese mechanism summary extracted from `mechanism.excerpt`
- patient-readable notes and quality flags

The supplemental report does not perform live openFDA searches during rendering. It reads the selected CT.gov trial page for the current trial drug and then matches against the local FDA evidence database. FDA comparator data is shown only as same-class or same-context reference and is not presented as efficacy for the current investigational drug.

For backward compatibility, the previous renderer entrypoint now delegates to the new subskill:

```bash
python skills/clinical-trial-matching/scripts/render/ctgov_dynamic_drug_report_zh.py \
  --patient patient.json \
  --scored scored.json \
  --decision-report decision_report.json \
  --out-dir output_dir
```

### Validation

Representative local checks:

```bash
python -m unittest test_build_openfda_drug_database.py
python -m unittest test_supplemental_drug_report.py
```

The supplemental report was tested on the `PT-17CE02BC33` KRAS G12C metastatic colorectal cancer example with ClinicalTrials.gov-only trial selection. The generated report included 3 trial drug modules with 0 module-generation errors.

---

## Cancer type coverage

> **Code is mechanism. Knowledge is in subskills.**

**All cancer types are supported out of the box.** The LLM subskills generate eligibility judgments, efficacy estimates, and SoC comparisons from training knowledge of NCCN / CSCO / ESMO guidelines and pivotal trials — no per-cancer table maintenance.

Where the repo *does* ship anchored rule files is for **risk narratives that historically cross-leaked between cancer types** (the v1.7.x bug pattern: PDAC-specific risk text appearing on CRC reports). These are pinned per (mechanism × cancer) to lock the grounding:

- [KRAS G12C inhibitor by cancer](skills/trial-risk-annotator/rules/risk-kras-g12c-by-cancer.md) — NSCLC vs CRC vs PDAC sections (efficacy and risk profile differ markedly)
- [KRAS G12D class](skills/trial-risk-annotator/rules/risk-kras-g12d-class.md) — primarily PDAC
- [Pan-RAS / RAS-ON class](skills/trial-risk-annotator/rules/risk-pan-ras-class.md)
- [Cell therapy in solid tumors](skills/trial-risk-annotator/rules/risk-cell-therapy-solid-tumor.md)
- [Bispecific antibody in MSS CRC](skills/trial-risk-annotator/rules/risk-bispecific-mss-crc.md)
- [Phase 1 dose escalation overlay](skills/trial-risk-annotator/rules/risk-phase-1-dose-escalation.md)

For mechanisms / cancers not in this list, the LLM generates risk narratives from training knowledge — the schema still requires explicit `applies_because: (mechanism × cancer × patient)` grounding so cross-cancer leaks can't happen.

### Want tighter accuracy for your cancer of interest?

If your clinical use case needs reproducibility lock-in (e.g. for regulatory audit), you can re-introduce per-cancer SoC reference files. Contribute a rule file — no code change needed:

1. Add aliases / chemo regimens to [`skills/clinical-trial-matching/data/clinical_ontology.json`](skills/clinical-trial-matching/data/clinical_ontology.json)
2. Add `skills/trial-efficacy-contextualizer/rules/soc-{cancer}-by-line.md` (SoC benchmarks per line, with PMID citations) — the subskill will prefer file content over training knowledge when present
3. Add `skills/trial-risk-annotator/rules/risk-{mechanism}-{cancer}.md` for cancer-specific mechanism risks

The next invocation picks up new rule files automatically.

---

## Data sources

| Source | Coverage | Access |
|---|---|---|
| [ClinicalTrials.gov](https://clinicaltrials.gov) | Global trials registry | API v2 (no key) |
| [ChiCTR](https://www.chictr.org.cn) | Chinese registered trials | [chictr-mcp-server](https://github.com/PancrePal-xiaoyibao/chictr-mcp-server) (Puppeteer; one-line install) |

ChiCTR's site requires browser automation (no JSON API, anti-scraping). The bundled `setup-chictr-mcp.sh` registers the MCP server in your Claude config; if it's unavailable, the skill degrades to ClinicalTrials.gov only and annotates the report.

---

## Compliance posture (patient-facing output)

- No numerical scores shown
- No "推荐" / "recommend" wording — uses "匹配理由" / "match rationale"
- No priority ranking
- Investigator + center info provided so patients can self-contact, but no specific contact directed
- Disclaimer in footer

Internal scoring (feasibility composite, evidence tier, confidence penalty) is preserved for debugging but not rendered.

---

## License & attribution

- **CancerDAO additions**: [MIT](./LICENSE)
- Inspired by [NCBI TrialGPT](https://github.com/ncbi-nlp/TrialGPT) (8-dimension keyword strategy + criterion-level CoT pattern). The original Python package is not vendored. See [`NOTICE.md`](./NOTICE.md).
- ChiCTR access via [chictr-mcp-server](https://github.com/PancrePal-xiaoyibao/chictr-mcp-server) by [PancrePal-xiaoyibao](https://github.com/PancrePal-xiaoyibao).

---

## Contributing

PRs welcome at <https://github.com/CancerDAO/clinical-trial-matching-skill>.

The high-leverage contributions are **rule files for new cancers / drug classes / risk narratives**, not Python changes. See [`AGENTS.md`](./AGENTS.md) for the decision tree on what belongs in code vs rules. See [`CHANGELOG.md`](./CHANGELOG.md) for version history.

Built by [CancerDAO](https://github.com/CancerDAO) — open-source AI for cancer patients.

