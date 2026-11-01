"""Unit tests for alert-receiver application."""
import http.client
import io
import json
import os
import queue
import socket
import sys
import threading
import time
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, call, patch

# Ensure alert-receiver module is in sys.path
sys.path.insert(0, os.path.dirname(__file__))

import app


class TestObjectKey(unittest.TestCase):
    def test_valid_alert(self):
        alert = {
            "fingerprint": "alert-12345",
            "startsAt": "2026-11-01T12:30:45.123456Z",
        }
        key = app.object_key(alert)
        self.assertEqual(
            key, "alerts/2026-11-01/alert-12345/20261101T123045123000Z/result.json"
        )

    def test_missing_fingerprint(self):
        alert = {"startsAt": "2026-11-01T12:30:45Z"}
        with self.assertRaises(ValueError):
            app.object_key(alert)

    def test_invalid_fingerprint_characters(self):
        alert = {
            "fingerprint": "../bad/path",
            "startsAt": "2026-11-01T12:30:45Z",
        }
        with self.assertRaises(ValueError):
            app.object_key(alert)

    def test_missing_timezone(self):
        alert = {
            "fingerprint": "alert-12345",
            "startsAt": "2026-11-01T12:30:45",
        }
        with self.assertRaises(ValueError):
            app.object_key(alert)


class TestTransientErrorDetection(unittest.TestCase):
    def test_http_errors(self):
        # 5xx should be transient
        for code in (500, 502, 503, 504, 429):
            err = urllib.error.HTTPError("http://test", code, "Err", {}, None)
            self.assertTrue(app.is_transient_error(err), f"Code {code} should be transient")

        # 4xx (except 429) should NOT be transient
        for code in (400, 401, 403, 404):
            err = urllib.error.HTTPError("http://test", code, "Err", {}, None)
            self.assertFalse(app.is_transient_error(err), f"Code {code} should not be transient")

    def test_network_and_timeout_errors(self):
        self.assertTrue(app.is_transient_error(urllib.error.URLError("Network down")))
        self.assertTrue(app.is_transient_error(TimeoutError("Timeout")))
        self.assertTrue(app.is_transient_error(socket.timeout("Socket timeout")))
        self.assertTrue(app.is_transient_error(ConnectionError("Connection lost")))
        self.assertTrue(app.is_transient_error(http.client.RemoteDisconnected("Remote disconnected")))


class TestDiagnoseWithRetry(unittest.TestCase):
    def setUp(self):
        app.SHUTDOWN_EVENT.clear()
        app.BASE_BACKOFF_SECONDS = 0.001  # Fast backoff for tests

    @patch("time.sleep")
    @patch("urllib.request.urlopen")
    def test_first_attempt_success(self, mock_urlopen, mock_sleep):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"answer": "all good"}).encode()
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        alert = {"fingerprint": "fp1", "startsAt": "2026-11-01T10:00:00Z"}
        data, attempts = app.diagnose(alert)

        self.assertEqual(data, {"answer": "all good"})
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["attempt"], 1)
        self.assertIsNone(attempts[0]["error"])
        self.assertEqual(attempts[0]["status"], "succeeded")
        self.assertIn("timestamp", attempts[0])
        self.assertIn("durationSeconds", attempts[0])
        mock_sleep.assert_not_called()

    @patch("time.sleep")
    @patch("urllib.request.urlopen")
    def test_retry_on_transient_error_then_success(self, mock_urlopen, mock_sleep):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"answer": "recovered"}).encode()
        mock_resp.__enter__.return_value = mock_resp

        # 1st attempt: 503, 2nd attempt: URLError, 3rd attempt: 200 OK
        err_503 = urllib.error.HTTPError("http://test", 503, "Unavailable", {}, None)
        err_url = urllib.error.URLError("connection reset")
        mock_urlopen.side_effect = [err_503, err_url, mock_resp]

        alert = {"fingerprint": "fp1", "startsAt": "2026-11-01T10:00:00Z"}
        data, attempts = app.diagnose(alert)

        self.assertEqual(data, {"answer": "recovered"})
        self.assertEqual(len(attempts), 3)
        self.assertEqual(attempts[0]["status"], "failed")
        self.assertIn("503", attempts[0]["error"])
        self.assertEqual(attempts[1]["status"], "failed")
        self.assertEqual(attempts[2]["status"], "succeeded")
        self.assertIsNone(attempts[2]["error"])
        self.assertEqual(mock_sleep.call_count, 2)

    @patch("time.sleep")
    @patch("urllib.request.urlopen")
    def test_non_transient_error_fails_immediately(self, mock_urlopen, mock_sleep):
        err_400 = urllib.error.HTTPError("http://test", 400, "Bad Request", {}, None)
        mock_urlopen.side_effect = err_400

        alert = {"fingerprint": "fp1", "startsAt": "2026-11-01T10:00:00Z"}
        with self.assertRaises(app.DiagnosisError) as ctx:
            app.diagnose(alert)

        self.assertEqual(len(ctx.exception.attempts), 1)
        self.assertEqual(ctx.exception.attempts[0]["attempt"], 1)
        self.assertEqual(ctx.exception.attempts[0]["status"], "failed")
        mock_sleep.assert_not_called()

    @patch("time.sleep")
    @patch("urllib.request.urlopen")
    def test_max_retries_exceeded(self, mock_urlopen, mock_sleep):
        err_500 = urllib.error.HTTPError("http://test", 500, "Internal Server Error", {}, None)
        mock_urlopen.side_effect = [err_500, err_500, err_500]

        alert = {"fingerprint": "fp1", "startsAt": "2026-11-01T10:00:00Z"}
        with self.assertRaises(app.DiagnosisError) as ctx:
            app.diagnose(alert)

        self.assertEqual(len(ctx.exception.attempts), 3)
        for att in ctx.exception.attempts:
            self.assertEqual(att["status"], "failed")
        self.assertEqual(mock_sleep.call_count, 2)


