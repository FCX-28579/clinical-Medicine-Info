# Third-Party Attributions

This repository builds on the following upstream projects. We gratefully
acknowledge their authors.

## NCBI TrialGPT (conceptual lineage only)

- Upstream: https://github.com/ncbi-nlp/TrialGPT
- License: U.S. Government Work / Public Domain
- Scope: earlier versions of this repository vendored NCBI's Python
  package under `repo/trialgpt_matching/`, `repo/trialgpt_ranking/`,
  and `repo/trialgpt_retrieval/keyword_generation.py` plus
  `hybrid_fusion_retrieval.py`. Those modules called Azure OpenAI
  directly and were never invoked by this skill's workflow (Claude
  performs all LLM reasoning in the conversation), so the directories
  and files were removed; the remaining retrieval code (now under
  `repo/retrieval/`) is CancerDAO-original. The 8-dimension keyword
  strategy and criterion-level evaluation pattern are conceptually
  inspired by the NCBI paper.
- Suggested citation if you build on this work:

  > Qiao Jin, Zifeng Wang, Charalampos S. Floudas, Fangyuan Chen, Changlin
  > Gong, Dara Bracken-Clarke, Elisabetta Xue, Yifan Yang, Jimeng Sun,
  > Zhiyong Lu. *Matching Patients to Clinical Trials with Large Language
  > Models.*

## chictr-mcp-server

- Upstream: https://github.com/PancrePal-xiaoyibao/chictr-mcp-server
- Role: external runtime dependency that provides MCP tools for querying
  ChiCTR (中国临床试验注册中心). Installed separately via `npx`; source is **not**
  vendored into this repository.

## CancerDAO Enhancements

The following additions are contributed by CancerDAO and released under the
MIT license (see `LICENSE`):

- Dual-source search orchestration (`repo/retrieval/dual_source_search.py`)
  — pure stdlib, no LLM client, no external Python dependencies.
- HTML report template (`repo/report/template.html`).
- The `SKILL.md` skill definition: 8-dimension keyword strategy,
  criterion-level chain-of-thought evaluation, hard grading rules (R1–R5),
  three-stage verification pipeline, compliance guardrails, and the Chinese
  clinical workflow.
