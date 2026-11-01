"""Alertmanager webhook -> one Holmes investigation and immutable S3 result per alert."""
import json
import os
import re
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import boto3
from botocore.exceptions import ClientError

BUCKET = os.environ.get("RESULT_BUCKET", "")
HOLMES_URL = os.environ.get("HOLMES_URL", "http://holmes-holmes.monitoring.svc.cluster.local:80/api/chat")
MODEL = os.environ.get("HOLMES_MODEL", "default-model")
PROMPT = "최종 진단 소견, 근거와 권장 조치를 한국어로 작성하세요."
s3 = boto3.client("s3")


def object_key(alert):
    fingerprint, starts_at = alert.get("fingerprint"), alert.get("startsAt")
    if not fingerprint or not starts_at:
        raise ValueError("alert requires fingerprint and startsAt")
    # Reject path traversal and invalid timestamps before constructing an S3 key.
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", fingerprint):
        raise ValueError("invalid fingerprint")
    started = datetime.fromisoformat(starts_at.replace("Z", "+00:00"))
    if started.tzinfo is None:
        raise ValueError("startsAt must include timezone")
    started = started.astimezone(timezone.utc).replace(microsecond=(started.microsecond // 1000) * 1000)
    safe_time = started.strftime("%Y%m%dT%H%M%S%fZ")
    return f"alerts/{started.astimezone(timezone.utc):%Y-%m-%d}/{fingerprint}/{safe_time}/result.json"


def diagnose(alert):
    question = (
        "Investigate this firing Alertmanager alert in Kubernetes. Identify the root cause, "
        "supporting evidence, and remediation. Alert data: "
        + json.dumps(alert, ensure_ascii=False)
    )
    body = json.dumps({"ask": question, "model": MODEL, "additional_system_prompt": PROMPT}).encode()
    req = urllib.request.Request(HOLMES_URL, body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=int(os.environ.get("HOLMES_TIMEOUT", "300"))) as response:
        return json.load(response)


def process_alert(alert):
    key = object_key(alert)
    # Conditional creation makes webhook redelivery and concurrent requests idempotent.
    try:
        s3.put_object(Bucket=BUCKET, Key=key + ".claim", Body=b"claimed", IfNoneMatch="*")
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("PreconditionFailed", "ConditionalRequestConflict", "412", "409"):
            return "duplicate"
        raise
    result = {
        "fingerprint": alert["fingerprint"],
        "startsAt": datetime.fromisoformat(alert["startsAt"].replace("Z", "+00:00")).astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "alert": alert, "diagnosisStartedAt": datetime.now(timezone.utc).isoformat(),
        "status": "succeeded", "holmesResponse": None,
    }
    import time
    started = time.monotonic()
    try:
        result["holmesResponse"] = diagnose(alert)
    except Exception as exc:
        result["status"] = "failed"
        result["error"] = str(exc)
    result["diagnosedAt"] = datetime.now(timezone.utc).isoformat()
    result["diagnosisDurationSeconds"] = round(time.monotonic() - started, 2)
    try:
        s3.put_object(Bucket=BUCKET, Key=key,
                      Body=json.dumps(result, ensure_ascii=False).encode(),
                      ContentType="application/json", IfNoneMatch="*")
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("PreconditionFailed", "412"):
            return "duplicate"
        # The webhook will be retried; a failed write must not leave a permanent claim.
        s3.delete_object(Bucket=BUCKET, Key=key + ".claim")
        raise
    except Exception:
        s3.delete_object(Bucket=BUCKET, Key=key + ".claim")
        raise
    return result["status"]


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200 if self.path == "/healthz" else 404)
        self.end_headers()

    def do_POST(self):
        if self.path != "/webhook":
            self.send_error(404)
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size <= 0 or size > 1024 * 1024:
                raise ValueError("invalid payload size")
            payload = json.loads(self.rfile.read(size))
            statuses = [process_alert(a) for a in payload.get("alerts", []) if a.get("status") == "firing"]
            code = 200
        except (ValueError, KeyError) as exc:
            statuses, code = [str(exc)], 400
        except Exception as exc:
            statuses, code = [str(exc)], 500
        data = json.dumps({"statuses": statuses}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    if not BUCKET:
        raise RuntimeError("RESULT_BUCKET is required")
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
