# FDA 同类药物疗效对比模块设计

## 目标

当 ClinicalTrialSKILL 给出任意推荐试验后，系统需要为每个推荐试验中的核心研究药物生成“FDA 同类药物疗效对比”模块。

这个模块的目标不是“强行给每个药都找一个数字”，而是：

1. 尽可能从 FDA 官方来源中找到同类、同适应症或同 biomarker 场景的已批准药物疗效数据。
2. 每个疗效数字必须绑定 FDA 官方 URL。
3. 如果没有合适 FDA 对照，必须明确说明失败原因。
4. 不能把不同癌种、不同突变、不同治疗线、不同联合方案的数据误当成当前试验药物疗效。

## 官方来源优先级

### Tier 1：FDA Oncology Approval Notifications

优先使用 FDA oncology approval / resources information approved drugs 页面。

这类页面通常包含：

- 批准药物或组合
- 适应症
- 关键入组人群
- 关键研究名称
- ORR、PFS、OS、DOR 等摘要疗效数据
- 常见不良反应

优点：

- 页面结构相对适合患者报告。
- 数字通常已经由 FDA 审核并简明呈现。
- 适合生成“同类药物疗效对比”。

限制：

- FDA 不保证每个批准都有独立 announcement。
- 老药、适应症扩展或标签更新可能只能在 label/Drugs@FDA 中找到。

### Tier 2：Drugs@FDA / FDA Label

如果 Tier 1 找不到，使用 Drugs@FDA label 或 openFDA drug label API。

优点：

- 覆盖面更广。
- 可追溯到正式标签。

限制：

- label 中疗效数据常在 Clinical Studies section，结构复杂。
- 同一个药可能有多个适应症，需要防止抽错癌种或治疗线。
- 需要更强的 parser 和人工复核提示。

### Tier 3：不使用非 FDA 数据作为默认对照

PubMed、ASCO、ESMO、公司新闻稿可以作为后续扩展，但不应混入“FDA 同类药物疗效对比”的默认模块。否则来源层级会变混乱。

## 总体流程

```text
推荐试验 NCT
  ↓
读取 CT.gov 页面/API
  ↓
识别核心研究药物
  ↓
抽取药物机制、靶点、癌种、biomarker、治疗线、联合方案
  ↓
生成 comparator query
  ↓
检索 FDA 官方来源
  ↓
解析候选 FDA 页面/label
  ↓
做适配性评分
  ↓
输出同类药物疗效对比或明确失败原因
```

## 关键模块

### 1. Trial Drug Profiler

输入：

- NCT ID
- CT.gov interventions
- CT.gov title
- CT.gov eligibility criteria
- patient profile

输出：

```json
{
  "trial_id": "NCT07087223",
  "core_drugs": [
    {
      "name": "Vebreltinib combined with Furmonertinib",
      "raw_description": "...",
      "inferred_targets": ["MET", "EGFR"],
      "inferred_class": ["MET inhibitor", "EGFR TKI"],
      "confidence": "medium",
      "source": "ClinicalTrials.gov interventions"
    }
  ],
  "disease_context": {
    "cancer_type": "NSCLC",
    "histology": "non-squamous / adenocarcinoma if available",
    "stage": "advanced/metastatic",
    "biomarkers": ["EGFR exon 19 deletion", "T790M"],
    "line_context": "after prior EGFR TKI"
  }
}
```

设计要点：

- 不要只靠药名判断机制。
- 优先使用 CT.gov intervention description。
- 如果 CT.gov 没有机制说明，再使用药名词典或 FDA/label 检索辅助推断。
- 推断结果必须标注 confidence。

### 2. Comparator Query Builder

根据药物 profile 生成 FDA 检索查询。

示例：

```json
{
  "queries": [
    "site:fda.gov EGFR-mutated metastatic NSCLC FDA approves progression-free survival",
    "site:fda.gov osimertinib EGFR T790M NSCLC objective response rate",
    "site:fda.gov amivantamab lazertinib EGFR exon 19 deletion L858R NSCLC PFS"
  ],
  "must_have": ["NSCLC", "EGFR"],
  "nice_to_have": ["T790M", "exon 19 deletion", "post EGFR TKI"],
  "avoid": ["KRAS", "ALK", "ROS1", "EGFR exon 20 only"]
}
```

查询生成规则：

- 癌种必须进入查询。
- 靶点/biomarker 必须进入查询。
- 如果患者治疗线已知，治疗线也进入查询。
- 如果研究药物是联合方案，分别查询：
  - 单药同类
  - 联合同类
  - 同 biomarker 标准治疗

### 3. FDA Source Retriever

职责：

- 只抓取 FDA 官方域名。
- 优先抓取 FDA oncology approval pages。
- 找不到时再查 label / Drugs@FDA / openFDA label。
- 保存原始 URL、抓取时间、页面标题和文本摘要。

输出：

```json
{
  "source_url": "https://www.fda.gov/...",
  "source_type": "fda_approval_page",
  "drug_names": ["osimertinib"],
  "indication_text": "...",
  "raw_text_hash": "...",
  "fetched_at": "2026-06-23T..."
}
```

### 4. FDA Evidence Parser

从 FDA 页面中抽取疗效字段。

目标字段：

- ORR
- PFS
- OS
- DOR
- CR/PR if available
- hazard ratio
- trial name
- study design
- line of therapy
- biomarker condition

输出：

