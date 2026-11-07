import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from collect import detect_quota_exhaustion, switch_holmes_api_key


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


class TestSwitchHolmesApiKey(unittest.TestCase):

    def test_missing_paid_key_returns_false(self):
        self.assertFalse(switch_holmes_api_key(""))
        self.assertFalse(switch_holmes_api_key(None))

    @patch("subprocess.run")
    def test_successful_switch_and_rollout(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="success", stderr="")

        res = switch_holmes_api_key("test_paid_key_12345", namespace="monitoring", deployment="holmes-holmes")
        self.assertTrue(res)
        self.assertEqual(mock_run.call_count, 2)

        set_env_call = mock_run.call_args_list[0][0][0]
        self.assertIn("kubectl", set_env_call)
        self.assertIn("set", set_env_call)
        self.assertIn("env", set_env_call)
        self.assertIn("GEMINI_API_KEY=test_paid_key_12345", set_env_call)

        rollout_call = mock_run.call_args_list[1][0][0]
        self.assertIn("rollout", rollout_call)
        self.assertIn("status", rollout_call)

    @patch("subprocess.run")
    def test_set_env_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error: deployment not found")

        res = switch_holmes_api_key("test_paid_key_12345")
        self.assertFalse(res)
        self.assertEqual(mock_run.call_count, 1)

    @patch("subprocess.run")
    def test_rollout_status_failure(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="deployment.apps/holmes-holmes env updated", stderr=""),
            MagicMock(returncode=1, stdout="", stderr="error: timed out waiting for the condition"),
        ]

        res = switch_holmes_api_key("test_paid_key_12345")
        self.assertFalse(res)
        self.assertEqual(mock_run.call_count, 2)


if __name__ == "__main__":
    unittest.main()
