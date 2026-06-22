# 为推荐临床试验新增动态药物信息报告模块

## PR 标题建议

```text
为推荐临床试验新增动态药物信息报告模块
```

## PR 概要

本 PR 为 ClinicalTrialSKILL 新增一个面向推荐临床试验的动态药物信息报告模块。

在患者完成临床试验匹配并得到推荐试验后，系统会针对每一个推荐试验实时读取 ClinicalTrials.gov 页面/API 信息，识别核心研究药物，并生成中文版药物信息报告。报告重点展示患者和医生复核时更需要关注的内容，包括：

- 推荐试验的核心研究药物
- 厂家/申办方背景
- 同类 FDA 药物疗效对比
- 给患者看的解释
- 质量提示和证据边界

该模块不依赖固定药品证据库，也不是只针对单个测试患者定制。它会根据每个推荐试验页面中的真实药物信息动态生成报告，并在缺少 FDA 标签或公开疗效数据时明确提示，不虚构药物疗效或来源。

## 解决的问题

原有 ClinicalTrialSKILL 可以生成临床试验推荐结果，但推荐报告中缺少面向患者和医生复核的药物解释层。用户看到某个试验被推荐后，仍需要额外查找：

- 这个试验的核心研究药物是什么
- 药物来自哪个申办方或研究方
- 是否已有同类 FDA 药物可以作为疗效参考
- 当前试验药物是否已有公开疗效数据
- 哪些信息来自官方页面，哪些信息需要继续人工复核

本 PR 在不改变原有试验匹配主流程的前提下，为推荐结果增加一个可读、可追溯、可复核的药物信息模块。

## 功能设计

### 1. 每个推荐试验实时生成药物信息

系统会对推荐结果中的每个 `NCT_ID` 调用 ClinicalTrials.gov API：

```text
https://clinicaltrials.gov/api/v2/studies/{NCT_ID}
```

并从真实页面字段中提取：

- 试验标题
- 招募状态
- 试验分期
- 适应症/疾病条件
- lead sponsor
- collaborators
- interventions
- 干预药物名称
- 药物描述
- 是否存在 posted results

### 2. 识别核心研究药物

模块会从 ClinicalTrials.gov 的 intervention 字段中识别核心研究药物，并避免把明显的背景治疗、联合用药或辅助治疗误当作主要研究药物。

报告会保留核心研究药物的中英文介绍：

- 中文介绍：面向中文患者和医生阅读
- English：保留 ClinicalTrials.gov 页面语义，便于回到英文原始页面复核

### 3. 厂家/申办方背景

报告展示 ClinicalTrials.gov 页面中的 lead sponsor 和 collaborators。

质量边界：

- 申办方信息按 ClinicalTrials.gov 页面展示。
- 不自动等同于药品上市许可持有人。
- 不补写页面中没有提供的公司背景、融资信息或商业化信息。

### 4. 同类 FDA 药物疗效对比

模块会根据推荐试验的癌种、biomarker、靶点和核心研究药物，实时检索 openFDA 药品标签，提取同类 FDA 药物中的疗效指标。

目前支持的主要信息包括：

- ORR
- Median PFS
- Median OS
- Median DFS
- Median DOR

示例：

对于 CRC / KRAS G12C 患者的推荐试验，系统可以识别同类 FDA 药物：

```text
LUMAKRAS / SOTORASIB -> ORR 36%
KRAZATI / ADAGRASIB -> ORR 43%
```

同类药物对照只用于帮助患者和医生理解治疗方向，不代表当前推荐试验药物已经证明具有同等疗效。

### 5. FDA 来源链接处理

系统使用 openFDA API 进行实时读取和结构化解析。

当存在已验证、可稳定访问的 FDA 人类可读审批页面时，报告优先展示 FDA 网页链接。例如：

