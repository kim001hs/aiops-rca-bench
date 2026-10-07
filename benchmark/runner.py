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


def check_frontend_health(namespace: str = "onlineboutique", timeout: int = 15, require_cart: bool = True) -> tuple[bool, str]:
    """
    frontend 서비스 포트포워딩을 통해:
    1) /_healthz (프로세스 생존 여부)
    2) /cart (cartservice gRPC 백엔드 통신 정상 여부)
    두 가지 엔드포인트를 모두 검증하여 실제 비즈니스 기능 정상 동작을 판정
    """
    import socket
    import urllib.request
    import urllib.error

    try:
        with socket.socket() as s:
            s.bind(("", 0))
            local_port = s.getsockname()[1]
    except Exception:
        local_port = 8085

    pf_cmd = ["kubectl", "port-forward", "svc/frontend", f"{local_port}:80", "-n", namespace]
    proc = subprocess.Popen(
        pf_cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        start = time.time()
        healthz_ok = False
        cart_ok = not require_cart

        while time.time() - start < timeout:
            # 1. /_healthz 검증
            if not healthz_ok:
                try:
                    req = urllib.request.Request(f"http://127.0.0.1:{local_port}/_healthz")
                    with urllib.request.urlopen(req, timeout=1.5) as resp:
                        if resp.status == 200:
                            healthz_ok = True
                except Exception:
                    time.sleep(0.5)
                    continue

            # 2. /cart 검증 (cartservice gRPC 연동 확인)
            if healthz_ok and require_cart and not cart_ok:
                try:
                    cart_req = urllib.request.Request(
                        f"http://127.0.0.1:{local_port}/cart",
                        headers={"Cookie": "shop_session-id=healthcheck-session-id"},
                    )
                    with urllib.request.urlopen(cart_req, timeout=2.0) as resp:
                        if resp.status == 200:
                            cart_ok = True
                except urllib.error.HTTPError as e:
                    # cartservice가 내려가 있으면 500 반환됨
                    cart_ok = False
                except Exception:
                    pass

            if healthz_ok and cart_ok:
                return True, "정상 (Healthz 및 Cart 기능 응답 200)"

            time.sleep(0.5)

        if not healthz_ok:
            return False, "프론트엔드 /_healthz 응답 실패 (타임아웃)"
        if not cart_ok:
            return False, "장바구니(/cart) 엔드포인트 응답 실패 (cartservice 연동 비정상)"
        return False, "엔드포인트 헬스체크 타임아웃"
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
    print(f"\n🔍 [1/5 Pre-check] 클러스터 정상 상태 및 헬스체크 엄격 검증 중... (namespace={namespace})")
    # 1. 기본 API 서버 연결 및 파드 목록 확인
    res = run_cmd(f"kubectl get pods -n {namespace} --no-headers")
    if res.returncode != 0:
        print(f"❌ kubectl 연결 실패: {res.stderr.strip()}")
        return False

    # 2. 모든 파드의 Ready 상태 대기 (최대 60초) - 실패 시 즉시 중단
    print("   ⏳ 모든 파드의 Ready 상태 검증 중 (kubectl wait Ready, timeout=60s)...")
    wait_res = run_cmd(f"kubectl wait --for=condition=Ready pods --all -n {namespace} --timeout=60s")
    if wait_res.returncode != 0:
        print(f"❌ 파드 Ready 상태 검증 실패: {wait_res.stderr.strip() or wait_res.stdout.strip()}")
        run_res = run_cmd(f"kubectl get pods -n {namespace}")
        if run_res.stdout:
            print(f"   [현재 파드 상태]\n{run_res.stdout.strip()}")
        return False
    print("   ✅ 모든 워크로드 Pod Ready 상태 확인 완료.")

    # 3. 프론트엔드 및 장바구니 엔드포인트 HTTP 200 검증 - 실패 시 즉시 중단
    print("   🌐 프론트엔드 및 장바구니(/cart) 엔드포인트 기능 검증 중...")
    healthy, msg = check_frontend_health(namespace, timeout=15, require_cart=True)
    if not healthy:
        print(f"❌ 사전 기능 검증 실패: {msg}")
        return False
    print(f"   ✅ 서비스 헬스체크 성공: {msg}")

    print("✅ 사전 클러스터 헬스체크 전 항목 엄격 통과.")
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


CURRENT_HOLMES_PROC = None


def run_holmes(query: str, output_file: Path, model: str = None, timeout: int = 300) -> tuple[float, int]:
    global CURRENT_HOLMES_PROC
    import threading

    print("\n🤖 [3/5 Autonomous Investigation] HolmesGPT 자율 조사 실행 중...", flush=True)
    print(f"   질의 내용: {query}", flush=True)
    print(f"   출력 저장 경로: {output_file}", flush=True)
    print(f"   실행 제한 시간: {timeout}초", flush=True)

    # Holmes 모델 지정 (기본값: openai/gpt-4o-mini)
    selected_model = model or os.environ.get("MODEL") or "openai/gpt-4o-mini"
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
    CURRENT_HOLMES_PROC = proc

    def stream_output(pipe):
        try:
            for line in iter(pipe.readline, ""):
                print(f"   [Holmes] {line}", end="", flush=True)
        except Exception:
            pass
        finally:
            try:
                pipe.close()
            except Exception:
                pass

    reader_thread = threading.Thread(target=stream_output, args=(proc.stdout,), daemon=True)
    reader_thread.start()

    timed_out = False
    try:
        returncode = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        print(f"\n⏱️ [Timeout Alert] Holmes 실행 제한 시간({timeout}초) 초과! 프로세스를 강제 종료(kill)합니다.", flush=True)
        try:
            proc.kill()
        except Exception:
            pass
        returncode = proc.wait()
    except KeyboardInterrupt:
        print("\n⚠️ [Interrupt Alert] 진단 중 인터럽트 발생! Holmes 프로세스를 강제 종료(kill)합니다.", flush=True)
        try:
            proc.kill()
            proc.wait(timeout=2)
        except Exception:
            pass
        raise
    finally:
        CURRENT_HOLMES_PROC = None

    duration = round(time.time() - start_time, 2)
    reader_thread.join(timeout=2.0)

    if timed_out:
        print(f"❌ Holmes 진단이 타임아웃({timeout}초)으로 중단되었습니다.", flush=True)
        return duration, -999

    if returncode != 0:
        print(f"⚠️ Holmes 프로세스 비정상 종료 (exit code: {returncode})", flush=True)

    return duration, returncode


def evaluate_results(metadata: dict, output_file: Path, duration: float, run_id: str = "", holmes_returncode: int = None) -> dict:
    print("\n📊 [4/5 Evaluation & Scoring] Ground Truth 기반 자동 채점 중...")
    if not output_file.exists() or output_file.stat().st_size == 0:
        print(f"❌ 결과 파일 {output_file}이 생성되지 않았거나 비어 있습니다.")
        status = "FAILED_NO_OUTPUT"
        if holmes_returncode is not None and holmes_returncode != 0:
            status = "FAILED_HOLMES_TIMEOUT" if holmes_returncode == -999 else f"FAILED_HOLMES_EXIT_{holmes_returncode}"
        return {
            "Run ID": run_id or f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            "Case ID": metadata.get("case_id"),
            "Title": metadata.get("title"),
            "Timestamp": datetime.now().isoformat(),
            "CA (Component Accuracy)": 0.0,
            "FA (Fault Type Accuracy)": 0.0,
            "JRA (Joint Root-Cause Accuracy)": 0.0,
            "TTD (Time To Diagnose, sec)": duration,
            "Steps (Tool Calls)": 0,
            "Total Tokens": 0,
            "Estimated Cost ($)": 0.0,
            "Matched Keywords": [],
            "Tools Used": [],
            "Holmes_Returncode": holmes_returncode,
            "Cleanup_Success": None,
            "Status": status,
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
        # 모델별 단가 자동 적용
        active_model = os.environ.get("MODEL", "").lower()
        if "mini" in active_model or "flash" in active_model or "haiku" in active_model:
            # GPT-4o-mini 기준 단가 ($0.15/1M in, $0.60/1M out)
            total_cost = (prompt_tokens * 0.15 / 1_000_000) + (completion_tokens * 0.60 / 1_000_000)
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

    status = "PASS" if jra == 1.0 else "PARTIAL/FAIL"
    if holmes_returncode is not None and holmes_returncode != 0:
        if holmes_returncode == -999:
            status = "FAILED_HOLMES_TIMEOUT"
        else:
            status = f"FAILED_HOLMES_EXIT_{holmes_returncode}"
        jra = 0.0

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
        "Holmes_Returncode": holmes_returncode,
        "Cleanup_Success": None,
        "Status": status,
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
    if holmes_returncode is not None and holmes_returncode != 0:
        print(f" • Holmes 실행 상태       : ❌ 비정상 (code: {holmes_returncode})")
    if scorecard["Tools Used"]:
        print(" • 실행된 도구 목록:")
        for idx, tool in enumerate(scorecard["Tools Used"], 1):
            print(f"    [{idx}] {tool}")
    print("=" * 60)

    return scorecard


def cleanup_fault(case_dir: Path, namespace: str = "onlineboutique") -> bool:
    print("\n🛡️ [5/5 Cleanup & Recovery] 클러스터 정상 복구 및 상태 엄격 검증 중...")
    res = run_case_script(case_dir, "cleanup")
    if res.stdout:
        print(res.stdout.strip())
    if res.returncode != 0:
        print(f"❌ 복구 스크립트 실행 실패: {res.stderr.strip()}")
        return False

    print("   ⏳ 복구 후 파드 안정화 및 Ready 상태 검증 중 (timeout=60s)...")
    wait_res = run_cmd(f"kubectl wait --for=condition=Ready pods --all -n {namespace} --timeout=60s")
    if wait_res.returncode != 0:
        print(f"❌ 복구 후 파드 Ready 검증 실패: {wait_res.stderr.strip() or wait_res.stdout.strip()}")
        return False
    print("   ✅ 모든 워크로드 Pod Ready 상태 복구 확인.")

    print("   🌐 복구 후 프론트엔드 및 장바구니 기능 검증 중...")
    healthy, msg = check_frontend_health(namespace, timeout=15, require_cart=True)
    if not healthy:
        print(f"❌ 복구 후 기능 검증 실패: {msg}")
        return False
    print(f"   ✅ 복구 후 서비스 헬스체크 성공: {msg}")

    print("✅ 클러스터 원상 복구 및 기능 검증 전 항목 엄격 통과.")
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
        help="HolmesGPT가 사용할 LLM 모델 (예: openai/gpt-4o-mini, openai/gpt-4o, anthropic/claude-3-5-sonnet-20241022, 기본값: openai/gpt-4o-mini)",
    )
    parser.add_argument(
        "--no-history",
        action="store_true",
        help="타임스탬프 히스토리 파일 저장을 비활성화하고 최신 결과 파일만 갱신",
    )
    parser.add_argument(
        "--rescore",
        nargs="?",
        const="latest",
        default=None,
        help="새로운 E2E 진단을 실행하지 않고 기존 rca.json 파일을 재채점하여 스코어카드 갱신 (경로 생략 시 results/{case}_rca.json 사용)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="HolmesGPT 자율 조사 실행 타임아웃 초 (기본값: 300초)",
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

    results_dir = project_root / "results"
    results_dir.mkdir(exist_ok=True)
    case_id = metadata.get("case_id")

    # 오프라인 재채점 모드 (--rescore)
    if args.rescore:
        if args.rescore == "latest":
            rca_file = results_dir / f"{case_id}_rca.json"
        else:
            rca_file = Path(args.rescore)
        if not rca_file.exists():
            print(f"❌ 재채점할 대상 RCA 파일을 찾을 수 없습니다: {rca_file}")
            sys.exit(1)

        print(f"\n🔄 [Re-scoring Mode] 기존 RCA 파일 재채점 수행: {rca_file.name}")
        duration = 0.0
        old_cleanup = None
        old_returncode = None
        old_scorecard = results_dir / f"{case_id}_scorecard.json"
        if old_scorecard.exists():
            try:
                with open(old_scorecard, "r", encoding="utf-8") as f:
                    sc = json.load(f)
                    duration = float(sc.get("TTD (Time To Diagnose, sec)") or sc.get("TTD (소요 시간 초)") or 0.0)
                    old_cleanup = sc.get("Cleanup_Success")
                    old_returncode = sc.get("Holmes_Returncode")
            except Exception:
                pass

        scorecard = evaluate_results(
            metadata,
            rca_file,
            duration,
            run_id="run_baseline_rescore",
            holmes_returncode=None,
        )
        # 오프라인 재채점에서는 복구/실행을 직접 검증하지 않으므로 null로 유지
        scorecard["Cleanup_Success"] = None
        scorecard["Holmes_Returncode"] = None
        scorecard_path = results_dir / f"{case_id}_scorecard.json"
        with open(scorecard_path, "w", encoding="utf-8") as f:
            json.dump(scorecard, f, indent=2, ensure_ascii=False)
        print(f"\n✅ 재채점 완료 및 스코어카드 저장: {scorecard_path}")
        sys.exit(0)

    print("=" * 60)
    print(f"🚀 [E2E Live Benchmark] 케이스 시작: {metadata.get('title')}")
    print("=" * 60)

    # 1. Pre-check (엄격 검증: 실패 시 실행 즉시 중단)
    if not precheck_cluster(metadata.get("namespace", "onlineboutique")):
        print("❌ 사전 클러스터 헬스체크 실패로 인해 벤치마크를 중단합니다.")
        sys.exit(1)

    cleanup_success = False
    pipeline_error = None
    fault_injected = False
    scorecard = None
    run_id = f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    run_dir = results_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    output_file = run_dir / f"{case_id}_rca.json"

    # 이전 캐시 오염 방지: 만약 파일이 이미 존재하면 사전 삭제
    if output_file.exists():
        output_file.unlink()

    try:
        # 2. Inject (try 블록 내부에서 보호하여 실패 시에도 cleanup 보장)
        fault_injected = inject_fault(case_dir)
        if not fault_injected:
            raise RuntimeError("고의 장애 주입 스크립트 실행 실패")

        # 3. Investigate
        duration, holmes_returncode = run_holmes(
            metadata.get("query"),
            output_file,
            model=args.model,
            timeout=args.timeout,
        )

        # 4. Evaluate & Scorecard (출력 파일 누락/비정상 종료 시에도 실패 스코어카드 저장)
        scorecard = evaluate_results(
            metadata,
            output_file,
            duration,
            run_id=run_id,
            holmes_returncode=holmes_returncode,
        )

    except Exception as e:
        pipeline_error = e
        print(f"\n💥 [Pipeline Error] 벤치마크 실행 중 예외 발생: {e}", flush=True)
    except KeyboardInterrupt:
        pipeline_error = KeyboardInterrupt("작업이 사용자에 의해 중단되었습니다.")
        print("\n⚠️ [Interrupt] 사용자에 의해 실행이 중단되었습니다. 실행 중인 조사 프로세스를 정리하고 복구를 시도합니다...", flush=True)
        if CURRENT_HOLMES_PROC and CURRENT_HOLMES_PROC.poll() is None:
            try:
                CURRENT_HOLMES_PROC.kill()
                CURRENT_HOLMES_PROC.wait(timeout=2)
                print("   ✅ 실행 중이던 Holmes 자식 프로세스 강제 종료 완료.", flush=True)
            except Exception:
                pass
    finally:
        # 5. Cleanup (장애 주입 시도 여부와 무관하게 실행 및 사후 상태 엄격 검증)
        target_ns = metadata.get("namespace", "onlineboutique") if "metadata" in locals() else "onlineboutique"
        cleanup_success = cleanup_fault(case_dir, namespace=target_ns)
        if not cleanup_success:
            print("🚨 [CRITICAL ALERT] 클러스터 복구 및 검증 실패! 클러스터 상태를 즉시 수동 점검해야 합니다.", flush=True)

        # scorecard 파일 갱신 및 저장 (실패 실행도 상태·시간·복구 결과를 파일로 보존)
        if run_dir is not None:
            if scorecard is None:
                fail_status = "FAILED_INTERRUPT" if isinstance(pipeline_error, KeyboardInterrupt) else "FAILED_PIPELINE"
                scorecard = {
                    "Run ID": run_id,
                    "Case ID": metadata.get("case_id"),
                    "Title": metadata.get("title"),
                    "Timestamp": datetime.now().isoformat(),
                    "CA (Component Accuracy)": 0.0,
                    "FA (Fault Type Accuracy)": 0.0,
                    "JRA (Joint Root-Cause Accuracy)": 0.0,
                    "TTD (Time To Diagnose, sec)": 0.0,
                    "Steps (Tool Calls)": 0,
                    "Total Tokens": 0,
                    "Estimated Cost ($)": 0.0,
                    "Matched Keywords": [],
                    "Tools Used": [],
                    "Holmes_Returncode": None,
                    "Cleanup_Success": cleanup_success,
                    "Status": fail_status,
                }
            else:
                scorecard["Cleanup_Success"] = cleanup_success

            if not cleanup_success:
                scorecard["Status"] = "FAILED_CLEANUP"
                scorecard["JRA (Joint Root-Cause Accuracy)"] = 0.0

            run_scorecard_path = run_dir / f"{case_id}_scorecard.json"
            with open(run_scorecard_path, "w", encoding="utf-8") as f:
                json.dump(scorecard, f, indent=2, ensure_ascii=False)

            # 최신 결과 파일 갱신
            latest_output_file = results_dir / f"{case_id}_rca.json"
            latest_scorecard_path = results_dir / f"{case_id}_scorecard.json"
            if output_file.exists():
                shutil.copyfile(output_file, latest_output_file)
            else:
                # 이번 실행에서 출력이 없으면 과거의 낡은 최신 RCA를 제거하여 스코어카드와 상태 불일치 방지
                if latest_output_file.exists():
                    latest_output_file.unlink()
            shutil.copyfile(run_scorecard_path, latest_scorecard_path)

            print(f"\n📁 실행 결과 및 스코어카드 저장: {run_dir}")
            print(f"📁 최신 채점 스코어카드 갱신: {latest_scorecard_path}")

            if not args.no_history:
                hist_scorecard = results_dir / f"{case_id}_scorecard_{run_id}.json"
                hist_rca = results_dir / f"{case_id}_rca_{run_id}.json"
                shutil.copyfile(run_scorecard_path, hist_scorecard)
                if output_file.exists():
                    shutil.copyfile(output_file, hist_rca)
                print(f"📜 시계열 히스토리 백업 완료: {hist_scorecard.name}")

    # 벤치마크 최종 합격 판정 (진단 성공 + 복구 성공 + 에러 없음)
    is_benchmark_passed = (
        pipeline_error is None
        and cleanup_success
        and scorecard is not None
        and scorecard.get("Status") == "PASS"
        and scorecard.get("Holmes_Returncode") == 0
    )

    if not is_benchmark_passed:
        failed_reasons = []
        if pipeline_error is not None:
            failed_reasons.append(f"파이프라인 예외 발생 ({pipeline_error})")
        if scorecard is None:
            failed_reasons.append("스코어카드 미생성")
        else:
            if scorecard.get("Holmes_Returncode") not in (0, None):
                failed_reasons.append(f"Holmes 비정상 종료 (Returncode: {scorecard.get('Holmes_Returncode')})")
            if scorecard.get("Status") != "PASS":
                failed_reasons.append(f"진단 판정 미통과 (Status: {scorecard.get('Status')})")
        if not cleanup_success:
            failed_reasons.append("클러스터 복구 또는 사후 검증 실패")

        print("\n" + "=" * 60)
        print("❌ [Benchmark Result] 벤치마크 테스트 미통과 / 실패:")
        for r in failed_reasons:
            print(f" • {r}")
        print("=" * 60)
        sys.exit(1)

    print("\n🎉 모든 파이프라인(주입 ➡️ 진단 ➡️ 채점 ➡️ 복구) 1사이클 정상 관통 완료!")


if __name__ == "__main__":
    main()
