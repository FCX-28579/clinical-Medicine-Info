"""
dual_source_search.py — LLM 生成关键词 → 并行查询 ClinicalTrials.gov + ChiCTR

使用方式:
  1. LLM 生成关键词 JSON 文件 (见 generate_search_plan)
  2. 脚本读取 JSON, 并行查询两个数据源, 输出去重合并结果

  python dual_source_search.py --plan search_plan.json --out results.json

  或作为模块被 LLM agent 调用:
  from dual_source_search import execute_search_plan
  results = execute_search_plan(plan)
"""

import json
import sys
import argparse
import urllib.request
import urllib.parse
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional


# ---------------------------------------------------------------------------
# 1. Search plan schema (由 LLM 填充)
# ---------------------------------------------------------------------------
SEARCH_PLAN_SCHEMA = {
    "patient_summary": "str — 一句话患者摘要",
    "treatment_lines": "int — 已完成治疗线数, 用于硬筛选",
    "keyword_groups": [
        {
            "label": "str — 关键词组标签, 如 '疾病+突变特异'",
            "source": "both | nct | chictr",
            "queries": [
                {
                    "condition": "str | null — ClinicalTrials.gov cond 字段",
                    "term": "str — 搜索词",
                }
            ],
        }
    ],
    "hard_exclude": {
        "first_line_only": True,
        "molecular_mismatch": ["str — 如 'RAS wild-type only'"],
    },
}


def generate_search_plan_prompt(patient_text: str) -> str:
    """返回给 LLM 的 prompt, 让 LLM 输出 search plan JSON."""
    return f"""你是一个临床试验检索专家。基于以下患者病历，生成一份结构化的检索计划 (JSON)。

要求:
1. 提取患者摘要、已完成治疗线数
2. 生成多维度关键词组:
   - 疾病+突变 特异关键词 (如: colorectal cancer KRAS G12C)
   - 泛化关键词 (如: solid tumor KRAS G12C) — 避免遗漏以"实体瘤"为入选标准的试验
   - 联合靶点关键词 (如: KRAS G12C SHP2 inhibitor)
   - 通路靶向关键词 (如: ATM PARP inhibitor, 基于患者特有的通路突变)
   - 细胞治疗关键词 — 不绑定突变类型 (如: CAR-T colorectal, TIL solid tumor)
   - 免疫治疗关键词 (基于 MSS/MSI 状态)
   - 中文关键词 (用于 ChiCTR 查询)
3. 定义硬排除规则 (治疗线数不匹配、分子特征不匹配)

输出严格遵循以下 JSON 格式, 不要输出其他内容:

{{
  "patient_summary": "70岁男性, 乙状结肠腺癌 IV期...",
  "treatment_lines": 5,
  "keyword_groups": [
    {{
      "label": "疾病+突变特异",
      "source": "both",
      "queries": [
        {{"condition": "colorectal cancer", "term": "KRAS G12C"}},
        {{"condition": null, "term": "KRAS G12C colorectal"}}
      ]
    }},
    {{
      "label": "泛化-实体瘤",
      "source": "nct",
      "queries": [
        {{"condition": "solid tumor", "term": "KRAS G12C"}}
      ]
    }},
    {{
      "label": "细胞治疗 (不限突变)",
      "source": "both",
      "queries": [
        {{"condition": "colorectal cancer", "term": "CAR-T"}},
        {{"condition": "solid tumor", "term": "TIL"}},
        {{"condition": null, "term": "CEA CAR-T"}},
        {{"condition": null, "term": "GUCY2C CAR-T"}}
      ]
    }}
  ],
  "hard_exclude": {{
    "first_line_only": true,
    "molecular_mismatch": ["RAS wild-type only"]
  }}
}}

患者病历:
{patient_text}

JSON output:"""


# ---------------------------------------------------------------------------
# 2. ClinicalTrials.gov API 查询
# ---------------------------------------------------------------------------
CT_GOV_BASE = "https://clinicaltrials.gov/api/v2/studies"