```json
{
  "comparator_drug": "Osimertinib",
  "evidence_context": {
    "cancer_type": "NSCLC",
    "biomarker": "EGFR T790M",
    "line": "after EGFR TKI",
    "study": "AURA3"
  },
  "metrics": [
    {
      "name": "PFS",
      "value": "...",
      "comparison": "...",
      "source_url": "https://www.fda.gov/..."
    }
  ],
  "parser_confidence": "high"
}
```

Parser 规则：

- 不解析没有上下文的裸数字。
- 数字必须和疗效指标在同一段或相邻句内。
- 如果页面同时包含多个适应症，必须先匹配 indication 段落。
- 如果无法确认数字对应当前癌种/biomarker，则不输出该数字。

### 5. Comparator Fit Scorer

不是所有 FDA 对照都适合展示。需要给候选 comparator 打分。

建议评分：

| 维度 | 权重 | 说明 |
|---|---:|---|
| 癌种一致 | 30 | NSCLC 对 NSCLC，CRC 对 CRC |
| biomarker 一致 | 25 | EGFR/T790M/KRAS G12C/HER2 等 |
| 药物类别一致 | 20 | EGFR TKI、MET inhibitor、ADC、PD-1 等 |
| 治疗线一致 | 15 | 一线、后线、post-TKI、post-platinum |
| 联合策略一致 | 5 | 单药 vs 联合 |
| FDA 来源质量 | 5 | approval page > label > 其他 FDA 页面 |

输出分级：

- `direct_comparator`：同癌种、同 biomarker、同治疗线或非常接近。
- `class_reference`：同癌种、同通路，但治疗线或联合方式不同。
- `weak_reference`：同类药物但癌种/治疗线差异明显，只能低优先级展示。
- `no_valid_fda_comparator`：找不到合格 FDA 对照。

## 报告展示规则

### 推荐展示结构

```text
同类药物疗效对比

1. 最接近 FDA 对照
   药物/组合：
   FDA 批准适应症：
   为什么可作为同类参考：
   关键疗效数据：
   来源：

2. 其他参考
   ...

质量提示：
   - 这是 FDA 已批准同类药物数据，不代表当前试验药物疗效。
   - 癌种/biomarker/治疗线差异如下：...
```

### 不允许展示的情况

以下情况不能硬输出疗效对比：

- 只找到同靶点但不同癌种的数据。
- 只找到同癌种但不同靶点的数据。
- 只找到公司新闻稿，没有 FDA 官方来源。
- FDA 页面中没有疗效数字。
- 不能确认疗效数字对应哪个适应症。
- 当前研究药物机制无法识别。

此时报告应显示：

```text
未找到足够可靠的 FDA 同类药物疗效对比。
原因：
- 当前试验药物机制尚无法从 CT.gov 页面稳定识别；
- 或 FDA 页面没有同癌种/同 biomarker/同治疗线的已批准同类药物；
- 或 FDA 页面未提供可解析疗效数字。
```

## 对当前项目的代码改造建议

当前代码中的 `same_class_comparison()` 不应继续按 KRAS G12C mCRC 写死。建议拆成以下文件：

```text
scripts/drug_info/
  dynamic_ctgov_drug_info.py        # 继续负责 CT.gov 当前试验药物解析
  fda_comparator/
    query_builder.py                # 根据 trial drug profile 生成 FDA 查询
    retriever.py                    # 抓取 FDA 官方页面/label
    parsers.py                      # 解析 FDA approval page 和 label
    fit_scorer.py                   # 对 comparator 适配性评分
    schemas.py                      # 统一输出结构
```

`dynamic_ctgov_drug_info.py` 中只保留：

```python
profile = build_trial_drug_profile(ctgov_study, patient)
comparison = fda_comparator.find(profile)
```

不要在主文件里写某个癌种的 FDA URL。

## 最小可行版本

第一阶段不必支持所有药物，但接口必须支持所有药物。

建议先覆盖 5 类高频肿瘤药物：

1. EGFR-mutant NSCLC
2. KRAS G12C CRC/NSCLC
3. HER2-positive breast cancer
4. PARP / BRCA ovarian cancer
5. PD-1/PD-L1 immunotherapy

对于未覆盖类别，输出 `no_valid_fda_comparator`，但保留完整失败原因。

## 质量保证

每个 comparator 输出前必须通过以下校验：

- `source_url` 是 FDA 官方 URL。
- `metric.name` 不为空。
- `metric.value` 不为空。
- `metric.value` 来自原文可定位片段。
- `cancer_type_match` 不低于 class_reference 阈值。
- `biomarker_match` 不低于 class_reference 阈值。
- 报告文案中明确写出“同类参考，不代表当前试验药物疗效”。

建议测试集：

- CRC KRAS G12C：应找到 sotorasib + panitumumab、adagrasib + cetuximab。
- NSCLC EGFR T790M：应找到 osimertinib 相关 FDA 数据。
- Breast HER2：应找到 trastuzumab/pertuzumab/T-DXd 等 FDA 数据。
- Ovarian BRCA/HRD：应找到 PARP inhibitor FDA 数据。
- Novel first-in-human unknown target：应返回 no_valid_fda_comparator。

## 结论

FDA 同类疗效对比应当是一个独立的 evidence retrieval and validation layer，而不是写死在报告渲染器里的癌种特例。

最重要的原则是：

```text
每个推荐试验药物都尝试寻找 FDA 同类对照；
找到则展示来源绑定的疗效数据；
找不到则说明原因；
永远不为了完整性而虚构或错配疗效数字。
```