class TestIsClaimStale(unittest.TestCase):
    def test_active_claim_not_stale(self):
        now = datetime.now(timezone.utc)
        claim_data = {
            "claimedAt": now.isoformat(),
            "expiresAt": (now + timedelta(seconds=300)).isoformat(),
            "workerId": "worker-1",
        }
        claim_obj = {"Body": json.dumps(claim_data).encode("utf-8")}
        self.assertFalse(app.is_claim_stale(claim_obj, now_dt=now))

    def test_expired_claim_is_stale(self):
        now = datetime.now(timezone.utc)
        claim_data = {
            "claimedAt": (now - timedelta(seconds=400)).isoformat(),
            "expiresAt": (now - timedelta(seconds=100)).isoformat(),
            "workerId": "worker-1",
        }
        claim_obj = {"Body": json.dumps(claim_data).encode("utf-8")}
        self.assertTrue(app.is_claim_stale(claim_obj, now_dt=now))

    def test_legacy_claim_with_last_modified(self):
        now = datetime.now(timezone.utc)
        # Old last modified (> 300s)
        claim_obj = {
            "Body": b"claimed",
            "LastModified": now - timedelta(seconds=350),
        }
        self.assertTrue(app.is_claim_stale(claim_obj, now_dt=now))

        # Recent last modified (< 300s)
        recent_claim = {
            "Body": b"claimed",
            "LastModified": now - timedelta(seconds=50),
        }
        self.assertFalse(app.is_claim_stale(recent_claim, now_dt=now))


