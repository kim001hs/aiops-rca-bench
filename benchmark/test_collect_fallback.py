import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from collect import detect_quota_exhaustion


class TestDetectQuotaExhaustion(unittest.TestCase):

    def test_empty_or_invalid_result(self):
        self.assertEqual(detect_quota_exhaustion(None), (False, ""))
        self.assertEqual(detect_quota_exhaustion({}), (False, ""))
        self.assertEqual(detect_quota_exhaustion({"error": ""}), (False, ""))

    def test_http_429_in_error_field(self):
        result = {"error": "HTTP Error 429: Too Many Requests: Rate limit exceeded"}
        is_quota, reason = detect_quota_exhaustion(result)
        self.assertTrue(is_quota)
        self.assertEqual(reason, "quota_exhausted_429")

    def test_resource_exhausted_in_attempts(self):
        result = {
            "error": "Holmes diagnosis failed",
            "attempts": [
                {"attempt": 1, "status": "failed", "error": "HTTP Error 500: Internal Server Error"},
                {"attempt": 2, "status": "failed", "error": "details: RESOURCE_EXHAUSTED: Quota exceeded for quota metric"},
            ]
        }
        is_quota, reason = detect_quota_exhaustion(result)
        self.assertTrue(is_quota)
        self.assertEqual(reason, "quota_exhausted_429")

    def test_http_402_payment_required(self):
        result = {"error": "HTTP Error 402: Payment Required"}
        is_quota, reason = detect_quota_exhaustion(result)
        self.assertTrue(is_quota)
        self.assertEqual(reason, "quota_exhausted_402")

    def test_http_503_with_overload_context(self):
        result = {"error": "HTTP Error 503: Service Unavailable: The model is overloaded. Please try again later."}
        is_quota, reason = detect_quota_exhaustion(result, fallback_on_503=True)
        self.assertTrue(is_quota)
        self.assertEqual(reason, "503_model_overloaded")

    def test_http_503_generic_with_fallback_enabled(self):
        result = {"error": "HTTP Error 503: Service Unavailable"}
        is_quota, reason = detect_quota_exhaustion(result, fallback_on_503=True)
        self.assertTrue(is_quota)
        self.assertEqual(reason, "503_service_unavailable")

    def test_http_503_with_fallback_disabled(self):
        result = {"error": "HTTP Error 503: Service Unavailable: The model is overloaded."}
        is_quota, reason = detect_quota_exhaustion(result, fallback_on_503=False)
        self.assertFalse(is_quota)
        self.assertEqual(reason, "")

    def test_unrelated_http_500_or_timeout(self):
        result = {"error": "HTTP Error 500: Internal Server Error: JSON decode error"}
        is_quota, reason = detect_quota_exhaustion(result)
        self.assertFalse(is_quota)
        self.assertEqual(reason, "")

    def test_http_400_bad_request_does_not_trigger(self):
        result = {"error": "HTTP Error 400: Bad Request: Invalid parameter payload"}
        is_quota, reason = detect_quota_exhaustion(result)
        self.assertFalse(is_quota)
        self.assertEqual(reason, "")

    def test_http_401_403_404_do_not_trigger(self):
        for code in ["401 Unauthorized", "403 Forbidden", "404 Not Found"]:
            result = {"error": f"HTTP Error {code}"}
            is_quota, reason = detect_quota_exhaustion(result)
            self.assertFalse(is_quota, f"Should not trigger for {code}")
            self.assertEqual(reason, "")

    def test_number_containing_429_as_substring_does_not_trigger(self):
        result = {"error": "Connection to backend service on port 8429 failed: Connection reset"}
        is_quota, reason = detect_quota_exhaustion(result)
        self.assertFalse(is_quota)
        self.assertEqual(reason, "")


if __name__ == "__main__":
    unittest.main()
