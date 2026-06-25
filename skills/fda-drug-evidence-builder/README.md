# FDA Drug Evidence Builder

`fda-drug-evidence-builder` 是为 ClinicalTrialSKILL 服务的子技能，用于构建可复用的本地 FDA 肿瘤药物证据库。

它支持两种模式：

1. `seed` 模式：按人工维护的药物清单下载 openFDA label。
2. `bulk-openfda` 模式：读取 openFDA bulk download 分片，扫描全部 FDA drug label，采用高召回肿瘤相关性初筛，尽量保留可能与癌症相关的药物，同时剔除明显非肿瘤适应症记录。

## 输出内容

默认输出：

```text
data/fda_drug_evidence_db.json
```

每个药物模块包含：

- 药物身份信息：brand、generic、manufacturer、route、application number、set_id
- FDA 适应症摘要和原文片段
- 机制/药物类别摘要和原文片段
- FDA label 中可解析的 ORR / PFS / OS / DFS / DOR 等疗效指标
- 来源链接
- 质量提示
- brand / generic / biomarker / cancer context / drug class 索引

不再生成：

- 给患者看的中文解释
- 给医生/CRC 复核用的中文摘要

这两部分更适合在最终报告生成阶段结合具体患者、具体推荐试验再生成；本地 FDA 数据库只保留可复核的结构化证据。

## 全量 openFDA 初筛模式

开发时可限制扫描数量：

```bash
python skills/fda-drug-evidence-builder/scripts/build_openfda_drug_database.py \
  --bulk-openfda \
  --max-partitions 1 \
  --limit 50
```

正式构建时扫描所有 openFDA drug label 分片：

```bash
python skills/fda-drug-evidence-builder/scripts/build_openfda_drug_database.py \
  --bulk-openfda \
  --out skills/fda-drug-evidence-builder/data/fda_drug_evidence_db.json
```

筛选策略是高召回保留：优先依据适应症、临床研究、说明书描述、临床药理和 openFDA 药物类别识别肿瘤相关信号；同时加入明显非肿瘤适应症排除词，减少消化、心血管、麻醉、抗过敏等无关 label。这样仍会保留一部分边界样本，但能降低漏掉癌症相关药物的风险。

## Seed 模式

```bash
python skills/fda-drug-evidence-builder/scripts/build_openfda_drug_database.py \
  --seed skills/fda-drug-evidence-builder/data/seed_oncology_drugs.json \
  --out skills/fda-drug-evidence-builder/data/fda_drug_evidence_db.json
```

## 测试

```bash
python -m unittest skills/fda-drug-evidence-builder/scripts/test_build_openfda_drug_database.py
```

## 质量原则

- 没有 FDA label 的药物不会生成虚假模块。
- 没有明确疗效数字时，不外推 ORR/PFS/OS/DOR。
- 所有疗效指标必须附带来源和原文片段。
- bulk 初筛只负责剔除明显无关药物，不负责最终适应症精确判定。
- 本地数据库中的 FDA 数据只能作为药物证据参考，不能代表当前推荐试验药物疗效。