class TestProcessAlert(unittest.TestCase):
    def setUp(self):
        app.BUCKET = "test-bucket"
        app.ACTIVE_CLAIMS.clear()
        app.SHUTDOWN_EVENT.clear()
        self.mock_s3 = MagicMock()
        app.s3 = self.mock_s3

    @patch("app.diagnose")
    def test_first_time_success(self, mock_diagnose):
        mock_diagnose.return_value = (
            {"summary": "root cause found"},
            [{"attempt": 1, "status": "succeeded", "durationSeconds": 0.5, "error": None}],
        )

        # head_object on result.json -> 404 (doesn't exist)
        from botocore.exceptions import ClientError
        self.mock_s3.head_object.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}}, "head_object"
        )
        self.mock_s3.put_object.return_value = {}

        alert = {
            "fingerprint": "alert-fp1",
            "startsAt": "2026-11-01T10:00:00.000Z",
        }
        status = app.process_alert(alert, worker_id=1)

        self.assertEqual(status, "succeeded")
        # Put claim called, put result called
        self.assertEqual(self.mock_s3.put_object.call_count, 2)
        # Result content verified
        result_call = self.mock_s3.put_object.call_args_list[1]
        result_body = json.loads(result_call.kwargs["Body"].decode("utf-8"))
        self.assertEqual(result_body["status"], "succeeded")
        self.assertEqual(result_body["fingerprint"], "alert-fp1")
        self.assertEqual(result_body["holmesResponse"], {"summary": "root cause found"})
        self.assertEqual(len(result_body["attempts"]), 1)
        # Active claims cleaned up
        self.assertEqual(len(app.ACTIVE_CLAIMS), 0)

    @patch("app.diagnose")
    def test_duplicate_result_exists_initially(self, mock_diagnose):
        # head_object on result.json succeeds -> duplicate
        self.mock_s3.head_object.return_value = {"ContentLength": 123}

        alert = {"fingerprint": "alert-fp1", "startsAt": "2026-11-01T10:00:00Z"}
        status = app.process_alert(alert)

        self.assertEqual(status, "duplicate")
        mock_diagnose.assert_not_called()
        self.mock_s3.put_object.assert_not_called()

    @patch("app.diagnose")
    def test_claim_conflict_with_result_existing(self, mock_diagnose):
        from botocore.exceptions import ClientError
        # 1st head_object raises 404, put_object(claim) raises 412, 2nd head_object succeeds
        err_404 = ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "head_object")
        err_412 = ClientError({"Error": {"Code": "PreconditionFailed", "Message": "Conflict"}}, "put_object")
        self.mock_s3.head_object.side_effect = [err_404, {"ContentLength": 100}]
        self.mock_s3.put_object.side_effect = err_412

        alert = {"fingerprint": "alert-fp1", "startsAt": "2026-11-01T10:00:00Z"}
        status = app.process_alert(alert)

        self.assertEqual(status, "duplicate")
        mock_diagnose.assert_not_called()

    @patch("app.diagnose")
    def test_claim_conflict_active_not_stale(self, mock_diagnose):
        from botocore.exceptions import ClientError
        err_404 = ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "head_object")
        err_412 = ClientError({"Error": {"Code": "PreconditionFailed", "Message": "Conflict"}}, "put_object")
        self.mock_s3.head_object.side_effect = err_404
        self.mock_s3.put_object.side_effect = err_412

        # Active claim with future expiresAt
        future_exp = (datetime.now(timezone.utc) + timedelta(seconds=200)).isoformat()
        claim_data = {"claimedAt": datetime.now(timezone.utc).isoformat(), "expiresAt": future_exp}
        self.mock_s3.get_object.return_value = {"Body": io.BytesIO(json.dumps(claim_data).encode("utf-8"))}

        alert = {"fingerprint": "alert-fp1", "startsAt": "2026-11-01T10:00:00Z"}
        status = app.process_alert(alert)

        self.assertEqual(status, "duplicate")
        mock_diagnose.assert_not_called()

    @patch("app.diagnose")
    def test_stale_claim_recovery(self, mock_diagnose):
        from botocore.exceptions import ClientError
        err_404 = ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "head_object")
        err_412 = ClientError({"Error": {"Code": "PreconditionFailed", "Message": "Conflict"}}, "put_object")
        self.mock_s3.head_object.side_effect = err_404

        # First put_object for claim fails with 412, subsequent put_objects succeed
        self.mock_s3.put_object.side_effect = [err_412, {}, {}]

        # Expired claim
        past_exp = (datetime.now(timezone.utc) - timedelta(seconds=50)).isoformat()
        claim_data = {"claimedAt": (datetime.now(timezone.utc) - timedelta(seconds=350)).isoformat(), "expiresAt": past_exp}
        self.mock_s3.get_object.return_value = {"Body": io.BytesIO(json.dumps(claim_data).encode("utf-8"))}

        mock_diagnose.return_value = (
            {"summary": "stale reclaimed"},
            [{"attempt": 1, "status": "succeeded", "durationSeconds": 0.2, "error": None}],
        )

        alert = {"fingerprint": "alert-fp1", "startsAt": "2026-11-01T10:00:00Z"}
        status = app.process_alert(alert)

        self.assertEqual(status, "succeeded")
        mock_diagnose.assert_called_once()
        # Verifies claim was overwritten (reclaimed)
        self.assertEqual(self.mock_s3.put_object.call_count, 3)

    @patch("app.diagnose")
    def test_diagnosis_failure_recorded_in_s3(self, mock_diagnose):
        from botocore.exceptions import ClientError
        err_404 = ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "head_object")
        self.mock_s3.head_object.side_effect = err_404
        self.mock_s3.put_object.return_value = {}

        attempts = [
            {"attempt": 1, "status": "failed", "durationSeconds": 0.1, "error": "500"},
            {"attempt": 2, "status": "failed", "durationSeconds": 0.1, "error": "500"},
            {"attempt": 3, "status": "failed", "durationSeconds": 0.1, "error": "500"},
        ]
        mock_diagnose.side_effect = app.DiagnosisError("All attempts failed", attempts=attempts)

        alert = {"fingerprint": "alert-fp1", "startsAt": "2026-11-01T10:00:00Z"}
        status = app.process_alert(alert)

        self.assertEqual(status, "failed")
        # Result was still saved with failed status and attempts
        result_call = self.mock_s3.put_object.call_args_list[1]
        result_body = json.loads(result_call.kwargs["Body"].decode("utf-8"))
        self.assertEqual(result_body["status"], "failed")
        self.assertEqual(len(result_body["attempts"]), 3)