```text
https://www.fda.gov/drugs/resources-information-approved-drugs/fda-approves-sotorasib-panitumumab-kras-g12c-mutated-colorectal-cancer
```

当无法验证对应 FDA 网页稳定存在时，报告保留 openFDA API 链接作为可追溯来源，避免生成 404 页面或不确定链接。

### 6. 质量提示

报告会明确说明：

- 当前研究药物如果没有 openFDA 标签，不会生成虚假的 FDA 信息。
- ClinicalTrials.gov 页面没有公开结果时，不虚构本药疗效数字。
- 同类 FDA 药物数据仅作参考，不等同于当前试验药物疗效。
- 试验最终是否可入组仍需研究中心、医生或 CRC 进行正式筛查。

## 主要实现文件

### 药物信息生成逻辑

```text
skills/clinical-trial-matching/scripts/drug_info/dynamic_ctgov_drug_info.py
```

职责：

- 实时读取 ClinicalTrials.gov study API
- 解析推荐试验页面中的药物、申办方、分期、状态等字段
- 识别核心研究药物
- 生成患者可读说明和质量提示

### FDA 同类药物对照模块

```text
skills/clinical-trial-matching/scripts/drug_info/fda_comparator/
```

新增子模块：

```text
__init__.py
query_builder.py
retriever.py
parsers.py
fit_scorer.py
finder.py
```

职责：

- 根据试验癌种、靶点和核心药物构建 FDA 候选药物
- 实时读取 openFDA label
- 评估 FDA label 与当前试验场景的相关性
- 从 FDA label 中提取 ORR、PFS、OS、DOR 等疗效指标
- 对同一报告内重复 FDA 请求进行缓存
- 在存在已验证 FDA 可读网页时优先展示网页链接

### 中文 HTML 报告渲染

```text
skills/clinical-trial-matching/scripts/render/ctgov_dynamic_drug_report_zh.py
```

职责：

- 读取患者信息、推荐试验结果和评分结果
- 对每个推荐试验生成药物信息模块
- 输出中文版 HTML 报告
- 输出结构化 JSON 结果

### 单元测试

```text
skills/clinical-trial-matching/scripts/drug_info/test_dynamic_ctgov_drug_info.py
```

覆盖：

- 核心研究药物识别
- 背景/联合药物不误判为核心药物
- 无 posted results 时不生成本药疗效数字

## 测试病例

使用 CRC / KRAS G12C 测试患者：

```text
patient_id: PT-17CE02BC33
癌种: 结直肠癌
分子特征: KRAS G12C, MSS
搜索范围: ClinicalTrials.gov only
```

生成报告：

```text
D:\Desktop\Comparision\clinical_trial_reports\PT-17CE02BC33_ctgov_only_dynamic_drug_info_after_generic_fda\drug_info_report_zh.html
```

结构化结果：

```text
D:\Desktop\Comparision\clinical_trial_reports\PT-17CE02BC33_ctgov_only_dynamic_drug_info_after_generic_fda\dynamic_drug_modules_zh.json
```

生成结果：

```text
module_count: 3
error_count: 0
```

推荐试验示例：

```text
NCT05410145 -> D3S-001
NCT05288205 -> JAB-21822 + JAB-3312
NCT06447662 -> PF-07934040
```

同类 FDA 药物对照示例：

```text
LUMAKRAS / SOTORASIB -> ORR 36%
KRAZATI / ADAGRASIB -> ORR 43%
```

## 验证结果

已执行：

```text
python -X utf8 -m py_compile ...
python -X utf8 -m unittest test_dynamic_ctgov_drug_info.py
```

结果：

```text
Ran 3 tests
OK
```

人工抽查结果：

- 中文报告可正常生成。
- 每个推荐试验均生成药物信息模块。
- 核心研究药物有中英文介绍。
- 厂家/申办方背景正常显示。
- 同类 FDA 药物疗效对照正常显示。
- LUMAKRAS 来源优先展示 FDA 人类可读审批页面。
- KRAZATI 未验证到稳定 FDA 可读网页时保留 openFDA API 链接。
- 没有 FDA 标签的研究药物不会生成虚假 FDA 信息。

