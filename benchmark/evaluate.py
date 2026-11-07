#!/usr/bin/env python3
"""
E2E Benchmark Batch Evaluator (LLM-as-a-Judge & Version A/B Comparison)
- results/<case_id>/<version>/<run_number>/ 디렉터리 구조 기반 일괄 정밀 채점
- 채점 결과(evaluation.json, evaluation.md)를 해당 run 폴더에 개별 보존 및 scorecard.json 연동
- 버전별 종합 리더보드 (results/summary_<version>.md) 자동 생성
- A/B 실험 비교 모드 (--compare v1 v2): 트레이스/로키 등 전후 성능 및 점수 변화 대조 분석
"""

import argparse
import json
import hashlib
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

from usage_display import usage_lines

# Windows 환경 한글 및 이모지 입출력 UTF-8 인코딩 강제
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["PYTHONUTF8"] = "1"
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def load_env():
    """benchmark/.env 파일 자동 로드"""
    base_dir = Path(__file__).resolve().parent
    env_paths = [base_dir / ".env", base_dir.parent / ".env"]
    for path in env_paths:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip("'\"")
                    if k and (k not in os.environ or not os.environ[k]):
                        os.environ[k] = v
            break


load_env()


def run_single_judge(report_text: str, metadata: dict, judge_model: str) -> dict:
    """단일 케이스 보고서에 대한 LLM-as-a-Judge 정밀 심사"""
    gt = metadata.get("ground_truth", {})
    criteria = metadata.get("evaluation_criteria", {})
    target_comp = criteria.get("target_component") or gt.get("root_cause_component") or "N/A"
    target_fault = criteria.get("target_fault_type") or gt.get("fault_type") or "N/A"
    req_ev = criteria.get("required_evidence", [])

    system_prompt = (
        "You are an expert Kubernetes Senior SRE and AIOps Benchmark Evaluator (LLM-as-a-Judge).\n"
        "Your task is to impartially and rigorously grade an autonomous diagnostic report produced by an AI agent against the Ground Truth."
    )

    eval_prompt = f"""[Ground Truth Reference Specification]
- Expected Root-Cause Component: {target_comp}
- Expected Fault Type: {target_fault}
- Ground Truth Context: {json.dumps(gt, ensure_ascii=False)}
- Key Diagnostic Evidence Expectations: {json.dumps(req_ev, ensure_ascii=False)}
- Case-specific scoring note: {criteria.get('scoring_note', 'None')}

[Candidate Diagnostic Report to Grade]
{report_text}

[Scoring Rubric (100 Points Total)]
1. root_cause_score (0~30): Did the report accurately pinpoint '{target_comp}' and '{target_fault}' as the true primary cause without blaming innocent services?
2. evidence_score (0~30): Did the report provide convincing, factual evidence (metrics, logs, events, timestamps, restarts, resource limits)?
3. remediation_score (0~20): Did the report propose logical, actionable, and practical engineering solutions or recovery steps?
4. factual_accuracy_score (0~20): Is the report free of hallucinations, false attributions, or fabricated errors?
For the order-latency case, give full evidence credit to independently sufficient logs or metrics; trace use is not required for points. Do not treat an unobserved internal cause as known.
For that case, cap factual_accuracy_score at 10/20 if the report suggests an unobserved internal, network, or external dependency as a likely cause, even when hedged. Do not mistake the percentage of orders under the latency threshold for order success rate.

Respond ONLY with a valid JSON object strictly matching this schema:
{{
  "root_cause_score": <float 0.0 - 30.0>,
  "evidence_score": <float 0.0 - 30.0>,
  "remediation_score": <float 0.0 - 20.0>,
  "factual_accuracy_score": <float 0.0 - 20.0>,
  "total_score": <float 0.0 - 100.0>,
  "verdict": "<PASS or FAIL>",
  "judge_feedback": "<2-4 sentences in Korean explaining the grade, key reasoning, strengths, and weaknesses>"
}}
"""
    try:
        from litellm import completion, completion_cost

        res = completion(
            model=judge_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": eval_prompt},
            ],
            temperature=0.0,
            response_format={"type": "json_object"},
            timeout=45,
        )
        content = res.choices[0].message.content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1]
            if content.endswith("```"):
                content = content.rsplit("\n", 1)[0]
        data = json.loads(content)
        raw_usage = getattr(res, "usage", None)
        token_usage = {key: getattr(raw_usage, key, None) for key in
                       ("prompt_tokens", "completion_tokens", "total_tokens")}
        token_usage = {key: value for key, value in token_usage.items() if isinstance(value, int)}
        estimated_cost_usd = None
        if token_usage:
            try:
                estimated_cost_usd = completion_cost(completion_response=res)
            except Exception:
                pass
        return {
            "root_cause_score": float(data.get("root_cause_score", 0.0)),
            "evidence_score": float(data.get("evidence_score", 0.0)),
            "remediation_score": float(data.get("remediation_score", 0.0)),
            "factual_accuracy_score": float(data.get("factual_accuracy_score", 0.0)),
            "total_score": float(data.get("total_score", 0.0)),
            "verdict": str(data.get("verdict", "FAIL")).upper(),
            "judge_feedback": str(data.get("judge_feedback", "평가 완료")),
            "token_usage": token_usage,
            "estimated_cost_usd": estimated_cost_usd,
        }
    except Exception as e:
        print(f"❌ LLM 채점관 호출 실패: {e}")
        return {
            "root_cause_score": 0.0,
            "evidence_score": 0.0,
            "remediation_score": 0.0,
            "factual_accuracy_score": 0.0,
            "total_score": 0.0,
            "verdict": "ERROR",
            "judge_feedback": f"채점 호출 실패 ({e})",
        }


