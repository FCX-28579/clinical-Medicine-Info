from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "drug_info"))

from dynamic_ctgov_drug_info import build_dynamic_modules  # noqa: E402


def h(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def trial_url(nct_id: str) -> str:
    return f"https://clinicaltrials.gov/study/{nct_id}"


def patient_summary(patient: dict[str, Any]) -> dict[str, Any]:
    return {
        "patient_id": patient.get("patient_id"),
        "diagnosis": patient.get("diagnosis") or patient.get("cancer_type"),
        "stage": patient.get("stage"),
        "biomarkers": patient.get("biomarkers_known", {}),
        "treatment_lines_completed": patient.get("treatment_lines_completed"),
        "ecog": patient.get("ECOG") or patient.get("ecog"),
    }


def fallback_decision_report(patient: dict[str, Any], scored: Any, top_n: int = 3) -> dict[str, Any]:
    included = scored.get("included_trials", []) if isinstance(scored, dict) else list(scored)
    ranked = sorted(
        included,
        key=lambda t: (
            t.get("feasibility", {}).get("composite") is None,
            -(t.get("feasibility", {}).get("composite") or 0),
        ),
    )[:top_n]
    paths = []
    for idx, trial in enumerate(ranked, start=1):
        paths.append(
            {
                "rank": idx,
                "trial_id": trial.get("id"),
                "trial_title": trial.get("title"),
                "sponsor": trial.get("sponsor"),
                "phase": "/".join(trial.get("phases", [])),
                "feasibility_score": trial.get("feasibility", {}).get("composite"),
                "rationale_one_liner": "该试验来自 ClinicalTrials.gov 检索结果，与患者癌种、分子特征或治疗线索相关；仍需医生/CRC 正式筛选。",
                "blockers_pending": [
                    "确认当前队列仍开放且可接收该患者人群",
                    "复核 RECIST 1.1 可测量病灶、ECOG 和近期实验室检查",
                    "复核既往治疗和 washout 时间窗",
                ],
            }
        )
    return {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "patient_summary": patient_summary(patient),
        "source_scope": "ClinicalTrials.gov only",
        "decision_paths": paths,
    }


def load_decision_report(patient: dict[str, Any], scored: Any, path: Path | None) -> dict[str, Any]:
    if path and path.exists():
        return read_json(path)
    return fallback_decision_report(patient, scored)


def render_sources(sources: list[dict[str, Any]]) -> str:
    if not sources:
        return ""
    items = "".join(
        f'<li><a href="{h(src.get("url"))}" target="_blank" rel="noopener">{h(src.get("label") or src.get("url"))}</a></li>'
        for src in sources
    )
    return f"<ul class=\"sources\">{items}</ul>"


def render_core_drugs(module: dict[str, Any]) -> str:
    drugs = module.get("core_study_drugs", []) or []
    if not drugs:
        return '<p class="muted">未能从试验页面稳定识别核心研究药物，需人工复核。</p>'
    cards = []
    for drug in drugs:
        other = ", ".join(drug.get("other_names", [])) or "无"
        cards.append(
            f"""
            <div class="core-drug">
              <strong>{h(drug.get("name"))}</strong>
              <span>{h(drug.get("type"))}</span>
              <p><b>中文介绍：</b>{h(drug.get("intro_zh") or drug.get("description"))}</p>
              <p><b>English:</b> {h(drug.get("intro_en") or drug.get("description"))}</p>
              <small>别名：{h(other)}</small>
            </div>
            """
        )
    return f"<div class=\"core-grid\">{''.join(cards)}</div>"


def render_metric_table(metrics: list[dict[str, Any]]) -> str:
    if not metrics:
        return '<p class="muted">暂无可引用的公开 ORR/PFS/OS/DOR 数字。</p>'
    rows = []
    for metric in metrics:
        src = metric.get("source", {})
        src_url = src.get("display_url") or src.get("url")
        src_label = src.get("display_label") or src.get("label")
        rows.append(
            "<tr>"
            f"<td>{h(metric.get('name'))}</td>"
            f"<td>{h(metric.get('value'))}</td>"
            f"<td>{h(metric.get('comparison'))}</td>"
            f'<td><a href="{h(src_url)}" target="_blank" rel="noopener">{h(src_label)}</a></td>'
            "</tr>"
        )
    return (
        '<table class="metric-table"><thead><tr><th>指标</th><th>结果</th><th>比较方式</th><th>来源</th></tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def render_trial_evidence(module: dict[str, Any]) -> str:
    evidence = module.get("ctgov_results_summary", {})
    source = evidence.get("source", {})
    return f"""
    <div class="evidence">
      <div class="mini-title">{h(module.get("trial_id"))} | {'/'.join(module.get("phase", [])) or '分期未标明'} | {h(module.get("status"))}</div>
      <p>{h(evidence.get("summary"))}</p>
      {render_metric_table(evidence.get("metrics", []))}
      <p class="source-line">来源：<a href="{h(source.get("url") or module.get("source_url"))}" target="_blank" rel="noopener">{h(source.get("label") or 'ClinicalTrials.gov 试验页面')}</a></p>
    </div>
    """


def render_comparators(module: dict[str, Any]) -> str:
    comparison = module.get("same_class_comparison", {})
    comparators = comparison.get("comparators", []) or []
    if not comparators:
        failures = comparison.get("failures", []) or []
        failure_note = ""
        if failures:
            items = "".join(
                f"<li>{h(f.get('candidate'))}: {h(f.get('reason'))}</li>"
                for f in failures[:8]
            )
            failure_note = f"<details><summary>FDA 检索尝试记录</summary><ul>{items}</ul></details>"
        return f'<p class="muted">{h(comparison.get("summary") or "未获得同类药物对照数据。")}</p>{failure_note}'
    blocks = []
    for comp in comparators:
        src = comp.get("source", {})
        src_url = src.get("display_url") or src.get("url")
        src_label = src.get("display_label") or src.get("label")
        blocks.append(
            f"""
            <div class="comparator">
              <h5>{h(comp.get("display_name"))}</h5>
              <p>{h(comp.get("summary"))}</p>
              {render_metric_table(comp.get("metrics", []))}
              <p class="source-line">FDA 来源：<a href="{h(src_url)}" target="_blank" rel="noopener">{h(src_label)}</a></p>
            </div>
            """
        )
    errors = (comparison.get("errors", []) or []) + (comparison.get("failures", []) or [])
    error_note = ""
    if errors:
        error_items = "".join(f"<li>{h(e.get('source') or e.get('candidate'))}: {h(e.get('error') or e.get('reason'))}</li>" for e in errors[:8])
        error_note = f"<details><summary>其他 FDA 检索尝试记录</summary><ul>{error_items}</ul></details>"
    return f"<p>{h(comparison.get('summary'))}</p>{''.join(blocks)}{error_note}"


def render_drug_module(module: dict[str, Any]) -> str:
    manufacturer = module.get("manufacturer", {})
    notes = "".join(f"<li>{h(note)}</li>" for note in module.get("patient_readable_notes", []))
    flags = "".join(f"<li>{h(flag)}</li>" for flag in module.get("quality_flags", []))
    core_names = " + ".join(d.get("name", "") for d in module.get("core_study_drugs", []) if d.get("name")) or "核心研究药物"
    return f"""
    <section class="drug-card">
      <div class="drug-head">
        <h4>{h(core_names)}</h4>
        <span>药品说明书式模块</span>
      </div>
      <h5>核心研究药物</h5>
      {render_core_drugs(module)}
      <div>
        <h5>厂家/申办方背景</h5>
        <p><strong>{h(manufacturer.get("name"))}</strong></p>
        <p>{h(manufacturer.get("background"))}</p>
        {render_sources(manufacturer.get("sources", []))}
      </div>
      <h5>同类药物疗效对比</h5>
      <div class="comparators">{render_comparators(module)}</div>
      <h5>给患者看的解释</h5>
      <ul>{notes}</ul>
      <h5>质量提示</h5>
      <ul>{flags}</ul>
      <p class="source-policy">{h(module.get("source_policy"))}</p>
    </section>
    """


def render_report(patient: dict[str, Any], decision_report: dict[str, Any], dynamic: dict[str, Any]) -> str:
    ps = decision_report.get("patient_summary") or patient_summary(patient)
    modules = dynamic.get("modules", {})
    errors = dynamic.get("errors", {})
    biomarkers = ps.get("biomarkers") if isinstance(ps.get("biomarkers"), dict) else {}
    biomarker_text = ", ".join(f"{k}: {v}" for k, v in biomarkers.items()) or "未结构化"
    path_blocks = []
    for path in decision_report.get("decision_paths", []):
        trial_id = path.get("trial_id")
        pending = "".join(f"<li>{h(item)}</li>" for item in path.get("blockers_pending", []))
        if trial_id in modules:
            module_html = render_drug_module(modules[trial_id])
        else:
            module_html = f'<div class="error">药品模块生成失败：{h(errors.get(trial_id, "未知错误"))}</div>'
        path_blocks.append(
            f"""
            <section class="trial-card">
              <div class="trial-head">
                <div>
                  <div class="rank">推荐 {h(path.get("rank"))}</div>
                  <h3>{h(path.get("trial_title"))}</h3>
                </div>
                <a class="nct" href="{trial_url(trial_id)}" target="_blank" rel="noopener">{h(trial_id)}</a>
              </div>
              <div class="trial-meta">
                <span>申办方：{h(path.get("sponsor"))}</span>
                <span>分期：{h(path.get("phase"))}</span>
                <span>可行性分：{h(path.get("feasibility_score"))}</span>
              </div>
              <p class="rationale">{h(path.get("rationale_one_liner") or path.get("rationale"))}</p>
              <div class="pending">
                <h4>正式筛选前还要确认</h4>
                <ul>{pending}</ul>
              </div>
              {module_html}
            </section>
            """
        )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>ClinicalTrialSKILL 动态药品信息增强报告</title>
  <style>
    :root {{
      --ink:#172026; --muted:#5c6975; --line:#d9e0e6; --bg:#f6f8fa;
      --card:#ffffff; --accent:#106c74; --accent2:#8b5a00; --soft:#e7f3f4;
    }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:"Microsoft YaHei","Noto Sans CJK SC",Arial,sans-serif; color:var(--ink); background:var(--bg); line-height:1.55; }}
    header {{ background:#0f2d33; color:white; padding:28px 38px; }}
    header h1 {{ margin:0 0 8px; font-size:28px; letter-spacing:0; }}
    header p {{ margin:0; color:#d4e4e6; }}
    main {{ max-width:1180px; margin:0 auto; padding:24px; }}
    .summary,.trial-card,.drug-card {{ background:var(--card); border:1px solid var(--line); border-radius:8px; box-shadow:0 1px 2px rgba(20,30,40,.04); }}
    .summary {{ padding:18px 20px; display:grid; grid-template-columns:repeat(4,1fr); gap:14px; margin-bottom:18px; }}
    .summary div {{ border-left:3px solid var(--accent); padding-left:10px; }}
    .label {{ font-size:12px; color:var(--muted); }}
    .value {{ font-size:15px; font-weight:650; }}
    .trial-card {{ padding:22px; margin:18px 0; }}
    .trial-head {{ display:flex; justify-content:space-between; gap:16px; align-items:flex-start; }}
    .trial-head h3 {{ margin:4px 0 0; font-size:22px; line-height:1.3; }}
    .rank {{ color:var(--accent); font-weight:700; }}
    .nct {{ white-space:nowrap; color:white; background:var(--accent); padding:7px 10px; border-radius:6px; text-decoration:none; font-weight:700; }}
    .trial-meta {{ display:flex; flex-wrap:wrap; gap:8px; margin:14px 0; }}
    .trial-meta span {{ background:#eef2f5; border:1px solid var(--line); border-radius:6px; padding:5px 8px; color:#2f3d48; }}
    .rationale {{ background:#fff8ea; border-left:4px solid var(--accent2); padding:10px 12px; }}
    .pending {{ background:#f8fafb; border:1px solid var(--line); border-radius:6px; padding:12px 14px; margin-bottom:16px; }}
    h4,h5 {{ margin:14px 0 8px; }}
    .drug-card {{ padding:18px; margin:16px 0 4px; border-color:#b8d7da; }}
    .drug-head {{ display:flex; justify-content:space-between; gap:12px; align-items:center; padding-bottom:8px; border-bottom:1px solid var(--line); }}
    .drug-head h4 {{ font-size:21px; margin:0; }}
    .drug-head span {{ background:var(--soft); color:#0d5860; border-radius:6px; padding:5px 8px; font-size:12px; font-weight:700; }}
    .grid-2 {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; }}
    .core-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:10px; }}
    .core-drug,.evidence,.comparator {{ border:1px solid var(--line); border-radius:6px; padding:12px; margin:10px 0; background:#fbfcfd; }}
    .core-drug strong {{ display:block; font-size:16px; }}
    .core-drug span {{ display:inline-block; color:#0d5860; background:var(--soft); border-radius:5px; padding:2px 6px; font-size:12px; margin:6px 0; }}
    .core-drug p {{ margin:4px 0; }}
    .core-drug small,.muted,.source-line,.source-policy {{ color:var(--muted); font-size:13px; }}
    .mini-title {{ font-weight:700; color:#26343f; }}
    .metric-table {{ width:100%; border-collapse:collapse; margin-top:8px; font-size:14px; }}
    .metric-table th,.metric-table td {{ border:1px solid var(--line); padding:7px; text-align:left; vertical-align:top; }}
    .metric-table th {{ background:#eef5f6; }}
    .sources {{ padding-left:18px; }}
    .source-policy {{ border-top:1px solid var(--line); padding-top:10px; }}
    .error {{ background:#fff0f0; border:1px solid #e2b8b8; border-radius:6px; padding:12px; color:#7a1f1f; }}
    a {{ color:#0b6975; }}
    footer {{ max-width:1180px; margin:0 auto; padding:8px 24px 28px; color:var(--muted); font-size:13px; }}
    @media (max-width:820px) {{
      .summary,.grid-2 {{ grid-template-columns:1fr; }}
      .trial-head,.drug-head {{ flex-direction:column; align-items:flex-start; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>ClinicalTrialSKILL 动态药品信息增强报告</h1>
    <p>检索范围：ClinicalTrials.gov only | 当前试验信息实时来自 CT.gov | 同类对照实时来自 FDA 官方页面 | 生成时间：{h(dt.datetime.now().isoformat(timespec="seconds"))}</p>
  </header>
  <main>
    <section class="summary">
      <div><div class="label">患者</div><div class="value">{h(ps.get("patient_id"))}</div></div>
      <div><div class="label">诊断/分期</div><div class="value">{h(ps.get("diagnosis"))} / {h(ps.get("stage"))}</div></div>
      <div><div class="label">关键 biomarker</div><div class="value">{h(biomarker_text)}</div></div>
      <div><div class="label">治疗线数/ECOG</div><div class="value">{h(ps.get("treatment_lines_completed"))} / {h(ps.get("ecog"))}</div></div>
    </section>
    {''.join(path_blocks)}
  </main>
  <footer>
    本报告用于医生/CRC 复核前的信息整理。当前推荐试验药物信息来自对应 CT.gov 页面；FDA 同类药物数据只作为参考，不等同于当前试验药物疗效。
  </footer>
</body>
</html>"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--patient", required=True, type=Path)
    parser.add_argument("--scored", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--decision-report", type=Path)
    parser.add_argument("--html-name", default="dynamic_drug_report_zh.html")
    parser.add_argument("--timeout", default=25, type=int)
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    patient = read_json(args.patient)
    scored = read_json(args.scored)
    decision_report = load_decision_report(patient, scored, args.decision_report)
    nct_ids = [p.get("trial_id") for p in decision_report.get("decision_paths", []) if p.get("trial_id")]
    dynamic = build_dynamic_modules(nct_ids, timeout=args.timeout)

    modules_path = args.out_dir / "dynamic_drug_modules_zh.json"
    report_path = args.out_dir / args.html_name
    write_json(modules_path, dynamic)
    report_path.write_text(render_report(patient, decision_report, dynamic), encoding="utf-8")

    print(json.dumps(
        {
            "report": str(report_path),
            "modules": str(modules_path),
            "module_count": len(dynamic.get("modules", {})),
            "error_count": len(dynamic.get("errors", {})),
        },
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()
