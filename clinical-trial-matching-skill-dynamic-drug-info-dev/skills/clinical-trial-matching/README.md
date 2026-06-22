# clinical-trial-matching

Parent orchestrator skill for end-to-end oncology clinical trial matching with dual-source retrieval (ClinicalTrials.gov + ChiCTR), per-trial LLM analysis, and a self-contained HTML decision report.

## Install

```bash
npx skills add CancerDAO/clinical-trial-matching-skill
```

This installs the parent skill plus 4 subskills it dispatches:
- [`trial-gater`](../trial-gater/) — criterion eligibility + R1-R5 hard rules
- [`trial-risk-annotator`](../trial-risk-annotator/) — per (mechanism × cancer) risk narratives
- [`trial-efficacy-contextualizer`](../trial-efficacy-contextualizer/) — efficacy + per-line SoC
- [`decision-synthesizer`](../decision-synthesizer/) — Top-N decision paths + GoC trigger

Then register the ChiCTR MCP server (one-time):

```bash
bash scripts/setup-chictr-mcp.sh
```

## Structure

- `SKILL.md` — full workflow + prompt contract
- `data/clinical_ontology.json` — cancer aliases, chemo regimens, therapy classes (NO efficacy / risk / SoC — those live in subskill rules)
- `scripts/` — deterministic Python mechanism (parallel HTTP retrieval, NCT live verification, 5-dim feasibility scoring, HTML template fill)
- `examples/` — worked patient + search_plan JSON examples

## Usage

Trigger from any conversation by describing the patient in natural language. See top-level [README](../../README.md) for examples.

## 动态药品信息模块

本开发版本增加了面向不同患者的动态药品信息报告。它不依赖固定药品证据库，而是在推荐试验生成后，针对每个推荐的 NCT 试验实时读取 ClinicalTrials.gov 页面/API，并从页面字段中生成药品说明模块。

当前原则：

- 药品信息来自对应试验页面，不从静态证据库补写。
- 申办方/合作者按 CT.gov 页面展示，不自动等同于上市许可持有人。
- 试验页面没有 posted results 时，不展示 ORR/PFS/OS/DOR 等疗效数字。
- 同类药物疗效对比仅在试验页面提供相关信息时展示；页面没有则明确标注未提供，避免外推。

示例：

```bash
python scripts/render/ctgov_dynamic_drug_report_zh.py \
  --patient <out-dir>/patient.json \
  --scored <out-dir>/scored.json \
  --decision-report <out-dir>/decision_report.json \
  --out-dir <out-dir>
```