def extract_report_text(run_dir: Path) -> str:
    """실행 폴더 내 report.md 또는 rca_raw.json에서 최종 텍스트 추출"""
    md_file = run_dir / "report.md"
    if md_file.exists():
        try:
            with open(md_file, "r", encoding="utf-8") as f:
                content = f.read().split("\n\n## 진단 리소스 사용량\n", 1)[0].strip()
                if content:
                    return content
        except Exception:
            pass

    rca_file = run_dir / "rca_raw.json"
    if rca_file.exists():
        try:
            with open(rca_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list) and data:
                data = data[0].get("result", data[0]) if isinstance(data[0], dict) else {}
            elif isinstance(data, dict) and "result" in data:
                data = data["result"]
            messages = data.get("messages", [])
            for msg in reversed(messages):
                if isinstance(msg, dict) and msg.get("role") == "assistant" and msg.get("content"):
                    return str(msg.get("content")).strip()
        except Exception:
            pass

    return ""


def extract_diagnosis_usage(run_dir: Path) -> str:
    """Copy the Holmes token and cost lines from the collected report."""
    report_file = run_dir / "report.md"
    if not report_file.exists():
        return "기록 없음"
    report = report_file.read_text(encoding="utf-8")
    _, marker, usage = report.partition("## 진단 리소스 사용량\n")
    if not marker:
        return "기록 없음"
    return usage.split("\n## ", 1)[0].strip() or "기록 없음"