def parse_eligibility_criteria(elig_text: str) -> dict:
    """
    将 eligibilityCriteria 全文解析为结构化的入选/排除标准列表。

    借鉴 TrialMatchAI 的 criterion-level 评估思路:
    将整段文本按条目拆分, 便于后续 LLM 逐条评估。

    Returns:
        {
            "inclusion": ["criterion 1", "criterion 2", ...],
            "exclusion": ["criterion 1", "criterion 2", ...],
            "raw": "原始文本"
        }
    """
    inclusion = []
    exclusion = []
    current_section = None

    for line in elig_text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue

        lower = stripped.lower()
        if "inclusion" in lower and "criteria" in lower:
            current_section = "inclusion"
            continue
        elif "exclusion" in lower and "criteria" in lower:
            current_section = "exclusion"
            continue

        # 跳过非标准行
        if stripped.startswith("*") or stripped.startswith("-") or stripped.startswith("•"):
            stripped = stripped.lstrip("*-• ").strip()

        # 编号行: "1.", "1)", "1:"
        import re
        stripped = re.sub(r"^\d+[\.\)\:]\s*", "", stripped)

        if not stripped or len(stripped) < 5:
            continue

        if current_section == "inclusion":
            inclusion.append(stripped)
        elif current_section == "exclusion":
            exclusion.append(stripped)

    return {
        "inclusion": inclusion,
        "exclusion": exclusion,
        "raw": elig_text,
    }


def query_ctgov(condition: Optional[str], term: str, max_results: int = 10) -> list:
    """查询 ClinicalTrials.gov API v2, 返回结构化试验列表."""
    params = {
        "query.term": term,
        "filter.overallStatus": "RECRUITING",
        "pageSize": str(max_results),
    }
    if condition:
        params["query.cond"] = condition

    url = CT_GOV_BASE + "?" + urllib.parse.urlencode(params)

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "TrialGPT/1.3"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read())
    except (urllib.error.URLError, urllib.error.HTTPError, Exception) as e:
        return [{"error": str(e), "query": {"condition": condition, "term": term}}]

    trials = []
    for study in data.get("studies", []):
        proto = study.get("protocolSection", {})
        ident = proto.get("identificationModule", {})
        design = proto.get("designModule", {})
        arms = proto.get("armsInterventionsModule", {})
        elig = proto.get("eligibilityModule", {})
        contacts = proto.get("contactsLocationsModule", {})
        sponsor_mod = proto.get("sponsorCollaboratorsModule", {})

        nctid = ident.get("nctId", "")
        title = ident.get("briefTitle", "")
        phases = design.get("phases", [])
        sponsor = sponsor_mod.get("leadSponsor", {}).get("name", "")

        interventions = [
            iv.get("name", "") for iv in arms.get("interventions", [])
        ]

        elig_text = elig.get("eligibilityCriteria", "")

        # 解析入排标准为结构化列表
        parsed_criteria = parse_eligibility_criteria(elig_text)

        # 提取中国临床中心
        china_sites = []
        for loc in contacts.get("locations", []):
            if loc.get("country", "") == "China":
                pi_name = ""
                for ct in loc.get("contacts", []):
                    if ct.get("role", "") in (
                        "PRINCIPAL_INVESTIGATOR",
                        "CONTACT",
                    ):
                        pi_name = ct.get("name", "")
                        break
                china_sites.append(
                    {
                        "facility": loc.get("facility", ""),
                        "city": loc.get("city", ""),
                        "contact": pi_name,
                    }
                )

        # 治疗线数判断
        elig_lower = elig_text.lower()
        line_info = "unknown"
        if any(
            kw in elig_lower
            for kw in [
                "first-line",
                "first line",
                "treatment-naïve",
                "treatment naive",
                "treatment-naive",
                "no prior systemic",
                "not received systemic",
            ]
        ):
            line_info = "first_line"
        elif any(
            kw in elig_lower
            for kw in ["failed", "progressed", "prior treatment", "second-line", "≥2"]
        ):
            line_info = "2L+"

        # 既往 KRAS 抑制剂排除
        prior_kras_excluded = False
        if "kras" in elig_lower and any(
            kw in elig_lower for kw in ["prior", "previous", "received"]
        ):
            if "inhibitor" in elig_lower or "g12c" in elig_lower:
                prior_kras_excluded = True

        trials.append(
            {
                "id": nctid,
                "source": "NCT",
                "title": title,
                "phases": phases,
                "sponsor": sponsor,
                "interventions": interventions,
                "line_info": line_info,
                "prior_kras_inhibitor_excluded": prior_kras_excluded,
                "china_sites": china_sites,
                "china_site_count": len(china_sites),
                "eligibility_excerpt": elig_text[:800],
                "eligibility_full": elig_text,
                "parsed_criteria": parsed_criteria,
            }
        )

    return trials


