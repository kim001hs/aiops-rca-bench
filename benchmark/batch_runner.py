#!/usr/bin/env python3
"""
E2E Benchmark Batch Runner & Automated Evaluator

지정한 벤치마크 케이스와 버전에 대해 n회 연속 자동 실행(주입 -> 감지 -> Holmes 진단 수집 -> 복구),
실행 완료 후 LLM-as-a-Judge 채점 및 comparison.md 결과표 갱신을 원클릭으로 수행합니다.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# pip install -e . 미설치 상태에서도 실행 가능하도록 루트 경로 보장
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Windows 환경 한글 및 이모지 입출력 UTF-8 인코딩 강제
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["PYTHONUTF8"] = "1"
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def load_env():
    """benchmark/.env 및 루트 .env 자동 로드"""
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


def stream_command(cmd: list[str], cwd: Path | None = None) -> int:
    """실시간 터미널 출력 스트리밍을 지원하는 subprocess 실행 래퍼"""
    print(f"\n[Command] {' '.join(cmd)}\n", flush=True)
    process = subprocess.Popen(
        cmd,
        cwd=cwd or _ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    if process.stdout:
        for line in iter(process.stdout.readline, ""):
            print(line, end="", flush=True)
        process.stdout.close()

    return process.wait()


def gather_run_results(case_dir_results: Path, version: str) -> list[dict]:
    """버전 폴더 내 각 회차의 scorecard.json 및 evaluation.json 수집"""
    v_dir = case_dir_results / version
    if not v_dir.exists():
        return []

    numeric_dirs = sorted(
        [d for d in v_dir.iterdir() if d.is_dir() and d.name.isdigit()],
        key=lambda d: int(d.name),
    )

    results = []
    for d in numeric_dirs:
        run_num = int(d.name)
        ev_file = d / "evaluation.json"
        sc_file = d / "scorecard.json"
        run_info_file = d / "run_info.json"

        ev_data = {}
        if ev_file.exists():
            try:
                ev_data = json.loads(ev_file.read_text(encoding="utf-8"))
            except Exception:
                pass

        sc_data = {}
        if sc_file.exists():
            try:
                sc_data = json.loads(sc_file.read_text(encoding="utf-8"))
            except Exception:
                pass

        run_info = {}
        if run_info_file.exists():
            try:
                run_info = json.loads(run_info_file.read_text(encoding="utf-8"))
            except Exception:
                pass

        scores = ev_data.get("scores", {})
        verdict = ev_data.get("verdict") or sc_data.get("Status") or "UNKNOWN"
        total_score = scores.get("total_score")
        if total_score is None:
            total_score = sc_data.get("JRA (Joint Root-Cause Accuracy, 100점)", 0.0)

        mttd = run_info.get("MTTD (Detection Time, sec)") or sc_data.get("MTTD (Detection Time, sec)", 0.0)
        mtti = run_info.get("MTTI / TTD (Diagnosis Time, sec)") or sc_data.get("MTTI / TTD (Diagnosis Time, sec)", 0.0)

        feedback = ev_data.get("judge_feedback", "")
        # 줄바꿈 및 파이프 문자 제거하여 마크다운 표 깨짐 방지
        clean_feedback = feedback.replace("\n", " ").replace("|", "/").strip()
        if len(clean_feedback) > 60:
            clean_feedback = clean_feedback[:57] + "..."

        results.append({
            "run": run_num,
            "verdict": verdict,
            "total_score": float(total_score),
            "cause_score": float(scores.get("root_cause_score", 0.0)),
            "evidence_score": float(scores.get("evidence_score", 0.0)),
            "remediation_score": float(scores.get("remediation_score", 0.0)),
            "factual_score": float(scores.get("factual_accuracy_score", 0.0)),
            "mttd": float(mttd),
            "mtti": float(mtti),
            "feedback": clean_feedback or ("수집 완료" if run_info.get("status") == "collected" else "진단 미수집"),
        })

    return results


def update_case_comparison_md(case_id: str, version: str, runs: list[dict]):
    """benchmark/results/<case_id>/comparison.md 내 해당 버전의 상세 표 자동 갱신"""
    comp_file = _ROOT / "benchmark" / "results" / case_id / "comparison.md"
    if not comp_file.exists() or not runs:
        return

    raw_text = comp_file.read_text(encoding="utf-8")
    lines = raw_text.splitlines()

    # 통계 계산
    passed = [r for r in runs if r["verdict"] == "PASS"]
    pass_rate = (len(passed) / len(runs)) * 100 if runs else 0.0
    avg_total = sum(r["total_score"] for r in runs) / len(runs) if runs else 0.0
    avg_cause = sum(r["cause_score"] for r in runs) / len(runs) if runs else 0.0
    avg_ev = sum(r["evidence_score"] for r in runs) / len(runs) if runs else 0.0
    avg_rem = sum(r["remediation_score"] for r in runs) / len(runs) if runs else 0.0
    avg_fact = sum(r["factual_score"] for r in runs) / len(runs) if runs else 0.0
    avg_mttd = sum(r["mttd"] for r in runs) / len(runs) if runs else 0.0
    avg_mtti = sum(r["mtti"] for r in runs) / len(runs) if runs else 0.0

    # 1. 상세 회차 테이블 행들 생성
    table_lines = [
        "| 회차 | 판정 | 총점 | 원인 (30) | 증거 (30) | 해결 (20) | 환각 방지 (20) | MTTD | MTTI | 주요 피드백 |",
        "| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |",
    ]
    for r in runs:
        badge = "✅ PASS" if r["verdict"] == "PASS" else "❌ FAIL"
        table_lines.append(
            f"| **Run {r['run']}** | {badge} | **{r['total_score']:.1f}** | "
            f"{r['cause_score']:.1f} | {r['evidence_score']:.1f} | {r['remediation_score']:.1f} | {r['factual_score']:.1f} | "
            f"{r['mttd']:.1f}s | {r['mtti']:.1f}s | {r['feedback']} |"
        )
    table_lines.append(
        f"| **평균** | **{pass_rate:.0f}%** | **{avg_total:.1f}점** | "
        f"**{avg_cause:.1f}** | **{avg_ev:.1f}** | **{avg_rem:.1f}** | **{avg_fact:.1f}** | "
        f"**{avg_mttd:.1f}s** | **{avg_mtti:.1f}s** | **{version} 집계 완료 ({len(passed)}/{len(runs)} 합격)** |"
    )

    # 2. 파일 라인 순회하며 상세 섹션 테이블 교체
    new_lines = []
    i = 0
    in_target_version = False
    table_replaced = False

    while i < len(lines):
        line = lines[i]

        # 섹션 헤더 감지 (예: ### 📌 `v3` 또는 ### 📌 v3)
        if line.startswith("### 📌") and (f"`{version}`" in line or f" {version} " in line):
            in_target_version = True
            new_lines.append(line)
            i += 1
            continue

        # 다른 섹션 헤더 감지 시 타겟 버전 섹션 종료
        if in_target_version and line.startswith("### 📌"):
            in_target_version = False

        # 타겟 버전 섹션 내부의 테이블 감지
        if in_target_version and not table_replaced and line.startswith("| 회차 |"):
            # 기존 테이블 끝(| **평균** |)까지 건너뛰기
            while i < len(lines) and not lines[i].startswith("| **평균** |"):
                i += 1
            if i < len(lines) and lines[i].startswith("| **평균** |"):
                i += 1  # 평균 라인 건너뛰기
            # 새 테이블 삽입
            new_lines.extend(table_lines)
            table_replaced = True
            continue

        # 3. 상단 1. 🏆 버전별 종합 요약 행 갱신
        # 예: | **`v3`** | `service: frontend` / **Tempo OFF** | *(진행 예정)* | *(대조군)* | ...
        if line.startswith(f"| **`{version}`** |") or line.startswith(f"| **{version}** |"):
            parts = [p.strip() for p in line.split("|")]
            # parts 구조: ['', '**`v3`**', '환경설명', '평균점수', '합격률', '핵심평가', '']
            if len(parts) >= 6:
                parts[3] = f"**{avg_total:.1f}점**"
                parts[4] = f"**{pass_rate:.0f}% ({len(passed)}/{len(runs)})**"
                line = " | ".join(parts[1:-1])
                line = f"| {line} |"

        new_lines.append(line)
        i += 1

    comp_file.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    print(f"\n📊 [자동 갱신 완료] {comp_file.name} 에 {version} 점수 결과가 반영되었습니다.", flush=True)


def main():
    parser = argparse.ArgumentParser(
        description="E2E Benchmark Automated Batch Runner (n-times loop + LLM judge + comparison report)"
    )
    parser.add_argument(
        "--case",
        default="service_05_order_latency",
        help="실행할 벤치마크 케이스 ID (기본값: service_05_order_latency)",
    )
    parser.add_argument(
        "-v",
        "--version",
        default="v3",
        help="실험 버전 태그 (예: v3, v4, 기본값: v3)",
    )
    parser.add_argument(
        "-n",
        "--count",
        type=int,
        default=5,
        help="반복 실행 횟수 (기본값: 5)",
    )
    parser.add_argument(
        "--bucket",
        default=os.environ.get("ALERT_RESULTS_BUCKET", "msa-demo-alert-results-ssouyy"),
        help="S3 진단 수집 버킷명 (기본값: ALERT_RESULTS_BUCKET 또는 msa-demo-alert-results-ssouyy)",
    )
    parser.add_argument(
        "--alert-timeout",
        type=int,
        default=180,
        help="Alertmanager 알람 발생 대기 제한시간(초) (기본값: 180)",
    )
    parser.add_argument(
        "--result-timeout",
        type=int,
        default=None,
        help="S3 Holmes 진단 완료 대기 제한시간(초) (미지정 시 order_latency는 900초, 기타는 600초)",
    )
    parser.add_argument(
        "--cooldown",
        type=int,
        default=15,
        help="각 회차 실행 사이 클러스터 안정화 대기 시간(초) (기본값: 15)",
    )
    parser.add_argument(
        "--no-eval",
        action="store_true",
        help="배치 실행 완료 후 자동 LLM 채점 생략",
    )
    parser.add_argument(
        "--no-comparison-update",
        action="store_true",
        help="comparison.md 자동 갱신 생략",
    )
    parser.add_argument(
        "--judge-model",
        default=os.environ.get("MODEL", "gemini/gemini-3.1-flash-lite"),
        help="LLM 채점관 모델 (기본값: .env의 MODEL 또는 gemini/gemini-3.1-flash-lite)",
    )
    args = parser.parse_args()

    # result-timeout 기본값 설정
    if args.result_timeout is None:
        args.result_timeout = 900 if "latency" in args.case else 600

    print("=" * 75)
    print("🚀 [E2E Benchmark Batch Runner 시작]")
    print(f" • 대상 케이스: {args.case}")
    print(f" • 실험 버전:   {args.version}")
    print(f" • 반복 횟수:   {args.count} 회")
    print(f" • 수집 버킷:   {args.bucket}")
    print(f" • 대기 제한:   알람 {args.alert_timeout}초 / 진단결과 {args.result_timeout}초")
    print(f" • 쿨다운 간격: {args.cooldown}초")
    print("=" * 75, flush=True)

    # 1. 반복 실행 루프
    total_runs = args.count
    success_count = 0
    fail_count = 0

    for idx in range(1, total_runs + 1):
        print(f"\n{'='*75}")
        print(f"🔄 [Run {idx}/{total_runs}] 케이스 '{args.case}' (버전: {args.version}) 시작")
        print(f" • 시작 일시: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*75}", flush=True)

        cmd = [
            sys.executable,
            "-m",
            "benchmark.runner",
            "--case",
            args.case,
            "-v",
            args.version,
            "--bucket",
            args.bucket,
            "--alert-timeout",
            str(args.alert_timeout),
            "--result-timeout",
            str(args.result_timeout),
        ]

        exit_code = stream_command(cmd)

        if exit_code == 0:
            success_count += 1
            print(f"\n✅ [Run {idx}/{total_runs}] 수집 완료 (성공)")
        else:
            fail_count += 1
            print(f"\n⚠️ [Run {idx}/{total_runs}] 수집 실패 (종료 코드: {exit_code})")

        # 마지막 회차가 아니면 쿨다운 대기
        if idx < total_runs and args.cooldown > 0:
            print(f"\n⏳ 다음 회차 실행 전 클러스터 안정화 대기 ({args.cooldown}초)...", flush=True)
            time.sleep(args.cooldown)

    print("\n" + "=" * 75)
    print(f"🏁 [{args.case} - {args.version}] 총 {total_runs}회 수집 루프 완료!")
    print(f" • 성공: {success_count}회 / 실패: {fail_count}회")
    print("=" * 75, flush=True)

    # 2. 자동 LLM 채점
    if not args.no_eval:
        print(f"\n{'='*75}")
        print(f"⚖️ [LLM-as-a-Judge 자동 채점 진행] 버전: {args.version} | 케이스: {args.case}")
        print(f" • 채점 모델: {args.judge_model}")
        print(f"{'='*75}", flush=True)

        eval_cmd = [
            sys.executable,
            str(_ROOT / "benchmark" / "evaluate.py"),
            "--version",
            args.version,
            "--case",
            args.case,
            "--all",
            "--judge-model",
            args.judge_model,
        ]

        eval_code = stream_command(eval_cmd)
        if eval_code != 0:
            print(f"❌ LLM 채점 도중 오류 발생 (종료 코드: {eval_code})", flush=True)

    # 3. comparison.md 자동 갱신
    if not args.no_comparison_update:
        results_dir = _ROOT / "benchmark" / "results" / args.case
        runs = gather_run_results(results_dir, args.version)
        if runs:
            update_case_comparison_md(args.case, args.version, runs)

    print("\n🎉 모든 작업(배치 수집, LLM 채점, 결과표 갱신)이 성공적으로 완료되었습니다!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
