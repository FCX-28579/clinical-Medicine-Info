from __future__ import annotations

import argparse
import datetime as dt
import html
import json
from pathlib import Path
from typing import Any


def h(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def render(patient: dict[str, Any], scored: dict[str, Any], decision: dict[str, Any]) -> str:
    stats = scored.get("search_stats", {})
    biomarkers = ", ".join(
        f"{k}: {v}" for k, v in (patient.get("biomarkers_known") or {}).items() if v is not None
    ) or "未结构化"
    blocks = []
    for path in decision.get("decision_paths", []):
        pending = "".join(f"<li>{h(item)}</li>" for item in path.get("blockers_pending", []))
        trial_id = path.get("trial_id")
        blocks.append(
            f"""
            <section class="trial-card">
              <div class="trial-head">
                <div>
                  <div class="rank">推荐 {h(path.get("rank"))} · {h(path.get("match_status"))}</div>
                  <h2>{h(path.get("trial_title"))}</h2>
                </div>
                <a class="nct" href="https://clinicaltrials.gov/study/{h(trial_id)}" target="_blank" rel="noopener">{h(trial_id)}</a>
              </div>
              <div class="trial-meta">
                <span>申办方：{h(path.get("sponsor"))}</span>
                <span>分期：{h(path.get("phase"))}</span>
                <span>可行性分：{h(path.get("feasibility_score"))}</span>
              </div>
              <p class="rationale">{h(path.get("rationale_one_liner"))}</p>
              <h3>正式筛选前需要确认</h3>
              <ul>{pending}</ul>
            </section>
            """
        )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>NSCLC EGFR 患者 ClinicalTrials.gov 推荐试验报告</title>
  <style>
    :root {{ --ink:#172026; --muted:#5c6975; --line:#d9e0e6; --bg:#f6f8fa; --card:#fff; --accent:#106c74; --warn:#8b5a00; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:"Microsoft YaHei","Noto Sans CJK SC",Arial,sans-serif; color:var(--ink); background:var(--bg); line-height:1.58; }}
    header {{ background:#0f2d33; color:white; padding:28px 38px; }}
    header h1 {{ margin:0 0 8px; font-size:28px; }}
    header p {{ margin:0; color:#d4e4e6; }}
    main {{ max-width:1120px; margin:0 auto; padding:24px; }}
    .summary,.trial-card {{ background:var(--card); border:1px solid var(--line); border-radius:8px; box-shadow:0 1px 2px rgba(20,30,40,.04); }}
    .summary {{ padding:18px 20px; display:grid; grid-template-columns:repeat(4,1fr); gap:14px; margin-bottom:18px; }}
    .summary div {{ border-left:3px solid var(--accent); padding-left:10px; }}
    .label {{ font-size:12px; color:var(--muted); }}
    .value {{ font-weight:700; }}
    .trial-card {{ padding:22px; margin:18px 0; }}
    .trial-head {{ display:flex; justify-content:space-between; gap:16px; align-items:flex-start; }}
    .trial-head h2 {{ margin:4px 0 0; font-size:22px; line-height:1.3; }}
    .rank {{ color:var(--accent); font-weight:800; }}
    .nct {{ white-space:nowrap; color:white; background:var(--accent); padding:7px 10px; border-radius:6px; text-decoration:none; font-weight:700; }}
    .trial-meta {{ display:flex; flex-wrap:wrap; gap:8px; margin:14px 0; }}
    .trial-meta span {{ background:#eef2f5; border:1px solid var(--line); border-radius:6px; padding:5px 8px; }}
    .rationale {{ background:#fff8ea; border-left:4px solid var(--warn); padding:10px 12px; }}
    .note {{ background:#f8fafb; border:1px solid var(--line); border-radius:6px; padding:12px 14px; margin:18px 0; color:#334; }}
    footer {{ max-width:1120px; margin:0 auto; padding:0 24px 28px; color:var(--muted); font-size:13px; }}
    @media(max-width:820px) {{ .summary {{ grid-template-columns:1fr; }} .trial-head {{ flex-direction:column; }} }}
  </style>
</head>
<body>
  <header>
    <h1>NSCLC EGFR 患者 ClinicalTrials.gov 推荐试验报告</h1>
    <p>检索范围：ClinicalTrials.gov only | 生成时间：{h(dt.datetime.now().isoformat(timespec="seconds"))}</p>
  </header>
  <main>
    <section class="summary">
      <div><div class="label">患者</div><div class="value">{h(patient.get("patient_id"))}</div></div>
      <div><div class="label">诊断/分期</div><div class="value">{h(patient.get("pathology"))} / {h(patient.get("stage"))}</div></div>
      <div><div class="label">关键 biomarker</div><div class="value">{h(biomarkers)}</div></div>
      <div><div class="label">治疗线数/ECOG</div><div class="value">{h(patient.get("treatment_lines_completed"))} / {h(patient.get("ecog"))}</div></div>
    </section>
    <section class="note"><strong>检索统计：</strong>{h(stats.get("total_queries"))} 个查询，{h(stats.get("total_raw_results"))} 条原始结果，去重后 {h(stats.get("unique_after_dedup"))} 条，初筛保留 {h(stats.get("included_after_filter"))} 条，排除 {h(stats.get("excluded_by_filter"))} 条，错误 {h(stats.get("errors"))} 个。</section>
    <section class="note"><strong>重要说明：</strong>{h(decision.get("important_note"))}</section>
    {''.join(blocks)}
  </main>
  <footer>本报告为 AI 初筛和医生/CRC 复核前的信息整理，不替代研究中心 screening 或医生治疗决策。</footer>
</body>
</html>"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--patient", required=True, type=Path)
    parser.add_argument("--scored", required=True, type=Path)
    parser.add_argument("--decision-report", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        render(read_json(args.patient), read_json(args.scored), read_json(args.decision_report)),
        encoding="utf-8",
    )
    print(args.out)


if __name__ == "__main__":
    main()