## 质量控制原则

- 不虚构药品疗效。
- 不虚构 FDA 页面链接。
- 不把同类药物疗效说成当前试验药物疗效。
- 不把 ClinicalTrials.gov 申办方自动等同于药品上市许可持有人。
- 所有药物信息都保留来源链接，方便医生或 CRC 复核。
- FDA 可读网页只有在验证可访问时才替换 openFDA API 链接。

## 如何在 GitHub 上提交这个 PR

当前本地开发目录不是 git 仓库，因此不能直接在本地执行 `gh pr create`。可以按下面方式提交到 GitHub 原项目。

### 方式一：网页上传/编辑提交

适合不想处理 git 命令的情况。

1. 打开 GitHub 原项目页面。
2. 点击 `Fork`，将项目 fork 到自己的账号下。
3. 在 fork 后的仓库中新建分支，例如：

```text
feature/dynamic-drug-info-report
```

4. 将本地开发目录中的改动文件复制到 fork 仓库对应路径。
5. 在 GitHub 网页中提交 commit，commit message 建议：

```text
Add dynamic drug information report for recommended clinical trials
```

6. 回到 GitHub，点击 `Compare & pull request`。
7. PR 标题使用：

```text
为推荐临床试验新增动态药物信息报告模块
```

8. PR 正文复制本文档中 “PR 概要” 到 “质量控制原则” 的内容。
9. 确认 base branch 是原项目主分支，compare branch 是自己的 `feature/dynamic-drug-info-report`。
10. 点击 `Create pull request`。

### 方式二：本地 git 提交

适合已经有原项目 git 仓库的情况。

1. 克隆原项目或进入已有 git 仓库：

```bash
git clone <原项目 GitHub 地址>
cd <原项目目录>
```

2. 新建分支：

```bash
git checkout -b feature/dynamic-drug-info-report
```

3. 将本地开发目录中的改动文件复制到该 git 仓库对应路径。

4. 查看变更：

```bash
git status
git diff
```

5. 提交：

```bash
git add skills/clinical-trial-matching/scripts/drug_info/dynamic_ctgov_drug_info.py
git add skills/clinical-trial-matching/scripts/drug_info/fda_comparator
git add skills/clinical-trial-matching/scripts/render/ctgov_dynamic_drug_report_zh.py
git add skills/clinical-trial-matching/scripts/drug_info/test_dynamic_ctgov_drug_info.py
git commit -m "Add dynamic drug information report for recommended clinical trials"
```

6. 推送分支：

```bash
git push origin feature/dynamic-drug-info-report
```

7. 打开 GitHub，根据页面提示创建 Pull Request。

## 建议提交的文件清单

请至少确认这些文件已包含在 PR 中：

```text
skills/clinical-trial-matching/scripts/drug_info/dynamic_ctgov_drug_info.py
skills/clinical-trial-matching/scripts/drug_info/fda_comparator/__init__.py
skills/clinical-trial-matching/scripts/drug_info/fda_comparator/query_builder.py
skills/clinical-trial-matching/scripts/drug_info/fda_comparator/retriever.py
skills/clinical-trial-matching/scripts/drug_info/fda_comparator/parsers.py
skills/clinical-trial-matching/scripts/drug_info/fda_comparator/fit_scorer.py
skills/clinical-trial-matching/scripts/drug_info/fda_comparator/finder.py
skills/clinical-trial-matching/scripts/render/ctgov_dynamic_drug_report_zh.py
skills/clinical-trial-matching/scripts/drug_info/test_dynamic_ctgov_drug_info.py
```

如果希望把 PR 正文也保存在仓库中，可以额外提交：

```text
PULL_REQUEST_ZH.md
```