# ---------------------------------------------------------------------------
# 3. ChiCTR MCP 查询 (通过 subprocess 调用 MCP tool)
# ---------------------------------------------------------------------------
def query_chictr_via_mcp(keyword: str, max_results: int = 10) -> list:
    """
    ChiCTR 查询占位 — 实际由 LLM agent 调用 MCP tool 完成。
    当脚本独立运行时, 此函数返回空列表并打印提示。
    当作为模块被 agent 调用时, agent 应替换此函数或传入结果。
    """
    # 此处为占位实现; 真正的查询由 agent 通过 MCP tool 完成
    return []


# ---------------------------------------------------------------------------
# 4. 执行检索计划 (并行)
# ---------------------------------------------------------------------------
def execute_search_plan(
    plan: dict, max_per_query: int = 10, chictr_results: Optional[list] = None
) -> dict:
    """
    执行 LLM 生成的检索计划, 并行查询, 去重合并。

    Args:
        plan: LLM 生成的 search plan JSON
        max_per_query: 每组查询最大返回数
        chictr_results: 如果 agent 已通过 MCP 查询了 ChiCTR, 直接传入结果

    Returns:
        {
            "all_trials": [...],           # 去重后全部试验
            "included_trials": [...],      # 硬筛选后保留的试验
            "excluded_trials": [...],      # 硬筛选排除的试验 (含排除原因)
            "search_stats": {...},         # 检索统计
        }
    """
    treatment_lines = plan.get("treatment_lines", 0)
    hard_exclude = plan.get("hard_exclude", {})
    keyword_groups = plan.get("keyword_groups", [])

    # ---- 并行查询 ClinicalTrials.gov ----
    all_nct_trials = []
    nct_queries = []

    for group in keyword_groups:
        source = group.get("source", "both")
        if source in ("both", "nct"):
            for q in group.get("queries", []):
                nct_queries.append(
                    {
                        "condition": q.get("condition"),
                        "term": q.get("term", ""),
                        "label": group.get("label", ""),
                    }
                )

    with ThreadPoolExecutor(max_workers=5) as executor:
        future_map = {}
        for q in nct_queries:
            future = executor.submit(
                query_ctgov, q["condition"], q["term"], max_per_query
            )
            future_map[future] = q

        for future in as_completed(future_map):
            q = future_map[future]
            try:
                results = future.result()
                for trial in results:
                    if "error" not in trial:
                        trial["matched_by"] = q["label"]
                all_nct_trials.extend(results)
            except Exception as e:
                all_nct_trials.append(
                    {"error": str(e), "query": q}
                )

    # ---- 合并 ChiCTR 结果 ----
    all_chictr_trials = chictr_results or []

    # ---- 去重 (按 trial ID) ----
    seen_ids = set()
    unique_trials = []
    for trial in all_nct_trials + all_chictr_trials:
        if "error" in trial:
            continue
        tid = trial.get("id", "")
        if tid and tid not in seen_ids:
            seen_ids.add(tid)
            unique_trials.append(trial)

    # ---- 硬筛选 ----
    included = []
    excluded = []

    for trial in unique_trials:
        exclude_reason = None

        # 规则 1: 一线试验 vs 多线患者
        if (
            hard_exclude.get("first_line_only", False)
            and treatment_lines >= 2
            and trial.get("line_info") == "first_line"
        ):
            exclude_reason = f"仅限一线治疗，患者已{treatment_lines}线"

        # 规则 2: 分子特征不匹配
        if not exclude_reason:
            for mismatch in hard_exclude.get("molecular_mismatch", []):
                mismatch_lower = mismatch.lower()
                title_lower = trial.get("title", "").lower()
                elig_lower = trial.get("eligibility_excerpt", "").lower()
                if (
                    "wild-type" in mismatch_lower
                    and "wild" in (title_lower + " " + elig_lower)
                    and "type" in (title_lower + " " + elig_lower)
                ):
                    exclude_reason = f"分子特征不匹配: {mismatch}"
                    break

        if exclude_reason:
            trial["exclude_reason"] = exclude_reason
            excluded.append(trial)
        else:
            included.append(trial)

    # ---- 统计 ----
    stats = {
        "total_queries": len(nct_queries),
        "total_raw_results": len(all_nct_trials) + len(all_chictr_trials),
        "unique_after_dedup": len(unique_trials),
        "included_after_filter": len(included),
        "excluded_by_filter": len(excluded),
        "errors": len([t for t in all_nct_trials if "error" in t]),
    }

    return {
        "all_trials": unique_trials,
        "included_trials": included,
        "excluded_trials": excluded,
        "search_stats": stats,
    }