class TestGracefulShutdownAndActiveClaims(unittest.TestCase):
    def setUp(self):
        app.BUCKET = "test-bucket"
        app.ACTIVE_CLAIMS.clear()
        self.mock_s3 = MagicMock()
        app.s3 = self.mock_s3

    def test_cleanup_active_claims(self):
        with app.CLAIMS_LOCK:
            app.ACTIVE_CLAIMS.add("alerts/2026-11-01/fp1/time1/result.json.claim")
            app.ACTIVE_CLAIMS.add("alerts/2026-11-01/fp2/time2/result.json.claim")

        app.cleanup_active_claims()

        self.assertEqual(len(app.ACTIVE_CLAIMS), 0)
        self.assertEqual(self.mock_s3.delete_object.call_count, 2)


class MockSocket:
    def __init__(self, request_bytes):
        self.rfile = io.BytesIO(request_bytes)
        self.wfile = io.BytesIO()

    def makefile(self, mode, *args, **kwargs):
        if "b" in mode:
            if "r" in mode:
                return self.rfile
            else:
                return self.wfile
        raise ValueError(f"Unsupported mode: {mode}")

    def sendall(self, data):
        self.wfile.write(data)


class TestHTTPHandler(unittest.TestCase):
    def setUp(self):
        app.SHUTDOWN_EVENT.clear()
        while not app.ALERT_QUEUE.empty():
            try:
                app.ALERT_QUEUE.get_nowait()
            except queue.Empty:
                break

    def make_handler(self, request_bytes):
        mock_sock = MockSocket(request_bytes)
        handler = app.Handler(mock_sock, ("127.0.0.1", 12345), None)
        mock_sock.wfile.seek(0)
        return handler, mock_sock.wfile.read()

    def test_healthz_healthy(self):
        req = b"GET /healthz HTTP/1.1\r\nHost: localhost\r\n\r\n"
        _, response = self.make_handler(req)
        self.assertIn(b"200 OK", response)
        self.assertIn(b"ok", response)

    def test_healthz_shutting_down(self):
        app.SHUTDOWN_EVENT.set()
        req = b"GET /healthz HTTP/1.1\r\nHost: localhost\r\n\r\n"
        _, response = self.make_handler(req)
        self.assertIn(b"503", response)
        self.assertIn(b"shutting down", response)

    def test_webhook_immediate_response_and_enqueue(self):
        payload = {
            "alerts": [
                {
                    "status": "firing",
                    "fingerprint": "alert-test-1",
                    "startsAt": "2026-11-01T12:00:00Z",
                    "labels": {"alertname": "HighCPU"},
                },
                {
                    "status": "resolved",
                    "fingerprint": "alert-test-2",
                    "startsAt": "2026-11-01T12:00:00Z",
                },
            ]
        }
        body = json.dumps(payload).encode("utf-8")
        req = (
            b"POST /webhook HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Content-Type: application/json\r\n"
            + f"Content-Length: {len(body)}\r\n\r\n".encode("utf-8")
            + body
        )

        _, response = self.make_handler(req)
        self.assertIn(b"200 OK", response)
        self.assertIn(b'"status": "accepted"', response)
        self.assertIn(b'"queued": 1', response)

        # Confirm firing alert was enqueued
        self.assertEqual(app.ALERT_QUEUE.qsize(), 1)
        queued_alert = app.ALERT_QUEUE.get_nowait()
        self.assertEqual(queued_alert["fingerprint"], "alert-test-1")

    def test_webhook_validation_error(self):
        # Invalid fingerprint (e.g. path traversal)
        payload = {
            "alerts": [
                {
                    "status": "firing",
                    "fingerprint": "../bad/path",
                    "startsAt": "2026-11-01T12:00:00Z",
                }
            ]
        }
        body = json.dumps(payload).encode("utf-8")
        req = (
            b"POST /webhook HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Content-Type: application/json\r\n"
            + f"Content-Length: {len(body)}\r\n\r\n".encode("utf-8")
            + body
        )

        _, response = self.make_handler(req)
        self.assertIn(b"400 Bad Request", response)
        self.assertEqual(app.ALERT_QUEUE.qsize(), 0)

    def test_webhook_shutting_down(self):
        app.SHUTDOWN_EVENT.set()
        body = json.dumps({"alerts": []}).encode("utf-8")
        req = (
            b"POST /webhook HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            + f"Content-Length: {len(body)}\r\n\r\n".encode("utf-8")
            + body
        )

        _, response = self.make_handler(req)
        self.assertIn(b"503", response)


if __name__ == "__main__":
    unittest.main()
