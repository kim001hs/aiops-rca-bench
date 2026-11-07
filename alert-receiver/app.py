"""Alertmanager webhook -> asynchronous in-memory queue, Holmes investigation with retries, and immutable S3 results."""
import http.client
import json
import logging
import os
import queue
import re
import signal
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import boto3
from botocore.exceptions import ClientError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(threadName)s] %(message)s",
)

BUCKET = os.environ.get("RESULT_BUCKET", "")
HOLMES_URL = os.environ.get(
    "HOLMES_URL", "http://holmes-holmes.monitoring.svc.cluster.local:80/api/chat"
)
MODEL = os.environ.get("HOLMES_MODEL", "default-model")
PROMPT = "최종 진단 소견, 근거와 권장 조치를 한국어로 작성하세요."
CLAIM_TTL_SECONDS = int(os.environ.get("CLAIM_TTL_SECONDS", "300"))
MAX_RETRIES = int(os.environ.get("MAX_RETRIES", "3"))
BASE_BACKOFF_SECONDS = float(os.environ.get("BASE_BACKOFF_SECONDS", "2.0"))
HOLMES_TIMEOUT = int(os.environ.get("HOLMES_TIMEOUT", "300"))
WORKER_COUNT = int(os.environ.get("WORKER_COUNT", "4"))

s3 = boto3.client("s3")

ALERT_QUEUE = queue.Queue(maxsize=1000)
ACTIVE_CLAIMS = set()
CLAIMS_LOCK = threading.Lock()
SHUTDOWN_EVENT = threading.Event()
WORKER_THREADS = []


class DiagnosisError(Exception):
    """Raised when Holmes diagnosis fails after all retry attempts."""

    def __init__(self, message, attempts=None):
        super().__init__(message)
        self.attempts = attempts or []


