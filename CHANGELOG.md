# Changelog

## v2.0.0 — 2026-05-07 — Subskill architecture

### Breaking changes

- Repository restructured to multi-skill layout following [vercel-labs/agent-skills](https://github.com/vercel-labs/agent-skills) convention. The single `SKILL.md` at root is replaced by 5 skills under `skills/`.
- Python `repo/` directory removed. Mechanism scripts moved to `skills/clinical-trial-matching/scripts/`.
- Hardcoded JSON knowledge tables removed:
  - `repo/data/risk_taxonomy.json` → moved to `skills/trial-risk-annotator/rules/risk-*.md`
  - `repo/data/efficacy_database.json` → moved to `skills/trial-efficacy-contextualizer/rules/soc-*.md`
  - `repo/data/soc_benchmarks.json` → moved to `skills/trial-efficacy-contextualizer/rules/soc-*.md`
  - `repo/data/clinical_ontology.json` → slimmed to aliases + chemo regimens + therapy classes only

### Bugs fixed (regression tests in `AGENTS.md`)

1. **PDAC risk text leaking onto CRC reports** (v1.7.x `risk_lookup.py` keyed on mechanism only). Fixed by `trial-risk-annotator` requiring `applies_because: (mechanism × cancer × patient)` grounding for every risk.
2. **KRAS G12D drug class baseline applied to KRAS G12C patient** (v1.7.x `efficacy_lookup.py` matched on "RAS mutation (any)"). Fixed by `trial-efficacy-contextualizer` requiring drug class to match patient mutation.
3. **CRC SoC database had only 1 entry** (v1.7.x `soc_benchmarks.json` had only `metastatic_1L_BRAF_V600E`). Fixed by `trial-efficacy-contextualizer` reading SoC from LLM training knowledge (NCCN / CSCO / ESMO guidelines + pivotal trials), with the schema enforcing explicit `evidence_source.tier` and `applies_because` for auditability.
4. **R1 hard rule (prior same-class drug → demote) silently failed** for patients with prior anti-PD-1 / anti-VEGF (v1.7.x regex was hardcoded to KRAS-specific phrasings). Fixed by `trial-gater/rules/R1-prior-same-class-drug.md` with comprehensive drug class reference.
5. **GoC trigger missed L3+ patients** (v1.7.x used `treatment_lines_completed` ignoring ongoing line). Fixed by `decision-synthesizer/rules/synthesis-goals-of-care-trigger.md` using `effective_line = completed + (1 if ongoing else 0)`.
6. **`run_v160_pipeline.sh` `cd` to `$REPO` broke relative `--patient` path**. Fixed by removing the orchestration shell script (subskills are dispatched via Agent tool from parent SKILL.md, no path-juggling needed).
7. **`decision_report.json` had `patient_summary={}` and `feasibility_score=None`** despite stdout printing correct values. Fixed by `decision-synthesizer/rules/output-decision-report-schema.md` requiring all fields populated.

### Added

- `skills/trial-gater/` — LLM subskill replacing `scoring/gating.py` + R1-R5 regex
- `skills/trial-risk-annotator/` — LLM subskill replacing `scoring/risk_lookup.py` + `risk_taxonomy.json`
- `skills/trial-efficacy-contextualizer/` — LLM subskill replacing `synthesis/efficacy_lookup.py` + `efficacy_database.json` + `soc_benchmarks.json`
- `skills/decision-synthesizer/` — LLM subskill replacing `synthesis/decision_paths.py` + `goals_of_care.py` + `consistency_check.py`
- `AGENTS.md` — guidance for AI agents working on this codebase
- Per-skill `rules/` directories with prefix-named markdown files

### Kept

- `dual_source_search.py` — parallel CT.gov v2 query (stdlib only)
- `chictr_resilient.py` — retry/circuit-breaker wrapper for ChiCTR MCP
- `nct_verifier.py` — live NCT API + citation chain verification
- `feasibility.py` — 5-dim deterministic feasibility scoring
- `html_renderer.py` + `template.html` — JSON → self-contained HTML
- `setup-chictr-mcp.sh` — one-command ChiCTR MCP installer

---

## v1.7.1 — 2026-04-XX (legacy)

See [previous SKILL.md](https://github.com/CancerDAO/clinical-trial-matching-skill/blob/v1.7.1/SKILL.md) for the v1.7.x changelog. Headline:

- G1-G6 — externalized clinical ontology to JSON
- Validated on 3 cases: PDAC G12D, PDAC G12C, NSCLC EGFR
- Eval framework: `must_match_keywords` semantic = ANY

The fundamental limitation of v1.7.x — that clinical knowledge was JSON-encoded and never complete enough — is what motivated v2's subskill architecture.

## v1.6.0 — 2026-XX-XX

- Decision Report (Top N) + Match List dual-layer output
- 5-dim feasibility scoring
- Blocker vs Advisor gating
- Structured trial metadata extraction (replacing v1.5 keyword line_info)
- Efficacy database (NCT-level + drug-class baselines)
- vs Standard of Care head-to-head (PDAC/NSCLC/CRC — though CRC was nearly empty)
- Risk taxonomy (mechanism × cancer — though cross-cancer leaks were not prevented)
- Patient profile internal-consistency check
- Goals of Care trigger module
- Enhanced NCT verification (status + condition + intervention + citations)
- Golden test cases + eval-runner with semantic checks
- ChiCTR retry/circuit-breaker wrapper
- search_plan disease_stage_filter