def evaluate_version(
    version: str,
    cases_dir: Path,
    results_dir: Path,
    judge_model: str,
    target_case: str = None,
    target_run: str = "latest",
    draft: bool = False,
) -> list:
    """특정 실험 버전(v1, v2 등)의 결과 일괄 채점"""
    results_dir = results_dir / "draft" if draft else results_dir
    if not results_dir.exists():
        print(f"⚠️ 결과 디렉터리가 존재하지 않습니다: {results_dir}")
        return []

    # 채점 대상 케이스 디렉터리 탐색
    candidate_cases = []
    if target_case:
        cd = results_dir / target_case
        if cd.is_dir():
            candidate_cases.append(cd)
        else:
            print(f"⚠️ 지정한 케이스 결과 폴더를 찾을 수 없습니다: {cd}")
            return []
    else:
        for cd in sorted(results_dir.iterdir()):
            if cd.is_dir() and (cd / version).exists():
                candidate_cases.append(cd)

    # 각 케이스별 회차(run) 목록 취합
    tasks = []
    for cdir in candidate_cases:
        case_id = cdir.name
        ver_dir = cdir / version
        if not ver_dir.exists():
            continue

        numeric_runs = sorted(
            [int(d.name) for d in ver_dir.iterdir() if d.is_dir() and d.name.isdigit()]
        )
        if not numeric_runs:
            continue

        if target_run == "latest":
            selected_runs = [numeric_runs[-1]]
        elif target_run == "all":
            selected_runs = numeric_runs
        elif target_run.isdigit():
            r_int = int(target_run)
            selected_runs = [r_int] if r_int in numeric_runs else []
        else:
            selected_runs = [numeric_runs[-1]]

        for r_num in selected_runs:
            tasks.append((case_id, ver_dir / str(r_num), r_num, numeric_runs[-1]))

    if not tasks:
        print(f"⚠️ 버전 '{version}'에 채점할 대상 실행 폴더(run)가 없습니다.")
        return []

    print("=" * 75)
    print(f" ⚖️ [AIOps Batch Evaluator] 실험 버전: '{version}' 정밀 채점 시작")
    print(f"    • 채점관 모델: {judge_model}")
    print(f"    • 채점 대상: {len(tasks)}개 실행 폴더 (대상 케이스: {len(candidate_cases)}개)")
    print("=" * 75)

    summary_rows = []
    for idx, (case_id, run_dir, r_num, latest_num) in enumerate(tasks, 1):
        meta_file = cases_dir / case_id / "metadata.json"
        metadata = {}
        if meta_file.exists():
            with open(meta_file, "r", encoding="utf-8") as f:
                metadata = json.load(f)

        report_text = extract_report_text(run_dir)
        info_file = run_dir / "run_info.json"
        run_info = json.loads(info_file.read_text(encoding="utf-8")) if info_file.exists() else {}
        if run_info and run_info.get("status") != "collected":
            print(f"[{idx}/{len(tasks)}] {case_id} Run {r_num}: 수집 실패 결과를 건너뜁니다.")
            continue
        if not report_text:
            print(f"[{idx}/{len(tasks)}] ⚠️ {case_id} (Run {r_num}): 보고서 내용이 없어 건너뜁니다.")
            continue

        print(f"\n[{idx}/{len(tasks)}] 🔍 심사 중: {case_id} [Run {r_num}] ({metadata.get('title', 'N/A')})...")
        report_hash = hashlib.sha256(report_text.encode("utf-8")).hexdigest()
        metadata_hash = hashlib.sha256(json.dumps(metadata, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        previous_file = run_dir / "evaluation.json"
        previous = json.loads(previous_file.read_text(encoding="utf-8")) if previous_file.exists() else {}
        if (previous.get("report_sha256") == report_hash and previous.get("judge_model") == judge_model
                and previous.get("metadata_sha256", metadata_hash) == metadata_hash
                and previous.get("verdict") != "ERROR"):
            print("   기존 채점 재사용")
            scores = previous["scores"]
            judge_res = {**scores, "verdict": previous["verdict"], "judge_feedback": previous["judge_feedback"],
                         "token_usage": previous.get("token_usage"),
                         "estimated_cost_usd": previous.get("estimated_cost_usd")}
        else:
            judge_res = run_single_judge(report_text, metadata, judge_model)
        if judge_res["verdict"] == "ERROR":
            print("   채점 실패. 결과 파일을 쓰지 않아 다음 실행에서 재시도할 수 있습니다.")
            continue

        score = judge_res["total_score"]
        verdict = judge_res["verdict"]
        icon = "✅ PASS" if verdict == "PASS" else "❌ FAIL"
        print(f"   결과: {icon} | 총점: {score:.1f}점 (원인: {judge_res['root_cause_score']:.1f}/30, 증거: {judge_res['evidence_score']:.1f}/30, 조치: {judge_res['remediation_score']:.1f}/20, 환각배제: {judge_res['factual_accuracy_score']:.1f}/20)")
        print(f"   📝 총평: \"{judge_res['judge_feedback']}\"")

        # 1. run 폴더 내 evaluation.json 저장
        eval_json_data = {
            "case_id": case_id,
            "version": version,
            "run_number": r_num,
            "alert_fingerprint": run_info.get("alert_fingerprint"),
            "alert_starts_at": run_info.get("alert_starts_at"),
            "s3_key": run_info.get("s3_key"),
            "s3_uri": run_info.get("s3_uri"),
            "holmes_tempo_used": run_info.get("holmes_tempo_used"),
            "linked_order_trace_ids": run_info.get("linked_order_trace_ids", []),
            "baseline_orders": run_info.get("baseline_orders"),
            "injected_orders": run_info.get("injected_orders"),
            "timestamp": datetime.now().isoformat(),
            "judge_model": judge_model,
            "report_sha256": report_hash,
            "metadata_sha256": metadata_hash,
            "scores": {
                "root_cause_score": judge_res["root_cause_score"],
                "evidence_score": judge_res["evidence_score"],
                "remediation_score": judge_res["remediation_score"],
                "factual_accuracy_score": judge_res["factual_accuracy_score"],
                "total_score": score,
            },
            "verdict": verdict,
            "judge_feedback": judge_res["judge_feedback"],
            "token_usage": judge_res.get("token_usage"),
            "estimated_cost_usd": judge_res.get("estimated_cost_usd"),
        }
        eval_json_path = run_dir / "evaluation.json"
        with open(eval_json_path, "w", encoding="utf-8") as f:
            json.dump(eval_json_data, f, indent=2, ensure_ascii=False)

        # 2. run 폴더 내 evaluation.md 마크다운 리포트 생성
        eval_md_content = f"""# ⚖️ LLM-as-a-Judge 심사 결과: [{case_id}]
- **시나리오**: {metadata.get('title', 'N/A')}
- **버전**: `{version}` | **회차**: `Run {r_num}`
- **알람 fingerprint**: `{run_info.get('alert_fingerprint') or (run_info.get('selected_alert') or {}).get('fingerprint', '없음')}`
- **알람 시작**: `{run_info.get('alert_starts_at') or (run_info.get('selected_alert') or {}).get('startsAt', '없음')}`
- **S3 원본**: `{run_info.get('s3_uri', '없음')}`
- **채점 일시**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
- **심사 모델**: `{judge_model}`
- **최종 판정**: {'✅ **PASS**' if verdict == 'PASS' else '❌ **FAIL**'} (총점: **{score:.1f}** / 100점)

---

## 채점 리소스 사용량

{usage_lines(judge_res.get('token_usage'), judge_res.get('estimated_cost_usd'))}

비용은 LiteLLM 모델 요금표로 계산한 USD 추정치입니다. 이전 채점에서 사용량을 저장하지 않았다면 `기록 없음`으로 표시합니다.

---

## 📊 세부 배점표
| 평가 항목 | 배점 기준 | 획득 점수 | 만점 |
| :--- | :--- | :---: | :---: |
| **근본 원인 정확도 (Root Cause)** | 핵심 컴포넌트 및 장애 유형 오귀인 없이 정확히 진단 | **{judge_res['root_cause_score']:.1f}** | 30.0 |
| **증거 수집 타당성 (Evidence)** | 메트릭, 로그, 이벤트 등 사실적 근거 확보 여부 | **{judge_res['evidence_score']:.1f}** | 30.0 |
| **해결책 실효성 (Remediation)** | 실질적이고 논리적인 복구 조치 제안 | **{judge_res['remediation_score']:.1f}** | 20.0 |
| **환각 배제 (Factual Accuracy)** | 허위 사실, 가짜 에러, 오귀인 여부 배제 | **{judge_res['factual_accuracy_score']:.1f}** | 20.0 |
| **총합 (Total Score)** | 종합 가중 합산 | **{score:.1f}** | 100.0 |

---

## 📝 심사위원 정밀 심사평
> {judge_res['judge_feedback']}
"""
        eval_md_path = run_dir / "evaluation.md"
        with open(eval_md_path, "w", encoding="utf-8") as f:
            f.write(eval_md_content)

        # 최신 회차인 경우 버전 루트에 latest_evaluation.md로도 복사
        if r_num == latest_num:
            latest_eval_path = run_dir.parent / "latest_evaluation.md"
            try:
                shutil.copyfile(eval_md_path, latest_eval_path)
            except Exception:
                pass

        # 3. scorecard.json 연동 갱신
        sc_file = run_dir / "scorecard.json"
        scorecard = {}
        if sc_file.exists():
            try:
                with open(sc_file, "r", encoding="utf-8") as f:
                    scorecard = json.load(f)
            except Exception:
                pass

        mttd = scorecard.get("MTTD (Detection Time, sec)", 0.0)
        mtti = scorecard.get("MTTI / TTD (Diagnosis Time, sec)", 0.0)
        if info_file.exists():
            run_info = json.loads(info_file.read_text(encoding="utf-8"))
            mttd = run_info.get("MTTD (Detection Time, sec)", mttd)
            mtti = run_info.get("MTTI / TTD (Diagnosis Time, sec)") or mtti
            scorecard["MTTD (Detection Time, sec)"] = mttd
            scorecard["MTTI / TTD (Diagnosis Time, sec)"] = mtti

        scorecard["LLM_Judge_Evaluation"] = {
            "Timestamp": datetime.now().isoformat(),
            "Judge_Model": judge_model,
            "Total_Score": score,
            "Verdict": verdict,
            "Breakdown": {
                "Root_Cause_Score (30)": judge_res["root_cause_score"],
                "Evidence_Score (30)": judge_res["evidence_score"],
                "Remediation_Score (20)": judge_res["remediation_score"],
                "Factual_Accuracy_Score (20)": judge_res["factual_accuracy_score"],
            },
            "Feedback": judge_res["judge_feedback"],
        }
        if case_id == "service_05_order_latency":
            scorecard["Tempo_Comparison"] = {
                "Holmes_Tempo_Used": run_info.get("holmes_tempo_used"),
                "Linked_Order_Trace_IDs": run_info.get("linked_order_trace_ids", []),
                "Order_Samples_Before": run_info.get("baseline_orders", []),
                "Order_Samples_After": run_info.get("injected_orders", []),
            }
        with open(sc_file, "w", encoding="utf-8") as f:
            json.dump(scorecard, f, indent=2, ensure_ascii=False)

        summary_rows.append({
            "case_id": case_id,
            "title": metadata.get("title", case_id),
            "run": r_num,
            "score": score,
            "verdict": verdict,
            "cause": judge_res["root_cause_score"],
            "evidence": judge_res["evidence_score"],
            "remedy": judge_res["remediation_score"],
            "factual": judge_res["factual_accuracy_score"],
            "mttd": mttd,
            "mtti": mtti,
            "feedback": judge_res["judge_feedback"],
            "diagnosis_usage": extract_diagnosis_usage(run_dir),
        })

    # 버전별 종합 리포트 생성 (results/summary_<version>.md)
    summary_md = results_dir / f"summary_{version}.md"
    with open(summary_md, "w", encoding="utf-8") as f:
        f.write(f"# 🏆 AIOps 벤치마크 평가 리포트: [{version}]\n\n")
        f.write(f"- **실험 버전**: `{version}`\n")
        f.write(f"- **채점 일시**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"- **채점관 모델**: `{judge_model}`\n\n")
        f.write("## 1. 📊 리더보드 (Summary Leaderboard)\n\n")
        f.write("| 케이스 ID | 시나리오 명칭 | 회차 | 판정 | 총점 (100) | 원인(30) | 증거(30) | 해결(20) | 환각(20) | MTTD(초) | MTTI(초) |\n")
        f.write("| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |\n")
        for r in summary_rows:
            v_badge = "✅ **PASS**" if r["verdict"] == "PASS" else "❌ **FAIL**"
            f.write(f"| `{r['case_id']}` | {r['title']} | Run {r['run']} | {v_badge} | **{r['score']:.1f}** | {r['cause']:.1f} | {r['evidence']:.1f} | {r['remedy']:.1f} | {r['factual']:.1f} | {r['mttd']:.1f}s | {r['mtti']:.1f}s |\n")
        f.write("\n---\n\n## 2. 📝 심사위원 상세 심사평\n\n")
        for r in summary_rows:
            f.write(f"### 📌 `{r['case_id']}` (Run {r['run']} - {r['title']})\n")
            f.write(f"- **판정**: {r['verdict']} ({r['score']:.1f}점 / 100점)\n")
            f.write(f"\n#### 진단 리소스 사용량\n\n{r['diagnosis_usage']}\n\n")
            f.write(f"- **심사위원 총평**:\n  > {r['feedback']}\n\n")

    print("\n" + "=" * 75)
    print(f"📊 [리더보드 요약: {version}]")
    print(f"| {'케이스 ID':<35} | {'회차':<6} | {'판정':<6} | {'총점':<8} | {'원인':<6} | {'증거':<6} | {'해결':<6} |")
    print(f"|{'-'*37}|{'-'*8}|{'-'*8}|{'-'*10}|{'-'*8}|{'-'*8}|{'-'*8}|")
    for r in summary_rows:
        print(f"| {r['case_id']:<35} | Run {r['run']:<2} | {r['verdict']:<6} | {r['score']:>6.1f}점 | {r['cause']:>4.1f}/30 | {r['evidence']:>4.1f}/30 | {r['remedy']:>4.1f}/20 |")
    print("=" * 75)
    print(f"📄 종합 리더보드 저장 완료: {summary_md}")
    return summary_rows


def compare_two_versions(ver_a: str, ver_b: str, results_dir: Path, cases_dir: Path):
    """두 버전(예: v1 vs v2, baseline vs with_trace) 결과 대조 비교표 생성"""
    if not results_dir.exists():
        print(f"❌ 결과 디렉터리가 존재하지 않습니다: {results_dir}")
        return

    def get_latest_eval_for_version(case_id: str, ver: str) -> dict:
        v_dir = results_dir / case_id / ver
        if not v_dir.exists():
            return {}
        numeric_runs = sorted(
            [int(d.name) for d in v_dir.iterdir() if d.is_dir() and d.name.isdigit()]
        )
        if not numeric_runs:
            return {}
        latest_run = v_dir / str(numeric_runs[-1])

        # evaluation.json 우선 로드
        ev_file = latest_run / "evaluation.json"
        sc_file = latest_run / "scorecard.json"
        data = {}
        if ev_file.exists():
            try:
                with open(ev_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                pass
        if sc_file.exists():
            try:
                with open(sc_file, "r", encoding="utf-8") as f:
                    sc = json.load(f)
                    if "LLM_Judge_Evaluation" in sc and not data:
                        data = sc["LLM_Judge_Evaluation"]
                    data["mttd"] = sc.get("MTTD (Detection Time, sec)", 0.0)
                    data["mtti"] = sc.get("MTTI / TTD (Diagnosis Time, sec)", 0.0)
                    data["steps"] = sc.get("Steps (Tool Calls)", 0)
            except Exception:
                pass
        data["run"] = numeric_runs[-1]
        return data

    all_case_ids = sorted([d.name for d in results_dir.iterdir() if d.is_dir()])
    valid_cases = []
    for cid in all_case_ids:
        if (results_dir / cid / ver_a).exists() or (results_dir / cid / ver_b).exists():
            valid_cases.append(cid)

    if not valid_cases:
        print(f"⚠️ 비교할 수 있는 결과 폴더를 찾지 못했습니다: '{ver_a}' 또는 '{ver_b}'")
        return

    print("\n" + "=" * 88)
    print(f" 🔬 [A/B Experiment Comparison] '{ver_a}' (대조군) vs '{ver_b}' (실험군)")
    print("=" * 88)
    print(f"| {'케이스 ID':<35} | {ver_a:<14} | {ver_b:<14} | {'점수 변화':<10} | {'판정 변화':<15} |")
    print(f"|{'-'*37}|{'-'*16}|{'-'*16}|{'-'*12}|{'-'*17}|")

    comp_rows = []
    for cid in valid_cases:
        a_data = get_latest_eval_for_version(cid, ver_a)
        b_data = get_latest_eval_for_version(cid, ver_b)

        score_a = float(a_data.get("scores", {}).get("total_score") or a_data.get("Total_Score") or 0.0)
        score_b = float(b_data.get("scores", {}).get("total_score") or b_data.get("Total_Score") or 0.0)
        verd_a = str(a_data.get("verdict") or a_data.get("Verdict") or "N/A")
        verd_b = str(b_data.get("verdict") or b_data.get("Verdict") or "N/A")

        diff = score_b - score_a
        diff_str = f"+{diff:.1f}" if diff > 0 else f"{diff:.1f}"

        print(f"| {cid:<35} | {score_a:>5.1f}점 ({verd_a:<4}) | {score_b:>5.1f}점 ({verd_b:<4}) | {diff_str:>10} | {verd_a:<4} ➡️ {verd_b:<10} |")
        comp_rows.append({
            "case_id": cid,
            "score_a": score_a,
            "score_b": score_b,
            "verd_a": verd_a,
            "verd_b": verd_b,
            "diff": diff,
            "diff_str": diff_str,
            "mtti_a": a_data.get("mtti", 0.0),
            "mtti_b": b_data.get("mtti", 0.0),
        })
    print("=" * 88)

    # 마크다운 리포트 저장
    comp_md = results_dir / f"comparison_{ver_a}_vs_{ver_b}.md"
    with open(comp_md, "w", encoding="utf-8") as f:
        f.write(f"# 🔬 AIOps A/B 실험 성능 비교 리포트: `{ver_a}` vs `{ver_b}`\n\n")
        f.write(f"- **생성 일시**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"- **대조군 (Baseline)**: `{ver_a}`\n")
        f.write(f"- **실험군 (Proposed / Enhanced)**: `{ver_b}`\n\n")
        f.write("## 1. 📊 종합 성능 비교표\n\n")
        f.write(f"| 케이스 ID | {ver_a} 점수 | {ver_b} 점수 | 점수 증감 | 판정 변화 | {ver_a} MTTI | {ver_b} MTTI |\n")
        f.write("| :--- | :---: | :---: | :---: | :---: | :---: | :---: |\n")
        for r in comp_rows:
            f.write(f"| `{r['case_id']}` | {r['score_a']:.1f}점 ({r['verd_a']}) | {r['score_b']:.1f}점 ({r['verd_b']}) | **{r['diff_str']}** | `{r['verd_a']}` ➡️ `{r['verd_b']}` | {r['mtti_a']:.1f}s | {r['mtti_b']:.1f}s |\n")

    print(f"\n📄 A/B 비교 리포트 생성 완료: {comp_md}")


def main():
    parser = argparse.ArgumentParser(description="Kubernetes AIOps Batch Evaluator & Comparison")
    parser.add_argument(
        "-v",
        "--version",
        "--tag",
        dest="version",
        default="v1",
        help="채점할 실험 버전 (기본값: v1)",
    )
    parser.add_argument("--case", default=None, help="특정 케이스만 채점")
    parser.add_argument(
        "--run",
        default="latest",
        help="채점할 실행 회차 (latest: 가장 최신 회차, all: 전체 회차, 또는 숫자 예: 1, 기본값: latest)",
    )
    parser.add_argument("--all", action="store_true", help="모든 케이스의 모든 회차 일괄 채점")
    parser.add_argument("--draft", action="store_true", help="초안 결과만 채점")
    parser.add_argument(
        "--compare",
        nargs=2,
        metavar=("VER_A", "VER_B"),
        help="두 실험 버전 결과 A/B 비교 (예: --compare v1 v2)",
    )
    parser.add_argument(
        "--judge-model",
        default=os.environ.get("MODEL", "gemini/gemini-3.1-flash-lite"),
        help="채점관 모델 (기본값: .env의 MODEL)",
    )
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent
    cases_dir = project_root / "cases"
    results_dir = project_root / "results"

    if args.compare:
        compare_two_versions(args.compare[0], args.compare[1], results_dir / "draft" if args.draft else results_dir, cases_dir)
    else:
        evaluate_version(
            version=args.version,
            cases_dir=cases_dir,
            results_dir=results_dir,
            judge_model=args.judge_model,
            target_case=args.case,
            target_run="all" if args.all else args.run,
            draft=args.draft,
        )


if __name__ == "__main__":
    main()
