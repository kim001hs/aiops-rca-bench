#!/usr/bin/env python3
"""
E2E Benchmark Runner for Kubernetes AIOps RCA
- 1. Pre-check: 검증 전 클러스터 파드 및 프론트엔드/카트 엔드포인트 정상 확인
- 2. Fault Injection: 장애 주입 (inject.sh / inject.ps1 크로스 플랫폼 지원)
- 3. Alert Detection (MTTD): Prometheus Alertmanager firing 알람 실시간 감지
- 4. Autonomous Investigation (MTTI/TTD): HolmesGPT ReAct 자율 진단 실행 (alertmanager 모드 / ask 모드)
- 5. Evaluation & Scorecard: Ground Truth 대조 독립 100점 채점 (CA, FA, EA, JRA, FalseAttribution, MTTD, MTTI)
- 6. Cleanup & Recovery: 장애 해제 및 클러스터 원상 복구 (cleanup.sh / cleanup.ps1)
- 7. History Retention: 최신 결과 파일 및 시계열 타임스탬프 히스토리 파일 동시 보존
"""

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
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
        if ps_path.exists():
            cmd = f'powershell -ExecutionPolicy Bypass -File "{ps_path.resolve()}"'
            return run_cmd(cmd, cwd=case_dir)
        elif sh_path.exists():
            bash_bin = get_bash_executable()
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


def check_frontend_health(namespace: str = "onlineboutique", timeout: int = 45, require_cart: bool = True) -> tuple[bool, str]:
    """
    frontend 서비스 포트포워딩을 통해:
    1) /_healthz (프로세스 생존 여부)
    2) /cart (cartservice gRPC 백엔드 통신 정상 여부)
    두 가지 엔드포인트를 매 시도마다 깨끗한 터널로 검증하여 실제 비즈니스 기능 정상 동작을 판정
    """
    start = time.time()
    last_err = ""

    while time.time() - start < timeout:
        try:
            with socket.socket() as s:
                s.bind(("", 0))
                local_port = s.getsockname()[1]
        except Exception:
            local_port = 8085

        pf_cmd = ["kubectl", "port-forward", "svc/frontend", f"{local_port}:80", "-n", namespace]
        proc = subprocess.Popen(pf_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.2)

        try:
            # 1. /_healthz 검증
            req = urllib.request.Request(f"http://127.0.0.1:{local_port}/_healthz")
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                if resp.status != 200:
                    last_err = f"/_healthz status {resp.status}"
                    continue

            # 2. /cart 검증
            if require_cart:
                cart_req = urllib.request.Request(
                    f"http://127.0.0.1:{local_port}/cart",
                    headers={"Cookie": "shop_session-id=healthcheck-session-id"},
                )
                with urllib.request.urlopen(cart_req, timeout=3.0) as cart_resp:
                    if cart_resp.status == 200:
                        return True, "정상 (Healthz 및 Cart 기능 응답 200)"
                    else:
                        last_err = f"/cart status {cart_resp.status}"
            else:
                return True, "정상 (Healthz 응답 200)"
        except Exception as e:
            last_err = str(e)
        finally:
            try:
                proc.terminate()
                proc.wait(timeout=1)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

        time.sleep(2.0)

    return False, f"엔드포인트 기능 검증 실패 ({last_err})"