# ---------------------------------------------------------------------------
# 5. 报告校验: 验证试验 ID 准确性
# ---------------------------------------------------------------------------
def verify_trial_ids(trial_ids: list) -> dict:
    """
    对一组 NCT/ChiCTR ID 逐个调用官方 API 校验是否存在且信息一致。

    Args:
        trial_ids: ["NCT06585488", "ChiCTR2600119904", ...]

    Returns:
        {
            "results": [
                {"id": "NCT06585488", "status": "valid", "title": "...", "recruiting": True, ...},
                {"id": "NCT06584488", "status": "invalid", "error": "404 Not Found"},
                ...
            ],
            "summary": {"total": 9, "valid": 8, "invalid": 1}
        }
    """
    results = []

    for tid in trial_ids:
        tid = tid.strip()
        if tid.startswith("NCT"):
            url = f"https://clinicaltrials.gov/api/v2/studies/{tid}"
            try:
                req = urllib.request.Request(
                    url, headers={"User-Agent": "TrialGPT/1.3"}
                )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read())

                proto = data.get("protocolSection", {})
                ident = proto.get("identificationModule", {})
                status_mod = proto.get("statusModule", {})
                sponsor_mod = proto.get("sponsorCollaboratorsModule", {})
                design = proto.get("designModule", {})
                contacts = proto.get("contactsLocationsModule", {})

                china_count = sum(
                    1
                    for loc in contacts.get("locations", [])
                    if loc.get("country", "") == "China"
                )

                results.append(
                    {
                        "id": tid,
                        "status": "valid",
                        "title": ident.get("briefTitle", ""),
                        "sponsor": sponsor_mod.get("leadSponsor", {}).get(
                            "name", ""
                        ),
                        "phases": design.get("phases", []),
                        "recruiting": status_mod.get("overallStatus", "")
                        == "RECRUITING",
                        "overall_status": status_mod.get("overallStatus", ""),
                        "china_site_count": china_count,
                    }
                )
            except urllib.error.HTTPError as e:
                results.append(
                    {
                        "id": tid,
                        "status": "invalid",
                        "error": f"HTTP {e.code}: {e.reason}",
                    }
                )
            except Exception as e:
                results.append(
                    {"id": tid, "status": "error", "error": str(e)}
                )

        elif tid.startswith("ChiCTR"):
            # ChiCTR 校验需要通过 MCP tool, 此处标记为 unchecked
            results.append(
                {
                    "id": tid,
                    "status": "unchecked",
                    "note": "ChiCTR ID 需通过 MCP tool 验证",
                }
            )
        else:
            results.append(
                {
                    "id": tid,
                    "status": "invalid",
                    "error": f"Unrecognized ID format: {tid}",
                }
            )

    valid = sum(1 for r in results if r["status"] == "valid")
    invalid = sum(1 for r in results if r["status"] == "invalid")

    return {
        "results": results,
        "summary": {
            "total": len(results),
            "valid": valid,
            "invalid": invalid,
            "unchecked": len(results) - valid - invalid,
        },
    }


