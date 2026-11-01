"""Collect the AWS diagnosis for the one alert caused by a benchmark injection."""
import argparse
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from runner import (cleanup_fault, ensure_alertmanager_portforward, inject_fault,
                    precheck_cluster)
from usage_display import usage_lines


def alert_identity(alert):
    starts_at = alert.get("startsAt")
    if starts_at:
        started = datetime.fromisoformat(starts_at.replace("Z", "+00:00"))
        starts_at = started.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return alert.get("fingerprint"), starts_at


def get_active_alerts(url):
    with urllib.request.urlopen(f"{url.rstrip('/')}/api/v2/alerts", timeout=5) as response:
        return [a for a in json.load(response) if a.get("status", {}).get("state") == "active"]


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


def collect(metadata, case_dir, run_dir, alertmanager_url, bucket, alert_timeout, result_timeout,
            version="v1", draft=False):
    import boto3
    run_dir.mkdir(parents=True, exist_ok=False)
    state = {"status": "started", "case_id": metadata["case_id"], "version": version,
             "draft": draft, "run": int(run_dir.name), "startedAt": datetime.now(timezone.utc).isoformat()}
    injected = False
    port_forwards = []
    try:
        pf, alertmanager_url = ensure_alertmanager_portforward(alertmanager_url)
        if pf:
            port_forwards.append(pf)
        if not precheck_cluster(metadata["namespace"]):
            raise RuntimeError("precheck failed")
        baseline = {alert_identity(a) for a in get_active_alerts(alertmanager_url)}
        state["baseline_alerts"] = [list(item) for item in sorted(baseline)]
        injected = True  # cleanup even when the injection script partially succeeds
        if not inject_fault(case_dir):
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
        (run_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        if result.get("status") != "succeeded":
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
            state["status"] = "s3_timeout" if isinstance(exc, TimeoutError) else "failed"
        state["error"] = str(exc)
    finally:
        if injected:
            state["cleanup_success"] = cleanup_fault(case_dir, namespace=metadata["namespace"])
            if not state["cleanup_success"]:
                state["status"] = "cleanup_failed"
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
                    args.bucket, args.alert_timeout, args.result_timeout, args.version, args.draft)
    print(json.dumps({"run": str(version_dir / str(run)), "status": state["status"]}, ensure_ascii=False))
    return 0 if state["status"] == "collected" else 1


if __name__ == "__main__":
    sys.exit(main())
