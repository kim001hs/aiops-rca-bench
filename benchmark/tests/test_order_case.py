import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from benchmark.lib import order_case
from benchmark.probes import order_probe
from benchmark.lib import collect
import tempfile
import shutil
import uuid


class OrderCaseTests(unittest.TestCase):
    def test_restore_original_delay(self):
        deployment = {"spec": {"template": {"spec": {"containers": [{
            "name": "server", "image": "example/paymentservice-benchmark:tag",
            "env": [{"name": "BENCHMARK_CHARGE_DELAY_MS", "value": "275"}]
        }]}}}}
        with patch.object(order_case, "kubectl", return_value=json.dumps(deployment)) as command, \
             patch.object(order_case, "wait_for_pod_delay"):
            original = order_case.original_delay("onlineboutique")
            order_case.set_delay("onlineboutique", "1000")
            order_case.set_delay("onlineboutique", original)
        self.assertEqual(command.call_args_list[1].args[-1], "BENCHMARK_CHARGE_DELAY_MS=1000")
        self.assertEqual(command.call_args_list[3].args[-1], "BENCHMARK_CHARGE_DELAY_MS=275")

    def test_failed_order_invalidates_run(self):
        with self.assertRaisesRegex(ValueError, "invalid experiment"):
            order_case.require_valid([{"outcome": "failure", "duration_seconds": 1.2}], minimum=1)

    def test_waits_until_old_payment_pod_is_gone(self):
        def pod(delay):
            return {"spec": {"containers": [{"env": [{"name": "BENCHMARK_CHARGE_DELAY_MS", "value": delay}]}]}}
        responses = [json.dumps({"items": [pod("0"), pod("1000")]}),
                     json.dumps({"items": [pod("1000")]})]
        with patch.object(order_case, "kubectl", side_effect=responses) as command, \
             patch.object(order_case.time, "sleep"):
            order_case.wait_for_pod_delay("onlineboutique", "1000")
        self.assertEqual(command.call_count, 2)

    def test_observed_metrics_and_alert_inputs(self):
        with patch.object(order_probe, "place_order", return_value=1.1):
            status, duration = order_probe.probe_once()
        self.assertEqual(status, "success")
        self.assertEqual(duration, 1.1)
        metric = order_probe.metrics()
        self.assertIn('order_probe_duration_seconds_count{outcome="success"} 1', metric)
        self.assertIn('order_probe_duration_seconds_bucket{outcome="success",le="0.9"} 0', metric)

    def test_tempo_usage_only_counts_tempo_tool_calls(self):
        result = {"holmesResponse": {"tool_calls": [{"tool_name": "bash", "result": "tempo-0 Running"}]}}
        self.assertFalse(collect.used_tempo_tool(result))
        result["holmesResponse"]["tool_calls"].append({"tool_name": "tempo_search_traces_by_query"})
        self.assertTrue(collect.used_tempo_tool(result))

    def test_invalid_order_is_restored_and_not_collected(self):
        metadata = {"case_id": "service_05_order_latency", "namespace": "onlineboutique",
                    "expected_alert": {"names": ["OrderResponseSlow"], "service": "frontend", "service_labels": ["service"]}}
        baseline = [{"outcome": "success", "duration_seconds": 0.2}]
        failed = [{"outcome": "failure", "duration_seconds": 1.1}]
        directory = Path(__file__).parent / ("test_data_" + uuid.uuid4().hex)
        directory.mkdir()
        try:
          with patch.object(collect, "ensure_alertmanager_portforward", return_value=(None, "http://localhost:9093")), \
             patch.object(collect, "precheck_cluster", return_value=True), \
             patch.object(collect, "get_active_alerts", return_value=[]), \
             patch.object(order_case, "original_delay", return_value="0"), \
             patch.object(order_case, "order_samples", side_effect=[(baseline, "metric"), (failed, "metric")]), \
             patch.object(order_case, "set_delay") as delay:
            state = collect.collect(metadata, directory, directory / "1", "http://localhost:9093", "bucket", 1, 1)
        finally:
            if directory.resolve().parent == Path(__file__).parent.resolve():
                shutil.rmtree(directory)
        self.assertEqual(state["status"], "invalid_experiment")
        self.assertEqual([call.args[1] for call in delay.call_args_list], ["1000", "0"])


if __name__ == "__main__":
    unittest.main()