# ---------------------------------------------------------------------------
# 6. CLI 入口
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Execute a TrialGPT search plan against ClinicalTrials.gov + ChiCTR"
    )
    parser.add_argument(
        "--plan", help="Path to search plan JSON (LLM generated). Required for search mode."
    )
    parser.add_argument(
        "--out", default="search_results.json", help="Output path for results JSON"
    )
    parser.add_argument(
        "--max-per-query", type=int, default=10, help="Max results per query"
    )
    parser.add_argument(
        "--verify",
        help="Verify trial IDs from a comma-separated list or a file (one ID per line). "
        "Example: --verify NCT06585488,NCT05288205 or --verify ids.txt",
    )
    args = parser.parse_args()

    # ---- Verify mode ----
    if args.verify:
        # Accept comma-separated IDs or a file path
        import os

        if os.path.isfile(args.verify):
            with open(args.verify, "r") as f:
                ids = [line.strip() for line in f if line.strip()]
        else:
            ids = [x.strip() for x in args.verify.split(",") if x.strip()]

        print(f"Verifying {len(ids)} trial IDs...")
        vresult = verify_trial_ids(ids)

        for r in vresult["results"]:
            if r["status"] == "valid":
                recruiting = "RECRUITING" if r.get("recruiting") else r.get("overall_status", "?")
                print(
                    f"  ✅ {r['id']} | {recruiting} | CN:{r.get('china_site_count', 0)} | {r['title'][:70]}"
                )
            elif r["status"] == "invalid":
                print(f"  ❌ {r['id']} | INVALID: {r['error']}")
            elif r["status"] == "unchecked":
                print(f"  ⚠️  {r['id']} | {r.get('note', 'unchecked')}")
            else:
                print(f"  ⚠️  {r['id']} | ERROR: {r.get('error', '?')}")

        s = vresult["summary"]
        print(f"\nSummary: {s['valid']} valid, {s['invalid']} invalid, {s['unchecked']} unchecked / {s['total']} total")

        if args.out != "search_results.json":
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(vresult, f, indent=2, ensure_ascii=False)
            print(f"Verification results saved to: {args.out}")
        return

    # ---- Search mode ----
    if not args.plan:
        parser.error("--plan is required for search mode")

    with open(args.plan, "r") as f:
        plan = json.load(f)

    print(f"Loaded search plan: {len(plan.get('keyword_groups', []))} keyword groups")
    print(f"Patient treatment lines: {plan.get('treatment_lines', '?')}")
    print(f"Executing parallel queries...")

    results = execute_search_plan(plan, max_per_query=args.max_per_query)

    stats = results["search_stats"]
    print(f"\nSearch complete:")
    print(f"  Queries executed: {stats['total_queries']}")
    print(f"  Raw results: {stats['total_raw_results']}")
    print(f"  After dedup: {stats['unique_after_dedup']}")
    print(f"  After filter: {stats['included_after_filter']} included, {stats['excluded_by_filter']} excluded")
    if stats["errors"] > 0:
        print(f"  Errors: {stats['errors']}")

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"\nResults saved to: {args.out}")


if __name__ == "__main__":
    main()