def ensure_alertmanager_portforward(alertmanager_url: str = "http://localhost:9093", namespace: str = "monitoring") -> tuple[subprocess.Popen | None, str]:
    """
    Alertmanager API 접근성을 확인하고, 비활성 상태면 자동으로 백그라운드 kubectl port-forward를 실행하여 활성화
    """
    # 1. 이미 응답하는지 확인
    try:
        req = urllib.request.Request(f"{alertmanager_url}/api/v2/status")
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            if resp.status == 200:
                print(f"   ✅ Alertmanager가 이미 활성화되어 있습니다 ({alertmanager_url}).")
                return None, alertmanager_url
    except Exception:
        pass

    # 2. 로컬 포트 추출
    parsed = urllib.parse.urlparse(alertmanager_url)
    target_port = parsed.port or 9093

    print(f"   ⏳ Alertmanager 서비스 포트포워딩 구동 중 (svc/kube-prometheus-stack-alertmanager -n {namespace} {target_port}:9093)...")
    pf_cmd = ["kubectl", "port-forward", "svc/kube-prometheus-stack-alertmanager", f"{target_port}:9093", "-n", namespace]
    proc = subprocess.Popen(
        pf_cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # 3. 연결 대기 (최대 10초)
    start = time.time()
    while time.time() - start < 10:
        try:
            req = urllib.request.Request(f"{alertmanager_url}/api/v2/status")
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                if resp.status == 200:
                    print(f"   ✅ Alertmanager 포트포워딩 연결 성공 ({alertmanager_url}).")
                    return proc, alertmanager_url
        except Exception:
            time.sleep(0.5)

    print(f"   ⚠️ Alertmanager 포트포워딩 연결 대기 타임아웃. ({alertmanager_url})")
    return proc, alertmanager_url


def wait_for_firing_alerts(alertmanager_url: str, namespace: str = "onlineboutique", timeout: int = 180, poll_interval: float = 3.0) -> tuple[bool, float, list]:
    """
    Alertmanager API(/api/v2/alerts)를 폴링하여 해당 네임스페이스의 장애 알람이 firing될 때까지 대기하고 MTTD를 측정
    """
    print(f"\n📡 [3/6 Alert Detection] Alertmanager 장애 감지 대기 중 (namespace={namespace}, timeout={timeout}s)...", flush=True)
    start_time = time.time()
    url = f"{alertmanager_url}/api/v2/alerts"

    while time.time() - start_time < timeout:
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    firing_alerts = []
                    for alert in data:
                        status = alert.get("status", {})
                        labels = alert.get("labels", {})
                        if status.get("state") == "active" and labels.get("namespace") == namespace:
                            firing_alerts.append(alert)

                    if firing_alerts:
                        mttd = round(time.time() - start_time, 2)
                        print(f"   🎯 [MTTD: {mttd}초] Prometheus Alertmanager 알람 firing 감지 완료!", flush=True)
                        for a in firing_alerts:
                            alertname = a.get("labels", {}).get("alertname", "UnknownAlert")
                            severity = a.get("labels", {}).get("severity", "unknown")
                            summary = a.get("annotations", {}).get("summary") or a.get("annotations", {}).get("description") or ""
                            print(f"      • [{severity.upper()}] {alertname}: {summary[:80]}", flush=True)
                        return True, mttd, firing_alerts
        except Exception as e:
            # 일시적 네트워크 에러 허용
            pass

        time.sleep(poll_interval)

    mttd = round(time.time() - start_time, 2)
    print(f"   ⏱️ [Alert Timeout] {timeout}초 내에 '{namespace}' 네임스페이스 알람이 감지되지 않았습니다. 조사를 계속 진행합니다.", flush=True)
    return False, mttd, []


def precheck_cluster(namespace: str) -> bool:
    print(f"\n🔍 [1/6 Pre-check] 클러스터 정상 상태 및 헬스체크 엄격 검증 중... (namespace={namespace})")
    res = run_cmd(f"kubectl get pods -n {namespace} --no-headers")
    if res.returncode != 0:
        print(f"❌ kubectl 연결 실패: {res.stderr.strip()}")
        return False

    print("   ⏳ 모든 파드의 Ready 상태 검증 중 (kubectl wait Ready, timeout=60s)...")
    wait_res = run_cmd(f"kubectl wait --for=condition=Ready pods --all -n {namespace} --timeout=60s")
    if wait_res.returncode != 0:
        print(f"❌ 파드 Ready 상태 검증 실패: {wait_res.stderr.strip() or wait_res.stdout.strip()}")
        run_res = run_cmd(f"kubectl get pods -n {namespace}")
        if run_res.stdout:
            print(f"   [현재 파드 상태]\n{run_res.stdout.strip()}")
        return False
    print("   ✅ 모든 워크로드 Pod Ready 상태 확인 완료.")

    print("   🌐 프론트엔드 및 장바구니(/cart) 엔드포인트 기능 검증 중...")
    healthy, msg = check_frontend_health(namespace, timeout=15, require_cart=True)
    if not healthy:
        print(f"❌ 사전 기능 검증 실패: {msg}")
        return False
    print(f"   ✅ 서비스 헬스체크 성공: {msg}")

    print("✅ 사전 클러스터 헬스체크 전 항목 엄격 통과.")
    return True


def inject_fault(case_dir: Path) -> bool:
    print("\n⚡ [2/6 Fault Injection] 고의 장애 주입 중...")
    res = run_case_script(case_dir, "inject")
    if res.stdout:
        print(res.stdout.strip())
    if res.returncode != 0:
        print(f"❌ 장애 주입 실패: {res.stderr.strip()}")
        return False
    print("✅ 장애 주입 완료.")
    return True


CURRENT_HOLMES_PROC = None


def run_holmes(
    mode: str,
    query: str,
    output_file: Path,
    alertmanager_url: str = "http://localhost:9093",
    namespace: str = "onlineboutique",
    model: str = None,
    max_steps: int = 40,
    timeout: int = 300,
) -> tuple[float, int]:
    """
    HolmesGPT 자율 조사 실행:
    - mode="alertmanager": 사람이 질문하지 않고 Alertmanager firing 알람을 기반으로 무인 자동 조사
    - mode="ask": 지정된 질의문(query)으로 대화식 조사
    """
    global CURRENT_HOLMES_PROC
    import threading

    print("\n🤖 [4/6 Autonomous Investigation] HolmesGPT 자율 조사 실행 중...", flush=True)
    print(f"   실행 모드: {mode.upper()}", flush=True)
    print(f"   출력 저장 경로: {output_file}", flush=True)
    print(f"   최대 탐색 단계: {max_steps} steps", flush=True)
    print(f"   실행 제한 시간: {timeout}초", flush=True)

    selected_model = model or os.environ.get("MODEL") or "gemini/gemini-3.1-flash-lite"
    if "/" not in selected_model:
        if selected_model.startswith("gpt-") or selected_model.startswith("o1") or selected_model.startswith("o3"):
            selected_model = f"openai/{selected_model}"
        elif selected_model.startswith("claude"):
            selected_model = f"anthropic/{selected_model}"
        elif selected_model.startswith("gemini"):
            selected_model = f"gemini/{selected_model}"

    print(f"   지정 모델: {selected_model}", flush=True)

    start_time = time.time()
    if selected_model.startswith("gemini") or selected_model.startswith("google"):
        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    else:
        api_key = os.environ.get("OPENAI_API_KEY")

    if mode == "alertmanager":
        print(f"   Alertmanager URL: {alertmanager_url}", flush=True)
        print(f"   대상 라벨 필터: namespace={namespace}", flush=True)
        cmd = [
            sys.executable,
            "-u",
            "-m",
            "holmes.main",
            "investigate",
            "alertmanager",
            "--alertmanager-url",
            alertmanager_url,
            "--alertmanager-label",
            f"namespace={namespace}",
            "--model",
            selected_model,
            "--max-steps",
            str(max_steps),
            "--json-output-file",
            str(output_file.resolve()),
            "--destination",
            "cli",
        ]
    else:
        print(f"   질의 내용: {query}", flush=True)
        cmd = [
            sys.executable,
            "-u",
            "-m",
            "holmes.main",
            "ask",
            query,
            "--model",
            selected_model,
            "--max-steps",
            str(max_steps),
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


def evaluate_results(
    metadata: dict,
    output_file: Path,
    duration: float,
    mttd: float = 0.0,
    firing_alerts: list = None,
    mode: str = "alertmanager",
    run_id: str = "",
    holmes_returncode: int = None,
) -> dict:
    print("\n📊 [5/6 Evaluation & Scoring] Ground Truth 기반 독립 100점 자동 채점 중...")
    triggered_alerts = [a.get("labels", {}).get("alertname", "Unknown") for a in (firing_alerts or [])]

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
            "Investigation Mode": mode,
            "CA (Component Accuracy, 100점)": 0.0,
            "FA (Fault Type Accuracy, 100점)": 0.0,
            "EA (Evidence Accuracy, 100점)": 0.0,
            "JRA (Joint Root-Cause Accuracy, 100점)": 0.0,
            "False Attribution Rate (%)": 0.0,
            "MTTD (Detection Time, sec)": mttd,
            "MTTI / TTD (Diagnosis Time, sec)": duration,
            "Triggered Alerts": triggered_alerts,
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
            rca_raw = json.load(f)
        except Exception as e:
            print(f"❌ JSON 파싱 에러: {e}")
            rca_raw = {}

    # alertmanager 모드에서 반환되는 리스트 포맷([]) 및 result 래퍼 호환
    if isinstance(rca_raw, list):
        if len(rca_raw) == 0:
            print("⚠️ Holmes 진단 결과 리스트가 비어 있습니다 (0 issues investigated).")
            rca_data = {}
        else:
            first_item = rca_raw[0] if isinstance(rca_raw[0], dict) else {}
            if "result" in first_item and isinstance(first_item["result"], dict):
                rca_data = dict(first_item["result"])
            else:
                rca_data = dict(first_item)

            if len(rca_raw) > 1:
                combined_msgs = list(rca_data.get("messages", []))
                combined_tools = list(rca_data.get("tool_calls", []))
                combined_tokens = int(rca_data.get("total_tokens") or 0)
                combined_cost = float(rca_data.get("total_cost") or 0.0)
                for item in rca_raw[1:]:
                    if isinstance(item, dict):
                        res_obj = item.get("result", item) if isinstance(item.get("result"), dict) else item
                        combined_msgs.extend(res_obj.get("messages", []))
                        combined_tools.extend(res_obj.get("tool_calls", []))
                        combined_tokens += int(res_obj.get("total_tokens") or 0)
                        combined_cost += float(res_obj.get("total_cost") or 0.0)
                rca_data["messages"] = combined_msgs
                rca_data["tool_calls"] = combined_tools
                rca_data["total_tokens"] = combined_tokens
                rca_data["total_cost"] = combined_cost
    elif isinstance(rca_raw, dict):
        if "result" in rca_raw and isinstance(rca_raw["result"], dict):
            rca_data = dict(rca_raw["result"])
        else:
            rca_data = rca_raw
    else:
        rca_data = {}

    # Holmes 출력 분석
    response_text = ""
    messages = rca_data.get("messages", [])
    if isinstance(messages, list):
        for msg in reversed(messages):
            if isinstance(msg, dict) and msg.get("role") == "assistant" and msg.get("content"):
                response_text = str(msg.get("content"))
                break

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

    # 도구 호출 및 스텝 수 집계
    tool_calls = rca_data.get("tool_calls", [])
    if not isinstance(tool_calls, list) or len(tool_calls) == 0:
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

    # 토큰 사용량 및 비용 파싱
    total_tokens = rca_data.get("total_tokens")
    total_cost = rca_data.get("total_cost")
    prompt_tokens = rca_data.get("prompt_tokens", 0)
    completion_tokens = rca_data.get("completion_tokens", 0)

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
    active_model = (os.environ.get("MODEL") or "gemini").lower()
    if "gemini" in active_model:
        # Google Gemini Flash 무료 티어 기준 $0.00
        total_cost = 0.0
    elif total_cost is not None:
        total_cost = float(total_cost)
    else:
        if "mini" in active_model or "haiku" in active_model:
            total_cost = (prompt_tokens * 0.15 / 1_000_000) + (completion_tokens * 0.60 / 1_000_000)
        else:
            total_cost = (prompt_tokens * 2.5 / 1_000_000) + (completion_tokens * 10.0 / 1_000_000)

    # Ground Truth 비교 채점
    gt = metadata.get("ground_truth", {})
    criteria = metadata.get("evaluation_criteria", {})
    target_comp = (criteria.get("target_component") or gt.get("root_cause_component") or gt.get("component") or "").strip().lower()
    target_fault = (criteria.get("target_fault_type") or gt.get("fault_type") or "").strip().lower()
    required_evidence = criteria.get("required_evidence", [])
    negative_keywords = criteria.get("negative_keywords", [])
    keywords = gt.get("keywords", [])

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

    # 1. 컴포넌트 정확도 (CA: 100점 만점)
    if not target_comp or not text_lower:
        ca = 0.0
    else:
        ca = 100.0 if target_comp in text_lower else 0.0

    # 2. 결함 유형 정확도 (FA: 100점 만점)
    matched_kw = [kw for kw in keywords if kw.lower() in text_lower] if text_lower else []
    if not text_lower or not keywords:
        fa = 0.0
    elif target_fault and target_fault in text_lower:
        fa = 100.0
    elif len(matched_kw) >= 2:
        fa = 100.0
    else:
        fa = round((len(matched_kw) / max(len(keywords), 1)) * 100.0, 1)
    fa = min(fa, 100.0)

    # 3. 증거 수집 정확도 (EA: 100점 만점)
    matched_ev = []
    for ev in required_evidence:
        ev_lower = ev.lower()
        subterms = [t.strip() for t in ev_lower.split(" or ") if t.strip()]
        hit = False
        for t in subterms:
            if t in text_lower:
                hit = True
                break
            words = [w for w in t.replace("/", " ").split() if len(w) > 1 and w not in ("is", "in", "or", "and", "the", "found", "not")]
            if words and all(w in text_lower for w in words):
                hit = True
                break
        if hit:
            matched_ev.append(ev)

    if not required_evidence:
        ea = 100.0 if len(tools_used) > 0 else 50.0
    else:
        ea = round((len(matched_ev) / len(required_evidence)) * 100.0, 1)
    ea = min(ea, 100.0)

    # 4. 허위 귀인율 (False Attribution Rate: 0%가 이상적)
    false_attribution = False
    for neg in negative_keywords:
        if neg.lower() in text_lower:
            false_attribution = True
            break
    false_attr_rate = 100.0 if false_attribution else 0.0

    # 5. 결합 진단 정확도 (JRA: CA 100점 & FA 100점 & FalseAttribution 0%)
    jra = 100.0 if (ca == 100.0 and fa >= 80.0 and false_attr_rate == 0.0) else 0.0

    status = "PASS" if jra == 100.0 else "PARTIAL/FAIL"
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
        "Investigation Mode": mode,
        "CA (Component Accuracy, 100점)": ca,
        "FA (Fault Type Accuracy, 100점)": fa,
        "EA (Evidence Accuracy, 100점)": ea,
        "JRA (Joint Root-Cause Accuracy, 100점)": jra,
        "False Attribution Rate (%)": false_attr_rate,
        "MTTD (Detection Time, sec)": mttd,
        "MTTI / TTD (Diagnosis Time, sec)": duration,
        "Triggered Alerts": triggered_alerts,
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
    print(f" 🏆 벤치마크 평가 스코어카드: [{metadata.get('case_id')}] (모드: {mode.upper()})")
    print("=" * 60)
    print(f" • 컴포넌트 정확도 (CA)   : {'✅ 100점 (PASS)' if ca == 100.0 else '❌ 0점 (FAIL)'} (정답: {target_comp})")
    print(f" • 결함 유형 정확도 (FA)   : {'✅ 100점 (PASS)' if fa == 100.0 else f'⚠️ {fa}점'} (정답: {target_fault})")
    print(f" • 증거 수집 정확도 (EA)   : {'✅ 100점 (PASS)' if ea == 100.0 else f'⚠️ {ea}점'} ({len(matched_ev)}/{len(required_evidence)}개 증거 채택)")
    print(f" • 허위 귀인율 (FalseAttr) : {'✅ 0% (Clean)' if false_attr_rate == 0.0 else '🚨 100% (오진 감지)'}")
    print(f" • 결합 진단 정확도 (JRA)  : {'🎯 100점 (PASS)' if jra == 100.0 else '❌ 0점 (FAIL)'}")
    print(f" • 감지 소요 시간 (MTTD)  : ⏱️  {mttd}초")
    print(f" • 진단 소요 시간 (MTTI)  : ⏱️  {duration}초")
    print(f" • 도구 호출 횟수 (Steps) : 🛠️  {steps}회")
    print(f" • API 토큰 / 비용       : 💰 {total_tokens:,} tokens / ${total_cost:.4f}")
    if triggered_alerts:
        print(f" • 감지된 Alertmanager 알람 : {', '.join(triggered_alerts)}")
    if holmes_returncode is not None and holmes_returncode != 0:
        print(f" • Holmes 실행 상태       : ❌ 비정상 (code: {holmes_returncode})")
    if scorecard["Tools Used"]:
        print(" • 실행된 도구 목록:")
        for idx, tool in enumerate(scorecard["Tools Used"], 1):
            print(f"    [{idx}] {tool}")
    print("=" * 60)

    return scorecard


def cleanup_fault(case_dir: Path, namespace: str = "onlineboutique") -> bool:
    print("\n🛡️ [6/6 Cleanup & Recovery] 클러스터 정상 복구 및 상태 엄격 검증 중...")
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
    time.sleep(5)

    print("   🌐 복구 후 프론트엔드 및 장바구니 기능 검증 중...")
    healthy, msg = check_frontend_health(namespace, timeout=45, require_cart=True)
    if not healthy:
        print(f"❌ 복구 후 기능 검증 실패: {msg}")
        return False
    print(f"   ✅ 복구 후 서비스 헬스체크 성공: {msg}")

    print("✅ 클러스터 원상 복구 및 기능 검증 전 항목 엄격 통과.")
    return True


def main():
    parser = argparse.ArgumentParser(description="Kubernetes AIOps Live Benchmark Runner (Autonomous Alertmanager & Ask)")
    parser.add_argument(
        "--case",
        default="service_01_cartservice_down",
        help="실행할 케이스 디렉터리 이름 (기본값: service_01_cartservice_down)",
    )
    parser.add_argument(
        "--mode",
        choices=["alertmanager", "ask"],
        default="alertmanager",
        help="조사 모드: alertmanager (Alertmanager 알람 기반 무인 자동 조사, 기본값) 또는 ask (수동 질의어 조사)",
    )
    parser.add_argument(
        "--alertmanager-url",
        default="http://localhost:9093",
        help="Prometheus Alertmanager URL (기본값: http://localhost:9093)",
    )
    parser.add_argument(
        "--model",
        default="gemini/gemini-3.1-flash-lite",
        help="HolmesGPT가 사용할 LLM 모델 (기본값: gemini/gemini-3.1-flash-lite, 무료 티어 권장)",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=40,
        help="자율 조사 에이전트 최대 도구 호출 스텝 수 (기본값: 40)",
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
    target_ns = metadata.get("namespace", "onlineboutique")

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
        mttd = 0.0
        old_scorecard = results_dir / f"{case_id}_scorecard.json"
        if old_scorecard.exists():
            try:
                with open(old_scorecard, "r", encoding="utf-8") as f:
                    sc = json.load(f)
                    duration = float(sc.get("MTTI / TTD (Diagnosis Time, sec)") or sc.get("TTD (Time To Diagnose, sec)") or 0.0)
                    mttd = float(sc.get("MTTD (Detection Time, sec)") or 0.0)
            except Exception:
                pass

        scorecard = evaluate_results(
            metadata,
            rca_file,
            duration,
            mttd=mttd,
            mode=args.mode,
            run_id="run_baseline_rescore",
            holmes_returncode=None,
        )
        scorecard["Cleanup_Success"] = None
        scorecard["Holmes_Returncode"] = None
        scorecard_path = results_dir / f"{case_id}_scorecard.json"
        with open(scorecard_path, "w", encoding="utf-8") as f:
            json.dump(scorecard, f, indent=2, ensure_ascii=False)
        print(f"\n✅ 재채점 완료 및 스코어카드 저장: {scorecard_path}")
        sys.exit(0)

    print("=" * 60)
    print(f"🚀 [E2E Live Benchmark] 케이스 시작: {metadata.get('title')}")
    print(f"   모드: {args.mode.upper()} | 모델: {args.model} | 네임스페이스: {target_ns}")
    print("=" * 60)

    # Alertmanager 모드인 경우 포트포워딩 사전 점검 및 활성화
    pf_proc = None
    if args.mode == "alertmanager":
        pf_proc, args.alertmanager_url = ensure_alertmanager_portforward(args.alertmanager_url, namespace="monitoring")

    # 1. Pre-check (엄격 검증: 실패 시 실행 즉시 중단)
    if not precheck_cluster(target_ns):
        print("❌ 사전 클러스터 헬스체크 실패로 인해 벤치마크를 중단합니다.")
        if pf_proc:
            try:
                pf_proc.terminate()
            except Exception:
                pass
        sys.exit(1)

    cleanup_success = False
    pipeline_error = None
    fault_injected = False
    scorecard = None
    mttd = 0.0
    firing_alerts = []
    run_id = f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    run_dir = results_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    output_file = run_dir / f"{case_id}_rca.json"

    if output_file.exists():
        output_file.unlink()

    try:
        # 2. Inject
        fault_injected = inject_fault(case_dir)
        if not fault_injected:
            raise RuntimeError("고의 장애 주입 스크립트 실행 실패")

        # 3. Alert Detection (Alertmanager 모드인 경우 firing 알람 대기 및 MTTD 산출)
        investigate_mode = args.mode
        if args.mode == "alertmanager":
            detected, mttd, firing_alerts = wait_for_firing_alerts(
                args.alertmanager_url,
                namespace=target_ns,
                timeout=120,
                poll_interval=3.0,
            )
            if not detected:
                print("   ⚠️ Alertmanager 알람 미발생으로 인해 메타데이터 질의문(ASK) 모드로 자동 폴백 전환합니다.", flush=True)
                investigate_mode = "ask"

        # 4. Investigate (MTTI/TTD 측정)
        duration, holmes_returncode = run_holmes(
            mode=investigate_mode,
            query=metadata.get("query", ""),
            output_file=output_file,
            alertmanager_url=args.alertmanager_url,
            namespace=target_ns,
            model=args.model,
            max_steps=args.max_steps,
            timeout=args.timeout,
        )

        # 5. Evaluate & Scorecard
        scorecard = evaluate_results(
            metadata,
            output_file,
            duration,
            mttd=mttd,
            firing_alerts=firing_alerts,
            mode=investigate_mode,
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
        # 6. Cleanup (원상 복구 보장)
        cleanup_success = cleanup_fault(case_dir, namespace=target_ns)
        if not cleanup_success:
            print("🚨 [CRITICAL ALERT] 클러스터 복구 및 검증 실패! 클러스터 상태를 즉시 수동 점검해야 합니다.", flush=True)

        if pf_proc:
            try:
                pf_proc.terminate()
            except Exception:
                pass

        # scorecard 파일 갱신 및 저장
        if run_dir is not None:
            if scorecard is None:
                fail_status = "FAILED_INTERRUPT" if isinstance(pipeline_error, KeyboardInterrupt) else "FAILED_PIPELINE"
                scorecard = {
                    "Run ID": run_id,
                    "Case ID": metadata.get("case_id"),
                    "Title": metadata.get("title"),
                    "Timestamp": datetime.now().isoformat(),
                    "Investigation Mode": args.mode,
                    "CA (Component Accuracy, 100점)": 0.0,
                    "FA (Fault Type Accuracy, 100점)": 0.0,
                    "EA (Evidence Accuracy, 100점)": 0.0,
                    "JRA (Joint Root-Cause Accuracy, 100점)": 0.0,
                    "False Attribution Rate (%)": 0.0,
                    "MTTD (Detection Time, sec)": mttd,
                    "MTTI / TTD (Diagnosis Time, sec)": 0.0,
                    "Triggered Alerts": [a.get("labels", {}).get("alertname", "Unknown") for a in firing_alerts],
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
                scorecard["JRA (Joint Root-Cause Accuracy, 100점)"] = 0.0

            run_scorecard_path = run_dir / f"{case_id}_scorecard.json"
            with open(run_scorecard_path, "w", encoding="utf-8") as f:
                json.dump(scorecard, f, indent=2, ensure_ascii=False)

            # 최신 결과 파일 갱신
            latest_output_file = results_dir / f"{case_id}_rca.json"
            latest_scorecard_path = results_dir / f"{case_id}_scorecard.json"
            if output_file.exists():
                shutil.copyfile(output_file, latest_output_file)
            else:
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

    # 벤치마크 최종 합격 판정
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

    print("\n🎉 모든 파이프라인(주입 ➡️ 감지(MTTD) ➡️ 무인 자율진단(MTTI) ➡️ 채점 ➡️ 복구) 1사이클 정상 관통 완료!")


if __name__ == "__main__":
    main()
