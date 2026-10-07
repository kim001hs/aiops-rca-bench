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


def check_frontend_http(namespace: str = "onlineboutique", timeout: int = 15) -> bool:
    """frontend 서비스 포트포워딩을 통해 실제 HTTP 200 정상 응답 여부 검증"""
    import socket
    import urllib.request

    # 사용 가능한 로컬 임시 포트 탐색
    try:
        with socket.socket() as s:
            s.bind(("", 0))
            local_port = s.getsockname()[1]
    except Exception:
        local_port = 8085

    # kubectl port-forward svc/frontend {local_port}:80 -n {namespace} 실행
    pf_cmd = ["kubectl", "port-forward", "svc/frontend", f"{local_port}:80", "-n", namespace]
    proc = subprocess.Popen(
        pf_cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        start = time.time()
        while time.time() - start < timeout:
            for path in ["/_healthz", "/"]:
                try:
                    req = urllib.request.Request(f"http://127.0.0.1:{local_port}{path}")
                    with urllib.request.urlopen(req, timeout=1.5) as resp:
                        if resp.status == 200:
                            return True
                except Exception:
                    pass
            time.sleep(0.5)
        return False
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


def precheck_cluster(namespace: str) -> bool:
    print(f"\n🔍 [1/5 Pre-check] 클러스터 정상 상태 및 헬스체크 검증 중... (namespace={namespace})")
    # 1. 기본 API 서버 연결 및 파드 목록 확인
    res = run_cmd(f"kubectl get pods -n {namespace} --no-headers")
    if res.returncode != 0:
        print(f"❌ kubectl 연결 실패: {res.stderr.strip()}")
        return False

    # 2. 모든 파드의 Ready 상태 대기 (최대 60초)
    print("   ⏳ 모든 파드의 Ready 상태 검증 중 (kubectl wait Ready, timeout=60s)...")
    wait_res = run_cmd(f"kubectl wait --for=condition=Ready pods --all -n {namespace} --timeout=60s")
    if wait_res.returncode != 0:
        print(f"⚠️ 일부 파드가 Ready 상태가 아니거나 대기 시간 초과: {wait_res.stderr.strip() or wait_res.stdout.strip()}")
        run_res = run_cmd(f"kubectl get pods -n {namespace}")
        if run_res.stdout:
            print(f"   [현재 파드 상태]\n{run_res.stdout.strip()}")
    else:
        print("   ✅ 모든 워크로드 Pod Ready 상태 확인 완료.")

    # 3. 프론트엔드 엔드포인트 HTTP 200 검증
    print("   🌐 프론트엔드 서비스 엔드포인트 HTTP 200 응답 확인 중...")
    if check_frontend_http(namespace, timeout=10):
        print("   ✅ 프론트엔드 서비스 HTTP 200 정상 응답 수신.")
    else:
        print("   ⚠️ 프론트엔드 직접 포트포워딩 HTTP 응답 대기 초과. Deployment 상태 추가 점검...")
        rollout_res = run_cmd(f"kubectl rollout status deployment/frontend -n {namespace} --timeout=15s")
        if rollout_res.returncode != 0:
            print(f"❌ 프론트엔드 배포 비정상: {rollout_res.stderr.strip()}")
            return False
        print("   ✅ 프론트엔드 Deployment 롤아웃 정상 완료 상태 확인.")

    print("✅ 사전 클러스터 헬스체크 통과.")
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


def evaluate_results(metadata: dict, output_file: Path, duration: float, run_id: str = "") -> dict:
    print("\n📊 [4/5 Evaluation & Scoring] Ground Truth 기반 자동 채점 중...")
    if not output_file.exists():
        print(f"❌ 결과 파일 {output_file}이 생성되지 않았습니다.")
        return {
            "Run ID": run_id or "unknown",
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
    # 1) 최종 진단 리포트 텍스트 추출 (messages 배열의 마지막 assistant 메시지 우선 탐색)
    response_text = ""
    messages = rca_data.get("messages", [])
    if isinstance(messages, list):
        for msg in reversed(messages):
            if isinstance(msg, dict) and msg.get("role") == "assistant" and msg.get("content"):
                response_text = str(msg.get("content"))
                break

    # fallback: 구버전 sections, response, answer 필드 호환
    if not response_text:
        sections = rca_data.get("sections", [])
        if isinstance(sections, list):
            for sec in sections:
                if isinstance(sec, dict):
                    content = sec.get("content", "")
                    if isinstance(content, str):
                        response_text += content + "\n"
        if not response_text:
            response_text = str(rca_data.get("response") or rca_data.get("answer") or "")

    # 2) 도구 호출(Tool Calls) 및 스텝 수 집계
    tool_calls = rca_data.get("tool_calls", [])
    if not isinstance(tool_calls, list) or len(tool_calls) == 0:
        # 구버전 호환: sections에서 tool_call 탐색
        sections = rca_data.get("sections", [])
        if isinstance(sections, list):
            tool_calls = [
                sec for sec in sections
                if isinstance(sec, dict) and (sec.get("type") == "tool_call" or "tool_call" in sec)
            ]

    steps = len(tool_calls)
    tools_used = []
    for item in tool_calls:
        if isinstance(item, dict):
            tool_name = item.get("tool_name") or item.get("name") or "unknown_tool"
            tools_used.append(tool_name)

    # 3) 토큰 사용량 및 비용 파싱 (최상위 필드 우선, metadata/usage 폴백)
    total_tokens = rca_data.get("total_tokens")
    total_cost = rca_data.get("total_cost")
    prompt_tokens = rca_data.get("prompt_tokens", 0)
    completion_tokens = rca_data.get("completion_tokens", 0)

    # 최상위 필드가 누락된 경우 metadata.costs 또는 usage 객체 순회
    if total_tokens is None or total_cost is None:
        meta_costs = rca_data.get("metadata", {}).get("costs", {})
        usage = rca_data.get("metadata", {}).get("usage", {}) or rca_data.get("token_usage", {}) or rca_data.get("usage", {})

        if total_tokens is None:
            total_tokens = meta_costs.get("total_tokens") or usage.get("total_tokens", 0)
        if total_cost is None:
            total_cost = meta_costs.get("total_cost")

        if not prompt_tokens:
            prompt_tokens = meta_costs.get("prompt_tokens") or usage.get("prompt_tokens", 0)
        if not completion_tokens:
            completion_tokens = meta_costs.get("completion_tokens") or usage.get("completion_tokens", 0)

    total_tokens = int(total_tokens or 0)
    if total_cost is not None:
        total_cost = float(total_cost)
    else:
        # GPT-4o 기준 단가 ($2.5/1M in, $10.0/1M out)
        total_cost = (prompt_tokens * 2.5 / 1_000_000) + (completion_tokens * 10.0 / 1_000_000)

    # Ground Truth 비교
    gt = metadata.get("ground_truth", {})
    target_comp = (gt.get("root_cause_component") or gt.get("component") or "").strip().lower()
    target_fault = (gt.get("fault_type") or "").strip().lower()
    keywords = gt.get("keywords", [])

    # keywords가 명시되지 않은 경우, 핵심 필드로부터 기본 키워드 리스트 자동 유도
    if not keywords:
        derived = []
        if target_comp:
            derived.append(target_comp)
        if target_fault:
            derived.extend([word for word in target_fault.replace("/", " ").split() if len(word) > 2])
        symptom = gt.get("symptom_service")
        if symptom and symptom.lower() not in derived:
            derived.append(symptom.lower())
        keywords = derived

    text_lower = response_text.lower()

    # 컴포넌트 정확도 (CA): 핵심 컴포넌트 언급 여부 (빈 문자열 방어)
    if not target_comp or not text_lower:
        ca = 0.0
    else:
        ca = 1.0 if target_comp in text_lower else 0.0

    # 결함 유형 정확도 (FA): 키워드 매칭 비율 및 결함 유형 명시 여부
    matched_kw = [kw for kw in keywords if kw.lower() in text_lower] if text_lower else []
    if not text_lower or not keywords:
        fa = 0.0
    elif target_fault and target_fault in text_lower:
        fa = 1.0
    elif len(matched_kw) >= 2:
        fa = 1.0
    else:
        fa = len(matched_kw) / max(len(keywords), 1)
    fa = round(min(fa, 1.0), 2)

    # 결합 진단 정확도 (JRA): CA와 FA가 모두 완벽할 때만 1.0
    jra = 1.0 if (ca == 1.0 and fa >= 0.8) else 0.0

    scorecard = {
        "Run ID": run_id or f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
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


def cleanup_fault(case_dir: Path, namespace: str = "onlineboutique") -> bool:
    print("\n🛡️ [5/5 Cleanup & Recovery] 클러스터 정상 복구 중...")
    res = run_case_script(case_dir, "cleanup")
    if res.stdout:
        print(res.stdout.strip())
    if res.returncode != 0:
        print(f"❌ 복구 스크립트 실행 실패: {res.stderr.strip()}")
        return False

    print("   ⏳ 복구 후 파드 안정화 및 Ready 상태 검증 중...")
    wait_res = run_cmd(f"kubectl wait --for=condition=Ready pods --all -n {namespace} --timeout=60s")
    if wait_res.returncode != 0:
        print(f"⚠️ 사후 파드 Ready 검증 경고: {wait_res.stderr.strip() or wait_res.stdout.strip()}")

    if check_frontend_http(namespace, timeout=10):
        print("   ✅ 복구 후 프론트엔드 HTTP 200 정상 응답 수신 확인.")

    print("✅ 클러스터 원상 복구 및 상태 검증 완료.")
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
        print("❌ 장애 주입 실패로 인해 벤치마크를 중단합니다.")
        sys.exit(1)

    cleanup_success = False
    pipeline_error = None

    try:
        # 3. Investigate
        results_dir = project_root / "results"
        results_dir.mkdir(exist_ok=True)
        case_id = metadata.get("case_id")

        run_id = f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        run_dir = results_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        output_file = run_dir / f"{case_id}_rca.json"

        # 이전 캐시 오염 방지: 만약 파일이 이미 존재하면 사전 삭제
        if output_file.exists():
            output_file.unlink()

        duration = run_holmes(metadata.get("query"), output_file, model=args.model)

        # 실행 완료 검증: Holmes 출력이 정상 생성되었는지 검증
        if not output_file.exists() or output_file.stat().st_size == 0:
            raise RuntimeError(f"Holmes 진단 결과 파일이 생성되지 않았거나 비어 있습니다: {output_file}")

        # 4. Evaluate & Scorecard
        scorecard = evaluate_results(metadata, output_file, duration, run_id=run_id)

        # 고유 격리 디렉터리에 스코어카드 저장
        run_scorecard_path = run_dir / f"{case_id}_scorecard.json"
        with open(run_scorecard_path, "w", encoding="utf-8") as f:
            json.dump(scorecard, f, indent=2, ensure_ascii=False)

        # 최신 결과 파일 갱신 (단일 최신 파일 호환성 유지)
        latest_output_file = results_dir / f"{case_id}_rca.json"
        latest_scorecard_path = results_dir / f"{case_id}_scorecard.json"
        shutil.copyfile(output_file, latest_output_file)
        shutil.copyfile(run_scorecard_path, latest_scorecard_path)

        print(f"\n📁 격리 실행 결과 저장: {run_dir}")
        print(f"📁 최신 채점 스코어카드 갱신: {latest_scorecard_path}")

        # 히스토리 보존 (플랫 타임스탬프 파일도 호환 유지)
        if not args.no_history:
            hist_scorecard = results_dir / f"{case_id}_scorecard_{run_id}.json"
            hist_rca = results_dir / f"{case_id}_rca_{run_id}.json"
            shutil.copyfile(run_scorecard_path, hist_scorecard)
            shutil.copyfile(output_file, hist_rca)
            print(f"📜 시계열 히스토리 백업 완료: {hist_scorecard.name}")

    except Exception as e:
        pipeline_error = e
        print(f"\n💥 [Pipeline Error] 벤치마크 실행 중 예외 발생: {e}", flush=True)
    except KeyboardInterrupt:
        pipeline_error = KeyboardInterrupt("작업이 사용자에 의해 중단되었습니다.")
        print("\n⚠️ [Interrupt] 사용자에 의해 실행이 중단되었습니다. 긴급 복구를 시도합니다...", flush=True)
    finally:
        # 5. Cleanup (어떤 상황에서도 클러스터 원상 복구 100% 보장)
        target_ns = metadata.get("namespace", "onlineboutique") if "metadata" in locals() else "onlineboutique"
        cleanup_success = cleanup_fault(case_dir, namespace=target_ns)
        if not cleanup_success:
            print("🚨 [CRITICAL ALERT] 클러스터 복구(Cleanup) 실패! 클러스터 상태를 즉시 수동 점검해야 합니다.", flush=True)

    if pipeline_error is not None:
        print(f"❌ 파이프라인 에러로 비정상 종료합니다: {pipeline_error}")
        sys.exit(1)

    if not cleanup_success:
        print("❌ 복구 스크립트 실행 실패로 프로세스를 에러(Exit Code 1) 종료합니다.")
        sys.exit(1)

    print("\n🎉 모든 파이프라인(주입 ➡️ 진단 ➡️ 채점 ➡️ 복구) 1사이클 관통 완료!")


if __name__ == "__main__":
    main()
