#!/usr/bin/env python3
"""
E2E Benchmark Runner for Kubernetes AIOps RCA
- 1. Pre-check: 검증 전 클러스터 파드 정상 확인
- 2. Fault Injection: 장애 주입 (inject.sh / inject.ps1 크로스 플랫폼 지원)
- 3. Autonomous Investigation: HolmesGPT ReAct 자율 진단 실행 (--json-output-file)
- 4. Evaluation & Scorecard: Ground Truth 대조 자동 채점 (CA, FA, JRA, Steps, TTD, Cost)
- 5. Cleanup & Recovery: 장애 해제 및 클러스터 원상 복구 (cleanup.sh / cleanup.ps1)
- 6. History Retention: 최신 결과 파일 및 시계열 타임스탬프 히스토리 파일 동시 보존
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# Windows 환경 한글 및 이모지 입출력 UTF-8 인코딩 강제
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["PYTHONUTF8"] = "1"
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def get_bash_executable() -> str:
    """크로스 플랫폼 호환 Bash 실행 파일 경로 탐색 (Linux/macOS 기본 bash, Windows Git Bash)"""
    if sys.platform != "win32":
        return shutil.which("bash") or "/bin/bash"

    # Windows Git Bash 후보지 우선 탐색
    candidates = [
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files (x86)\Git\bin\bash.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Git\bin\bash.exe"),
    ]
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate

    return shutil.which("bash") or "bash"


def load_env():
    """외부 패키지 없이 benchmark/.env 또는 루트 .env를 자동 로드"""
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


def run_cmd(cmd, cwd=None, shell=True) -> subprocess.CompletedProcess:
    """터미널 명령어 실행 헬퍼 (UTF-8 / CP949 자동 호환, 환경변수 완전 전파)"""
    res = subprocess.run(
        cmd,
        cwd=cwd,
        shell=shell,
        capture_output=True,
        env=os.environ,
    )

    stdout = ""
    for enc in ["utf-8", "cp949"]:
        try:
            stdout = res.stdout.decode(enc)
            break
        except UnicodeDecodeError:
            pass
    if not stdout and res.stdout:
        stdout = res.stdout.decode("utf-8", errors="replace")

    stderr = ""
    for enc in ["utf-8", "cp949"]:
        try:
            stderr = res.stderr.decode(enc)
            break
        except UnicodeDecodeError:
            pass
    if not stderr and res.stderr:
        stderr = res.stderr.decode("utf-8", errors="replace")

    return subprocess.CompletedProcess(
        args=res.args,
        returncode=res.returncode,
        stdout=stdout,
        stderr=stderr,
    )


def run_case_script(case_dir: Path, base_name: str) -> subprocess.CompletedProcess:
    """
    크로스 플랫폼 OS 셸 스크립트 실행기:
    - Linux / macOS / CI: .sh 우선 실행 (실행 권한 부여), 없으면 .ps1 (pwsh)
    - Windows: .ps1 우선 실행 (powershell), 없으면 .sh (Git Bash)
    """
    sh_path = case_dir / f"{base_name}.sh"
    ps_path = case_dir / f"{base_name}.ps1"

    is_windows = sys.platform == "win32"

    if is_windows:
        # Windows: .ps1 우선
        if ps_path.exists():
            cmd = f'powershell -ExecutionPolicy Bypass -File "{ps_path.resolve()}"'
            return run_cmd(cmd, cwd=case_dir)
        elif sh_path.exists():
            bash_bin = get_bash_executable()
            # Git Bash로 sh 스크립트 실행
            res = subprocess.run(
                [bash_bin, str(sh_path.resolve())],
                cwd=case_dir,
                capture_output=True,
                env=os.environ,
            )
            stdout = res.stdout.decode("utf-8", errors="replace") if res.stdout else ""
            stderr = res.stderr.decode("utf-8", errors="replace") if res.stderr else ""
            return subprocess.CompletedProcess(res.args, res.returncode, stdout, stderr)
    else:
        # Linux / macOS / GitHub Actions CI: .sh 우선
        if sh_path.exists():
            try:
                os.chmod(sh_path, 0o755)
            except Exception:
                pass
            res = subprocess.run(
                ["bash", str(sh_path.resolve())],
                cwd=case_dir,
                capture_output=True,
                env=os.environ,
            )
            stdout = res.stdout.decode("utf-8", errors="replace") if res.stdout else ""
            stderr = res.stderr.decode("utf-8", errors="replace") if res.stderr else ""
            return subprocess.CompletedProcess(res.args, res.returncode, stdout, stderr)
        elif ps_path.exists():
            pwsh_bin = shutil.which("pwsh") or shutil.which("powershell")
            if pwsh_bin:
                res = subprocess.run(
                    [pwsh_bin, "-File", str(ps_path.resolve())],
                    cwd=case_dir,
                    capture_output=True,
                    env=os.environ,
                )
                stdout = res.stdout.decode("utf-8", errors="replace") if res.stdout else ""
                stderr = res.stderr.decode("utf-8", errors="replace") if res.stderr else ""
                return subprocess.CompletedProcess(res.args, res.returncode, stdout, stderr)

    return subprocess.CompletedProcess(
        args=[],
        returncode=1,
        stdout="",
        stderr=f"❌ {base_name}.sh 또는 {base_name}.ps1 실행 스크립트를 찾을 수 없습니다.",
    )


def precheck_cluster(namespace: str) -> bool:
    print("\n🔍 [1/5 Pre-check] 클러스터 정상 상태 확인 중...")
    res = run_cmd(f"kubectl get pods -n {namespace} --no-headers")
    if res.returncode != 0:
        print(f"❌ kubectl 연결 실패: {res.stderr}")
        return False
    print("✅ 클러스터 연결 정상 확인.")
    return True


def inject_fault(case_dir: Path) -> bool:
    print("\n⚡ [2/5 Fault Injection] 고의 장애 주입 중...")
    res = run_case_script(case_dir, "inject")
    if res.stdout:
        print(res.stdout.strip())
    if res.returncode != 0:
        print(f"❌ 장애 주입 실패: {res.stderr.strip()}")
        return False
    print("✅ 장애 주입 완료.")
    return True


def run_holmes(query: str, output_file: Path, model: str = None) -> float:
    print("\n🤖 [3/5 Autonomous Investigation] HolmesGPT 자율 조사 실행 중...", flush=True)
    print(f"   질의 내용: {query}", flush=True)
    print(f"   출력 저장 경로: {output_file}", flush=True)

    # Holmes 모델 지정 (기본값: openai/gpt-4o)
    selected_model = model or os.environ.get("MODEL") or "openai/gpt-4o"
    # provider 접두사(openai/, anthropic/ 등)가 누락된 경우 자동 보정
    if "/" not in selected_model:
        if selected_model.startswith("gpt-") or selected_model.startswith("o1") or selected_model.startswith("o3"):
            selected_model = f"openai/{selected_model}"
        elif selected_model.startswith("claude"):
            selected_model = f"anthropic/{selected_model}"
        elif selected_model.startswith("gemini"):
            selected_model = f"google/{selected_model}"

    print(f"   지정 모델: {selected_model}", flush=True)

    start_time = time.time()
    api_key = os.environ.get("OPENAI_API_KEY")

    # 리스트 인자로 구성하여 쉘 따옴표 문제 및 파싱 에러 방지 (-u: unbuffered)
    cmd = [
        sys.executable,
        "-u",
        "-m",
        "holmes.main",
        "ask",
        query,
        "--model",
        selected_model,
        "--json-output-file",
        str(output_file.resolve()),
        "--no-interactive",
        "--bash-always-allow",
        "--fast-mode",
    ]
    if api_key:
        cmd.extend(["--api-key", api_key])

    print("   자율 조사 에이전트 구동 시작...", flush=True)
    child_env = os.environ.copy()
    child_env["PYTHONIOENCODING"] = "utf-8"
    child_env["PYTHONUTF8"] = "1"

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=child_env,
    )

    for line in iter(proc.stdout.readline, ""):
        print(f"   [Holmes] {line}", end="", flush=True)

    proc.stdout.close()
    returncode = proc.wait()
    duration = round(time.time() - start_time, 2)

    if returncode != 0 and not output_file.exists():
        print(f"⚠️ Holmes 실행 경고/에러: returncode={returncode}", flush=True)

    return duration


def evaluate_results(metadata: dict, output_file: Path, duration: float) -> dict:
    print("\n📊 [4/5 Evaluation & Scoring] Ground Truth 기반 자동 채점 중...")
    if not output_file.exists():
        print(f"❌ 결과 파일 {output_file}이 생성되지 않았습니다.")
        return {
            "Case ID": metadata.get("case_id"),
            "CA": 0.0,
            "FA": 0.0,
            "JRA": 0.0,
            "Status": "FAILED_NO_OUTPUT",
            "Duration_Seconds": duration,
        }

    with open(output_file, "r", encoding="utf-8") as f:
        try:
            rca_data = json.load(f)
        except Exception as e:
            print(f"❌ JSON 파싱 에러: {e}")
            rca_data = {}

    # Holmes 출력 분석
    response_text = ""
    sections = rca_data.get("sections", [])
    for sec in sections:
        content = sec.get("content", "")
        if isinstance(content, str):
            response_text += content + "\n"

    # 도구 호출 및 토큰 사용량 집계
    steps = 0
    tools_used = []
    total_tokens = 0
    total_cost = 0.0

    # 1) tool_calls 및 token_usages 집계
    for item in sections:
        if item.get("type") == "tool_call" or "tool_call" in item:
            steps += 1
            tool_name = item.get("tool_name") or item.get("name") or "unknown_tool"
            tools_used.append(tool_name)

    # 2) rca_data 메타데이터에서 토큰/비용 파싱
    usage = rca_data.get("token_usage", {}) or rca_data.get("usage", {})
    if usage:
        total_tokens = usage.get("total_tokens", 0)
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        # GPT-4o 기준 단가 ($2.5/1M in, $10.0/1M out)
        total_cost = (prompt_tokens * 2.5 / 1_000_000) + (completion_tokens * 10.0 / 1_000_000)

    # Ground Truth 비교
    gt = metadata.get("ground_truth", {})
    target_comp = gt.get("component", "").lower()
    target_fault = gt.get("fault_type", "").lower()
    keywords = gt.get("keywords", [])

    text_lower = response_text.lower()

    # 컴포넌트 정확도 (CA): 핵심 컴포넌트 언급 여부
    ca = 1.0 if target_comp in text_lower else 0.0

    # 결함 유형 정확도 (FA): 키워드 매칭 비율
    matched_kw = [kw for kw in keywords if kw.lower() in text_lower]
    fa = 1.0 if (len(matched_kw) >= 2 or target_fault in text_lower) else (len(matched_kw) / max(len(keywords), 1))
    fa = round(min(fa, 1.0), 2)

    # 결합 진단 정확도 (JRA): CA와 FA가 모두 완벽할 때만 1.0
    jra = 1.0 if (ca == 1.0 and fa >= 0.8) else 0.0

    scorecard = {
        "Case ID": metadata.get("case_id"),
        "Title": metadata.get("title"),
        "Timestamp": datetime.now().isoformat(),
        "CA (Component Accuracy)": ca,
        "FA (Fault Type Accuracy)": fa,
        "JRA (Joint Root-Cause Accuracy)": jra,
        "TTD (Time To Diagnose, sec)": duration,
        "Steps (Tool Calls)": steps,
        "Total Tokens": total_tokens,
        "Estimated Cost ($)": round(total_cost, 4),
        "Matched Keywords": matched_kw,
        "Tools Used": tools_used,
        "Status": "PASS" if jra == 1.0 else "PARTIAL/FAIL",
    }

    print("=" * 60)
    print(f" 🏆 벤치마크 평가 스코어카드: [{metadata.get('case_id')}]")
    print("=" * 60)
    print(f" • 컴포넌트 정확도 (CA)   : {'✅ 1.0 (PASS)' if ca == 1.0 else '❌ 0.0 (FAIL)'} (정답: {target_comp})")
    print(f" • 결함 유형 정확도 (FA)   : {'✅ 1.0 (PASS)' if fa == 1.0 else '❌ 0.0 (FAIL)'} (정답: {target_fault})")
    print(f" • 결합 진단 정확도 (JRA)  : {'🎯 1.0 (100% PASS)' if jra == 1.0 else '❌ 0.0 (FAIL)'}")
    print(f" • 진단 소요 시간 (TTD)   : ⏱️  {duration}초")
    print(f" • 도구 호출 횟수 (Steps) : 🛠️  {steps}회")
    print(f" • API 토큰 / 비용       : 💰 {total_tokens:,} tokens / ${total_cost:.4f}")
    if scorecard["Tools Used"]:
        print(" • 실행된 도구 목록:")
        for idx, tool in enumerate(scorecard["Tools Used"], 1):
            print(f"    [{idx}] {tool}")
    print("=" * 60)

    return scorecard


def cleanup_fault(case_dir: Path) -> bool:
    print("\n🛡️ [5/5 Cleanup & Recovery] 클러스터 정상 복구 중...")
    res = run_case_script(case_dir, "cleanup")
    if res.stdout:
        print(res.stdout.strip())
    if res.returncode != 0:
        print(f"❌ 복구 실패: {res.stderr.strip()}")
        return False
    print("✅ 클러스터 원상 복구 완료.")
    return True


def main():
    parser = argparse.ArgumentParser(description="Kubernetes AIOps Live Benchmark Runner")
    parser.add_argument(
        "--case",
        default="service_01_cartservice_down",
        help="실행할 케이스 디렉터리 이름 (기본값: service_01_cartservice_down)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="HolmesGPT가 사용할 LLM 모델 (예: openai/gpt-4o, anthropic/claude-3-5-sonnet-20241022, 기본값: openai/gpt-4o)",
    )
    parser.add_argument(
        "--no-history",
        action="store_true",
        help="타임스탬프 히스토리 파일 저장을 비활성화하고 최신 결과 파일만 갱신",
    )
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent
    case_dir = project_root / "cases" / args.case

    if not case_dir.exists():
        print(f"❌ 케이스 디렉터리를 찾을 수 없습니다: {case_dir}")
        sys.exit(1)

    metadata_path = case_dir / "metadata.json"
    with open(metadata_path, "r", encoding="utf-8") as f:
        metadata = json.load(f)

    print("=" * 60)
    print(f"🚀 [E2E Live Benchmark] 케이스 시작: {metadata.get('title')}")
    print("=" * 60)

    # 1. Pre-check
    if not precheck_cluster(metadata.get("namespace", "onlineboutique")):
        sys.exit(1)

    # 2. Inject
    if not inject_fault(case_dir):
        sys.exit(1)

    # 3. Investigate
    results_dir = project_root / "results"
    results_dir.mkdir(exist_ok=True)
    case_id = metadata.get("case_id")
    output_file = results_dir / f"{case_id}_rca.json"

    duration = run_holmes(metadata.get("query"), output_file, model=args.model)

    # 4. Evaluate & Scorecard
    scorecard = evaluate_results(metadata, output_file, duration)
    scorecard_path = results_dir / f"{case_id}_scorecard.json"
    with open(scorecard_path, "w", encoding="utf-8") as f:
        json.dump(scorecard, f, indent=2, ensure_ascii=False)
    print(f"\n📁 최신 채점 스코어카드 저장: {scorecard_path}")

    # 히스토리 보존 (시계열 분석을 위한 타임스탬프 파일 저장)
    if not args.no_history:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        hist_scorecard = results_dir / f"{case_id}_scorecard_{ts}.json"
        with open(hist_scorecard, "w", encoding="utf-8") as f:
            json.dump(scorecard, f, indent=2, ensure_ascii=False)

        if output_file.exists():
            hist_rca = results_dir / f"{case_id}_rca_{ts}.json"
            shutil.copyfile(output_file, hist_rca)
        print(f"📜 시계열 히스토리 백업 완료: {hist_scorecard.name}")

    # 5. Cleanup
    cleanup_fault(case_dir)

    print("\n🎉 모든 파이프라인(주입 ➡️ 진단 ➡️ 채점 ➡️ 복구) 1사이클 관통 완료!")


if __name__ == "__main__":
    main()
