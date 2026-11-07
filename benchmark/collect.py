"""Collect the AWS diagnosis for the one alert caused by a benchmark injection."""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from runner import (cleanup_fault, ensure_alertmanager_portforward, inject_fault,
                    load_env, precheck_cluster)
from usage_display import usage_lines
import order_case

load_env()


def alert_identity(alert):
    starts_at = alert.get("startsAt")
    if starts_at:
        started = datetime.fromisoformat(starts_at.replace("Z", "+00:00"))
        starts_at = started.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return alert.get("fingerprint"), starts_at


def get_active_alerts(url):
    with urllib.request.urlopen(f"{url.rstrip('/')}/api/v2/alerts", timeout=5) as response:
        return [a for a in json.load(response) if a.get("status", {}).get("state") == "active"]


def check_case_prerequisites(metadata):
    """Case 04 needs the Redis-aware cartservice image and the corrected alert rule."""
    if metadata["case_id"] != "service_04_cartservice_redis_misconfig":
        return

    def kubectl_json(*args):
        result = subprocess.run(["kubectl", *args, "-o", "json"], capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(f"case 04 prerequisite check failed: kubectl {' '.join(args)}: {result.stderr.strip()}")
        return json.loads(result.stdout)

    deployment = kubectl_json("get", "deployment", "cartservice", "-n", metadata["namespace"])
    container = next((item for item in deployment["spec"]["template"]["spec"]["containers"]
                      if item["name"] == "server"), None)
    if not container or "cartservice-benchmark" not in container.get("image", ""):
        raise RuntimeError("case 04 requires the cartservice-benchmark image with a Redis-aware health check")
    if container.get("readinessProbe", {}).get("grpc", {}).get("service") != "readiness":
        raise RuntimeError("case 04 requires cartservice gRPC readiness probe service=readiness")
    if container.get("livenessProbe", {}).get("grpc", {}).get("service") != "liveness":
        raise RuntimeError("case 04 requires cartservice gRPC liveness probe service=liveness")

    rules = kubectl_json("get", "prometheusrule", "benchmark-fast-alerts", "-n", "monitoring")
    health_rules = [rule for group in rules["spec"]["groups"] for rule in group["rules"]
                    if rule.get("alert") == "ServicePodNotHealthy"]
    if len(health_rules) != 1 or not re.search(r"\band\s+on\s*\(\s*namespace\s*,\s*pod\s*\)",
                                                 health_rules[0]["expr"]):
        raise RuntimeError("case 04 requires ServicePodNotHealthy to match readiness and phase on(namespace, pod)")


def matches(alert, metadata):
    labels = alert.get("labels", {})
    expected = metadata["expected_alert"]
    if labels.get("namespace") != metadata.get("namespace", "onlineboutique"):
        return False
    if labels.get("alertname") not in expected["names"]:
        return False
    service = expected["service"]
    return any(labels.get(field) == service or labels.get(field, "").startswith(service + "-")
               for field in expected["service_labels"])


def choose_alert(alerts, baseline, metadata):
    candidates = [a for a in alerts if alert_identity(a) not in baseline and matches(a, metadata)]
    if not candidates:
        return None, "missing"
    priority = metadata["expected_alert"]["names"]
    best = min(priority.index(a["labels"]["alertname"]) for a in candidates)
    selected = [a for a in candidates if priority.index(a["labels"]["alertname"]) == best]
    return (selected[0], "selected") if len(selected) == 1 else (None, "ambiguous")


def wait_for_alert(url, baseline, metadata, timeout, interval=3, reconnect=None):
    start = time.monotonic()
    fallback = (None, "missing")
    while time.monotonic() - start < timeout:
        try:
            alert, state = choose_alert(get_active_alerts(url), baseline, metadata)
        except OSError:
            fallback = (None, "missing")
            if reconnect:
                reconnect()
            time.sleep(interval)
            continue
        # A generic health alert can precede the scenario-specific alert.
        # Wait for the preferred kind; retain a generic candidate as fallback.
        if alert and alert["labels"]["alertname"] == metadata["expected_alert"]["names"][0]:
            return alert, state, time.monotonic() - start
        fallback = (alert, state)
        time.sleep(interval)
    return *fallback, time.monotonic() - start


def s3_key(alert):
    fingerprint, starts_at = alert_identity(alert)
    if not fingerprint or not re.fullmatch(r"[a-zA-Z0-9_-]+", fingerprint):
        raise ValueError("invalid alert fingerprint")
    started = datetime.fromisoformat(starts_at.replace("Z", "+00:00"))
    if started.tzinfo is None:
        raise ValueError("startsAt lacks timezone")
    return f"alerts/{started.astimezone(timezone.utc):%Y-%m-%d}/{fingerprint}/{started.astimezone(timezone.utc):%Y%m%dT%H%M%S%fZ}/result.json"


def wait_for_result(s3, bucket, alert, timeout, interval=3):
    from botocore.exceptions import ClientError
    key = s3_key(alert)
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        try:
            obj = s3.get_object(Bucket=bucket, Key=key)
            result = json.load(obj["Body"])
            if alert_identity(result) != alert_identity(alert):
                raise ValueError("S3 result identity mismatch")
            return result, key
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") not in ("NoSuchKey", "404"):
                raise
        time.sleep(interval)
    raise TimeoutError(f"S3 diagnosis not available: s3://{bucket}/{key}")


def report_text(result):
    response = result.get("holmesResponse") or {}
    if isinstance(response, dict):
        for key in ("analysis", "response", "answer", "content"):
            if isinstance(response.get(key), str):
                return response[key]
        for message in reversed(response.get("messages", [])):
            if message.get("role") == "assistant" and message.get("content"):
                return str(message["content"])
    return json.dumps(response, ensure_ascii=False, indent=2)


def holmes_usage_text(result):
    metadata = (result.get("holmesResponse") or {}).get("metadata") or {}
    costs = metadata.get("costs") or {}
    usage = costs if costs.get("total_tokens") is not None else metadata.get("usage") or {}
    scope = "Holmes 진단 전체" if usage is costs and costs else "Holmes 마지막 모델 호출"
    if not usage:
        scope = "사용량 미기록"
    return f"## 진단 리소스 사용량\n\n- **집계 범위**: {scope}\n{usage_lines(usage, costs.get('total_cost'))}"


def used_tempo_tool(result):
    calls = (result.get("holmesResponse") or {}).get("tool_calls") or []
    return any(isinstance(call, dict) and call.get("tool_name", "").startswith("tempo_")
               for call in calls)


def detect_quota_exhaustion(result, fallback_on_503=True):
    """S3 result.json의 에러 문자열 및 attempts에서 429, 402, 503 과부하 패턴 감지"""
    if not isinstance(result, dict):
        return False, ""

    error_candidates = []
    if result.get("error"):
        error_candidates.append(str(result["error"]))
    for attempt in result.get("attempts", []):
        if isinstance(attempt, dict) and attempt.get("error"):
            error_candidates.append(str(attempt["error"]))

    combined_text = " ".join(error_candidates).lower()
    if not combined_text:
        return False, ""

    # 1. HTTP 402 결제 필요 (Payment Required)
    if "402" in combined_text or "payment required" in combined_text:
        return True, "quota_exhausted_402"

    # 2. 명백한 할당량 초과 (HTTP 429, RESOURCE_EXHAUSTED 등)
    quota_keywords = [
        "resource_exhausted",
        "quota exceeded",
        "exceeded quota",
        "quota_exceeded",
        "rate limit",
        "ratelimit",
        "insufficient_quota",
        "too many requests",
    ]
    if "429" in combined_text or any(kw in combined_text for kw in quota_keywords):
        return True, "quota_exhausted_429"

    # 2. HTTP 503 (Service Unavailable / Model Overloaded)
    if "503" in combined_text or "service unavailable" in combined_text:
        if fallback_on_503:
            if any(w in combined_text for w in ("overload", "capacity", "quota", "resource")):
                return True, "503_model_overloaded"
            return True, "503_service_unavailable"

    return False, ""


def switch_holmes_api_key(paid_key, namespace="monitoring", deployment="holmes-holmes", timeout=60):
    """클러스터 유휴 상태에서 kubectl set env로 Holmes Pod의 GEMINI_API_KEY를 유료 키로 교체"""
    if not paid_key:
        print("[Fallback] GEMINI_API_KEY_PAID가 지정되지 않아 유료 키 전환을 건너뜁니다.")
        return False

    print(f"[Fallback] 유휴 상태 감지: {deployment} Pod의 GEMINI_API_KEY를 유료 키로 교체합니다...")
    cmd_set = [
        "kubectl", "set", "env", f"deployment/{deployment}",
        "-n", namespace, f"GEMINI_API_KEY={paid_key}"
    ]
    res_set = subprocess.run(cmd_set, capture_output=True, text=True)
    if res_set.returncode != 0:
        print(f"[Fallback] kubectl set env 실패: {res_set.stderr.strip()}")
        return False

    cmd_wait = [
        "kubectl", "rollout", "status", f"deployment/{deployment}",
        "-n", namespace, f"--timeout={timeout}s"
    ]
    res_wait = subprocess.run(cmd_wait, capture_output=True, text=True)
    if res_wait.returncode != 0:
        print(f"[Fallback] rollout status 대기 실패: {res_wait.stderr.strip()}")
        return False

    print(f"[Fallback] {deployment} 유료 키 교체 및 롤아웃 정상 완료.")
    return True


def collect(metadata, case_dir, run_dir, alertmanager_url, bucket, alert_timeout, result_timeout,
            version="v1", draft=False, fallback_on_503=True, paid_key=None):
    import boto3
    run_dir.mkdir(parents=True, exist_ok=False)
    state = {"status": "started", "case_id": metadata["case_id"], "version": version,
             "draft": draft, "run": int(run_dir.name), "startedAt": datetime.now(timezone.utc).isoformat()}
    injected = False
    order_case_run = metadata["case_id"] == "service_05_order_latency"
    original_delay = None
    port_forwards = []
    try:
        pf, alertmanager_url = ensure_alertmanager_portforward(alertmanager_url)
        if pf:
            port_forwards.append(pf)
        if not precheck_cluster(metadata["namespace"]):
            raise RuntimeError("precheck failed")
        check_case_prerequisites(metadata)
        if order_case_run:
            original_delay = order_case.original_delay(metadata["namespace"])
            baseline_orders, baseline_metrics = order_case.order_samples()
            state["baseline_orders"] = baseline_orders
            (run_dir / "baseline_metrics.prom").write_text(baseline_metrics, encoding="utf-8")
            order_case.require_valid(baseline_orders, maximum=0.9)
        baseline = {alert_identity(a) for a in get_active_alerts(alertmanager_url)}
        if order_case_run and any(matches(a, metadata) for a in get_active_alerts(alertmanager_url)):
            raise ValueError("invalid experiment: order latency alert already firing before injection")
        state["baseline_alerts"] = [list(item) for item in sorted(baseline)]
        injected = True  # cleanup even when the injection script partially succeeds
        if order_case_run:
            order_case.set_delay(metadata["namespace"], "1000")
            injected_orders, injected_metrics = order_case.order_samples()
            state["injected_orders"] = injected_orders
            (run_dir / "injected_metrics.prom").write_text(injected_metrics, encoding="utf-8")
            order_case.require_valid(injected_orders, minimum=1.0)
        elif not inject_fault(case_dir):
            raise RuntimeError("fault injection failed")
        def reconnect_alertmanager():
            new_pf, _ = ensure_alertmanager_portforward(alertmanager_url)
            if new_pf:
                port_forwards.append(new_pf)

        alert, selection, mttd = wait_for_alert(
            alertmanager_url, baseline, metadata, alert_timeout,
            reconnect=reconnect_alertmanager,
        )
        state["MTTD (Detection Time, sec)"] = round(mttd, 2)
        if alert is None:
            state["status"] = selection
            raise RuntimeError(f"alert selection: {selection}")
        state["selected_alert"] = alert
        state["alert_fingerprint"], state["alert_starts_at"] = alert_identity(alert)
        result, key = wait_for_result(boto3.client("s3"), bucket, alert, result_timeout)
        state["s3_key"] = key
        state["s3_uri"] = f"s3://{bucket}/{key}"
        state["MTTI / TTD (Diagnosis Time, sec)"] = result.get("diagnosisDurationSeconds")
        if order_case_run:
            start = datetime.fromisoformat(state["startedAt"]).timestamp() - 30
            end = datetime.now(timezone.utc).timestamp() + 30
            try:
                traces = order_case.tempo_search(start, end)
                (run_dir / "tempo_search.json").write_text(json.dumps(traces, indent=2), encoding="utf-8")
                state["tempo_trace_count"] = len(traces.get("traces", []))
                state["linked_order_trace_ids"] = traces.get("linked_order_trace_ids", [])
            except Exception as exc:
                state["tempo_search_error"] = str(exc)
            state["holmes_tempo_used"] = used_tempo_tool(result)
        (run_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        if result.get("status") != "succeeded":
            is_quota, quota_reason = detect_quota_exhaustion(result, fallback_on_503=fallback_on_503)
            if is_quota:
                state["status"] = "FAILED_QUOTA_EXHAUSTED"
                state["quota_exhausted"] = True
                state["quota_reason"] = quota_reason
                raise RuntimeError(f"Gemini API quota exhausted ({quota_reason}): {result.get('error', 'Holmes diagnosis failed')}")
            state["status"] = "diagnosis_failed"
            raise RuntimeError(result.get("error", "Holmes diagnosis failed"))
        (run_dir / "report.md").write_text(
            f"# RCA: {metadata['title']}\n\n"
            f"알람 fingerprint: `{state['alert_fingerprint']}`  \n"
            f"알람 시작: `{state['alert_starts_at']}`  \n"
            f"S3 원본: `{state['s3_uri']}`\n\n{report_text(result)}\n\n"
            f"{holmes_usage_text(result)}\n", encoding="utf-8")
        state["status"] = "collected"
    except Exception as exc:
        if state["status"] == "started":
            state["status"] = ("invalid_experiment" if order_case_run and isinstance(exc, ValueError)
                               else "s3_timeout" if isinstance(exc, TimeoutError) else "failed")
        state["error"] = str(exc)
    finally:
        if injected:
            if order_case_run:
                try:
                    order_case.set_delay(metadata["namespace"], original_delay)
                    state["cleanup_success"] = True
                except Exception as exc:
                    state["cleanup_success"] = False
                    state["cleanup_error"] = str(exc)
            else:
                state["cleanup_success"] = cleanup_fault(case_dir, namespace=metadata["namespace"])
            if not state["cleanup_success"]:
                state["status"] = "cleanup_failed"
            elif order_case_run and state.get("selected_alert"):
                deadline = time.monotonic() + 150
                identity = alert_identity(state["selected_alert"])
                while time.monotonic() < deadline:
                    try:
                        if identity not in {alert_identity(a) for a in get_active_alerts(alertmanager_url)}:
                            state["alert_resolved"] = True
                            break
                    except OSError as exc:
                        state["alert_resolution_error"] = str(exc)
                    time.sleep(5)
                else:
                    state["alert_resolved"] = False
                    state["status"] = "alert_not_resolved"

        # 클린업 완료 후 유휴(Idle) 상태에서 API 키 전환 수행
        target_paid_key = paid_key or os.environ.get("GEMINI_API_KEY_PAID")
        if state.get("quota_exhausted") and state.get("cleanup_success", True):
            if target_paid_key:
                switch_ok = switch_holmes_api_key(target_paid_key)
                state["api_key_fallback"] = "switched_to_paid" if switch_ok else "switch_failed"
            else:
                state["api_key_fallback"] = "paid_key_not_configured"

        for pf in port_forwards:
            pf.terminate()
        state["finishedAt"] = datetime.now(timezone.utc).isoformat()
        (run_dir / "run_info.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return state


def main():
    parser = argparse.ArgumentParser(description="Inject a fault and collect its AWS alert diagnosis")
    parser.add_argument("--case", default="service_01_cartservice_down")
    parser.add_argument("--version", "-v", default="v1")
    parser.add_argument("--run", type=int)
    parser.add_argument("--draft", action="store_true")
    parser.add_argument("--alertmanager-url", default="http://localhost:9093")
    parser.add_argument("--bucket", default=os.environ.get("ALERT_RESULTS_BUCKET"))
    parser.add_argument("--alert-timeout", type=int, default=120)
    parser.add_argument("--result-timeout", type=int, default=360)
    parser.add_argument("--fallback-on-503", action="store_true", default=True,
                        help="HTTP 503 지속 실패 시에도 유료 키 Fallback 허용 (기본: True)")
    parser.add_argument("--no-fallback-on-503", dest="fallback_on_503", action="store_false",
                        help="HTTP 503 발생 시 유료 키 Fallback 비활성화")
    parser.add_argument("--paid-key", default=os.environ.get("GEMINI_API_KEY_PAID"),
                        help="비상용 유료 Gemini API 키 (기본: GEMINI_API_KEY_PAID 환경변수)")
    parser.add_argument("--retry-on-fallback", action="store_true",
                        help="할당량 초과로 유료 키 전환 성공 시 해당 케이스를 즉시 1회 자동 재실행")
    args = parser.parse_args()
    if not args.bucket:
        parser.error("--bucket or ALERT_RESULTS_BUCKET is required")
    valid_name = r"[A-Za-z0-9][A-Za-z0-9_.-]*"
    if not re.fullmatch(valid_name, args.version) or not re.fullmatch(valid_name, args.case):
        parser.error("invalid case or version")
    if args.run is not None and args.run < 1:
        parser.error("--run must be a positive integer")
    root = Path(__file__).resolve().parent
    case_dir = root / "cases" / args.case
    metadata = json.loads((case_dir / "metadata.json").read_text(encoding="utf-8"))
    if metadata.get("case_id") != args.case:
        parser.error("metadata case_id does not match --case")
    version_dir = root / "results" / ("draft" if args.draft else "") / args.case / args.version
    version_dir.mkdir(parents=True, exist_ok=True)
    run = args.run or max((int(p.name) for p in version_dir.iterdir() if p.is_dir() and p.name.isdigit()), default=0) + 1
    state = collect(metadata, case_dir, version_dir / str(run), args.alertmanager_url,
                    args.bucket, args.alert_timeout, args.result_timeout, args.version, args.draft,
                    fallback_on_503=args.fallback_on_503, paid_key=args.paid_key)

    if (state.get("status") == "FAILED_QUOTA_EXHAUSTED"
            and args.retry_on_fallback
            and state.get("api_key_fallback") == "switched_to_paid"):
        print("[Fallback] 할당량 초과로 유료 키 교체 완료. --retry-on-fallback에 따라 다음 회차로 즉시 재실행합니다.")
        next_run = max((int(p.name) for p in version_dir.iterdir() if p.is_dir() and p.name.isdigit()), default=0) + 1
        state = collect(metadata, case_dir, version_dir / str(next_run), args.alertmanager_url,
                        args.bucket, args.alert_timeout, args.result_timeout, args.version, args.draft,
                        fallback_on_503=args.fallback_on_503, paid_key=args.paid_key)
        run = next_run

    print(json.dumps({"run": str(version_dir / str(run)), "status": state["status"]}, ensure_ascii=False))
    return 0 if state["status"] == "collected" else 1


if __name__ == "__main__":
    sys.exit(main())