def object_key(alert):
    fingerprint, starts_at = alert.get("fingerprint"), alert.get("startsAt")
    if not fingerprint or not starts_at:
        raise ValueError("alert requires fingerprint and startsAt")
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", fingerprint):
        raise ValueError("invalid fingerprint")
    started = datetime.fromisoformat(starts_at.replace("Z", "+00:00"))
    if started.tzinfo is None:
        raise ValueError("startsAt must include timezone")
    started = started.astimezone(timezone.utc).replace(
        microsecond=(started.microsecond // 1000) * 1000
    )
    safe_time = started.strftime("%Y%m%dT%H%M%S%fZ")
    return f"alerts/{started.astimezone(timezone.utc):%Y-%m-%d}/{fingerprint}/{safe_time}/result.json"


def http_error_detail(exc):
    if not isinstance(exc, urllib.error.HTTPError):
        return ""
    try:
        body = exc.read()
        payload = json.loads(body)
        return str(payload.get("detail", "")) if isinstance(payload, dict) else ""
    except (OSError, ValueError, TypeError):
        return ""


def is_transient_error(exc, detail=""):
    if isinstance(exc, urllib.error.HTTPError):
        if "Too many LLM calls - exceeded max_steps" in detail:
            return False
        return 500 <= exc.code <= 599 or exc.code == 429
    if isinstance(
        exc,
        (
            urllib.error.URLError,
            TimeoutError,
            socket.timeout,
            ConnectionError,
            http.client.IncompleteRead,
            http.client.RemoteDisconnected,
        ),
    ):
        return True
    return False


def diagnose(alert):
    question = (
        "Investigate this firing Alertmanager alert in Kubernetes. Identify the root cause, "
        "supporting evidence, and remediation. Alert data: "
        + json.dumps(alert, ensure_ascii=False)
    )
    body = json.dumps(
        {"ask": question, "model": MODEL, "additional_system_prompt": PROMPT}
    ).encode("utf-8")

    attempts = []
    last_exc = None

    for attempt_idx in range(1, MAX_RETRIES + 1):
        attempt_start = time.monotonic()
        attempt_ts = datetime.now(timezone.utc).isoformat()
        req = urllib.request.Request(
            HOLMES_URL, body, {"Content-Type": "application/json"}
        )

        try:
            with urllib.request.urlopen(req, timeout=HOLMES_TIMEOUT) as response:
                resp_data = json.load(response)
            duration = round(time.monotonic() - attempt_start, 2)
            attempts.append(
                {
                    "attempt": attempt_idx,
                    "timestamp": attempt_ts,
                    "durationSeconds": duration,
                    "error": None,
                    "status": "succeeded",
                }
            )
            return resp_data, attempts
        except Exception as exc:
            duration = round(time.monotonic() - attempt_start, 2)
            detail = http_error_detail(exc)
            error_message = f"{exc}: {detail}" if detail else str(exc)
            last_exc = error_message
            attempts.append(
                {
                    "attempt": attempt_idx,
                    "timestamp": attempt_ts,
                    "durationSeconds": duration,
                    "error": error_message,
                    "status": "failed",
                }
            )
            if (
                attempt_idx < MAX_RETRIES
                and is_transient_error(exc, detail)
                and not SHUTDOWN_EVENT.is_set()
            ):
                backoff = BASE_BACKOFF_SECONDS * (2 ** (attempt_idx - 1))
                time.sleep(backoff)
            else:
                break

    raise DiagnosisError(str(last_exc), attempts=attempts)


def is_claim_stale(claim_obj, now_dt=None):
    if now_dt is None:
        now_dt = datetime.now(timezone.utc)
    body_val = claim_obj.get("Body")
    if hasattr(body_val, "read"):
        body_bytes = body_val.read()
    elif isinstance(body_val, (bytes, bytearray)):
        body_bytes = bytes(body_val)
    elif isinstance(body_val, str):
        body_bytes = body_val.encode("utf-8")
    else:
        body_bytes = b""

    try:
        claim_data = json.loads(body_bytes.decode("utf-8"))
        expires_at_str = claim_data.get("expiresAt")
        if expires_at_str:
            expires_at = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
            return now_dt >= expires_at
    except Exception:
        pass

    last_modified = claim_obj.get("LastModified")
    if last_modified:
        if last_modified.tzinfo is None:
            last_modified = last_modified.replace(tzinfo=timezone.utc)
        age = (now_dt - last_modified.astimezone(timezone.utc)).total_seconds()
        return age >= CLAIM_TTL_SECONDS

    return False


def process_alert(alert, worker_id=None):
    key = object_key(alert)
    claim_key = key + ".claim"
    worker_tag = f"{os.getpid()}-{worker_id or threading.get_ident()}"
    now_utc = datetime.now(timezone.utc)
    claim_payload = {
        "claimedAt": now_utc.isoformat(),
        "expiresAt": (now_utc + timedelta(seconds=CLAIM_TTL_SECONDS)).isoformat(),
        "workerId": worker_tag,
    }
    claim_body = json.dumps(claim_payload, ensure_ascii=False).encode("utf-8")

    # 1. Quick check: if result.json already exists, skip as duplicate
    try:
        s3.head_object(Bucket=BUCKET, Key=key)
        return "duplicate"
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") not in (
            "404",
            "NoSuchKey",
            "NotFound",
        ):
            raise

    # 2. Acquire or check .claim
    try:
        s3.put_object(
            Bucket=BUCKET,
            Key=claim_key,
            Body=claim_body,
            ContentType="application/json",
            IfNoneMatch="*",
        )
    except ClientError as exc:
        err_code = exc.response.get("Error", {}).get("Code", "")
        if err_code in (
            "PreconditionFailed",
            "ConditionalRequestConflict",
            "412",
            "409",
        ):
            # Claim already exists. Verify if result.json was created.
            try:
                s3.head_object(Bucket=BUCKET, Key=key)
                return "duplicate"
            except ClientError as head_exc:
                if head_exc.response.get("Error", {}).get("Code") not in (
                    "404",
                    "NoSuchKey",
                    "NotFound",
                ):
                    raise

            # result.json does not exist. Check if claim is stale.
            stale = False
            try:
                claim_obj = s3.get_object(Bucket=BUCKET, Key=claim_key)
                stale = is_claim_stale(claim_obj)
            except ClientError as get_exc:
                if get_exc.response.get("Error", {}).get("Code") in (
                    "404",
                    "NoSuchKey",
                    "NotFound",
                ):
                    stale = True
                else:
                    raise

            if stale:
                logging.info(f"Reclaiming stale claim for {key}")
                s3.put_object(
                    Bucket=BUCKET,
                    Key=claim_key,
                    Body=claim_body,
                    ContentType="application/json",
                )
            else:
                return "duplicate"
        else:
            raise

    # 3. Track active claim for graceful shutdown cleanup
    with CLAIMS_LOCK:
        ACTIVE_CLAIMS.add(claim_key)

    result = {
        "fingerprint": alert["fingerprint"],
        "startsAt": datetime.fromisoformat(alert["startsAt"].replace("Z", "+00:00"))
        .astimezone(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
        "alert": alert,
        "diagnosisStartedAt": datetime.now(timezone.utc).isoformat(),
        "status": "succeeded",
        "holmesResponse": None,
        "attempts": [],
    }

    started = time.monotonic()
    try:
        holmes_resp, attempts = diagnose(alert)
        result["holmesResponse"] = holmes_resp
        result["attempts"] = attempts
    except Exception as exc:
        result["status"] = "failed"
        result["error"] = str(exc)
        result["attempts"] = getattr(exc, "attempts", [])

    result["diagnosedAt"] = datetime.now(timezone.utc).isoformat()
    result["diagnosisDurationSeconds"] = round(time.monotonic() - started, 2)

    try:
        s3.put_object(
            Bucket=BUCKET,
            Key=key,
            Body=json.dumps(result, ensure_ascii=False).encode("utf-8"),
            ContentType="application/json",
            IfNoneMatch="*",
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("PreconditionFailed", "412"):
            return "duplicate"
        try:
            s3.delete_object(Bucket=BUCKET, Key=claim_key)
        except Exception:
            pass
        raise
    except Exception:
        try:
            s3.delete_object(Bucket=BUCKET, Key=claim_key)
        except Exception:
            pass
        raise
    finally:
        with CLAIMS_LOCK:
            ACTIVE_CLAIMS.discard(claim_key)

    return result["status"]


def worker_loop(worker_id):
    logging.info(f"Worker {worker_id} started")
    while not SHUTDOWN_EVENT.is_set():
        try:
            alert = ALERT_QUEUE.get(timeout=0.5)
        except queue.Empty:
            continue
        try:
            process_alert(alert, worker_id=worker_id)
        except Exception as exc:
            logging.exception(
                f"Worker {worker_id} encountered error processing alert: {exc}"
            )
        finally:
            ALERT_QUEUE.task_done()
    logging.info(f"Worker {worker_id} stopped")


def start_workers(count=WORKER_COUNT):
    for i in range(count):
        t = threading.Thread(
            target=worker_loop, args=(i + 1,), name=f"worker-{i+1}", daemon=True
        )
        t.start()
        WORKER_THREADS.append(t)


def cleanup_active_claims():
    with CLAIMS_LOCK:
        for claim_key in list(ACTIVE_CLAIMS):
            try:
                s3.delete_object(Bucket=BUCKET, Key=claim_key)
                logging.info(f"Released active claim on shutdown: {claim_key}")
            except Exception as exc:
                logging.warning(
                    f"Failed to delete claim {claim_key} on shutdown: {exc}"
                )
        ACTIVE_CLAIMS.clear()


def handle_shutdown(signum, frame):
    logging.info(f"Received signal {signum}, initiating graceful shutdown...")
    SHUTDOWN_EVENT.set()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/healthz":
            if SHUTDOWN_EVENT.is_set():
                self.send_response(503)
                self.end_headers()
                self.wfile.write(b"shutting down")
            else:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path != "/webhook":
            self.send_error(404)
            return

        if SHUTDOWN_EVENT.is_set():
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps({"error": "server is shutting down"}).encode("utf-8")
            )
            return

        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size <= 0 or size > 1024 * 1024:
                raise ValueError("invalid payload size")
            payload = json.loads(self.rfile.read(size).decode("utf-8"))
            firing_alerts = [
                a for a in payload.get("alerts", []) if a.get("status") == "firing"
            ]

            # Validate each firing alert format upfront
            for a in firing_alerts:
                object_key(a)

            # Enqueue firing alerts
            for a in firing_alerts:
                ALERT_QUEUE.put_nowait(a)

            data = json.dumps(
                {"status": "accepted", "queued": len(firing_alerts)}
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except (ValueError, KeyError) as exc:
            data = json.dumps({"error": str(exc)}).encode("utf-8")
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except queue.Full:
            data = json.dumps({"error": "alert queue full"}).encode("utf-8")
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except Exception as exc:
            data = json.dumps({"error": str(exc)}).encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)


if __name__ == "__main__":
    if not BUCKET:
        raise RuntimeError("RESULT_BUCKET is required")

    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)

    start_workers(WORKER_COUNT)

    server = ThreadingHTTPServer(("0.0.0.0", 8080), Handler)
    logging.info("Starting alert-receiver on port 8080...")

    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    while not SHUTDOWN_EVENT.is_set():
        try:
            time.sleep(0.5)
        except KeyboardInterrupt:
            SHUTDOWN_EVENT.set()
            break

    logging.info("Shutting down HTTP server...")
    server.shutdown()
    server.server_close()

    logging.info("Waiting for background workers to drain active queue...")
    drain_start = time.time()
    while not ALERT_QUEUE.empty() and (time.time() - drain_start < 30):
        time.sleep(0.5)

    cleanup_active_claims()
    logging.info("Graceful shutdown complete.")
