import importlib.util
import io
import json
import sys
import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent))
import collect
import evaluate

spec = importlib.util.spec_from_file_location("alert_receiver", Path(__file__).resolve().parents[1] / "alert-receiver" / "app.py")
receiver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(receiver)


def alert(name, service="cartservice", fingerprint="abc", started="2026-11-01T00:00:00Z"):
    return {"status": {"state": "active"}, "fingerprint": fingerprint, "startsAt": started,
            "labels": {"namespace": "onlineboutique", "alertname": name, "pod": service + "-xyz"}}


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.meta = {"namespace": "onlineboutique", "expected_alert": {
            "names": ["ServiceContainerOOMKilled", "ServicePodNotHealthy"],
            "service": "cartservice", "service_labels": ["pod"]}}

    def test_old_and_unrelated_alerts_are_excluded(self):
        old = alert("ServiceContainerOOMKilled")
        unrelated = alert("ServiceContainerOOMKilled", "recommendationservice", "other")
        new = alert("ServiceContainerOOMKilled", fingerprint="new")
        selected, state = collect.choose_alert([old, unrelated, new], {collect.alert_identity(old)}, self.meta)
        self.assertEqual((selected, state), (new, "selected"))

    def test_priority_and_ambiguity(self):
        generic = alert("ServicePodNotHealthy", fingerprint="generic")
        oom = alert("ServiceContainerOOMKilled", fingerprint="oom")
        self.assertEqual(collect.choose_alert([generic, oom], set(), self.meta)[0], oom)
        self.assertEqual(collect.choose_alert([oom, alert("ServiceContainerOOMKilled", fingerprint="oom2")], set(), self.meta)[1], "ambiguous")

    def test_waits_for_specific_alert_after_generic_health_alert(self):
        generic = alert("ServicePodNotHealthy", fingerprint="generic")
        oom = alert("ServiceContainerOOMKilled", fingerprint="oom")
        with patch.object(collect, "get_active_alerts", side_effect=[[generic], [generic, oom]]), patch.object(collect.time, "sleep"):
            selected, state, _ = collect.wait_for_alert("http://example", set(), self.meta, 5)
        self.assertEqual((selected, state), (oom, "selected"))

    def test_alert_poll_reconnects_after_tunnel_reset(self):
        oom = alert("ServiceContainerOOMKilled", fingerprint="oom")
        with patch.object(collect, "get_active_alerts", side_effect=[ConnectionResetError(), [oom]]), \
             patch.object(collect.time, "sleep"), patch.object(collect, "ensure_alertmanager_portforward") as reconnect:
            selected, state, _ = collect.wait_for_alert("http://example", set(), self.meta, 5, reconnect=reconnect)
        self.assertEqual((selected, state), (oom, "selected"))
        reconnect.assert_called_once()

    def test_s3_identity_and_delay(self):
        candidate = alert("ServiceContainerOOMKilled")
        s3 = MagicMock()
        from botocore.exceptions import ClientError
        s3.get_object.side_effect = [ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject"),
                                     {"Body": io.BytesIO(json.dumps({"fingerprint": "abc", "startsAt": candidate["startsAt"], "status": "succeeded"}).encode())}]
        with patch.object(collect.time, "sleep"):
            result, key = collect.wait_for_result(s3, "bucket", candidate, 5)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(key, receiver.object_key(candidate))

    def test_alertmanager_timestamp_precision_is_normalized(self):
        webhook = alert("ServiceContainerOOMKilled", started="2026-11-01T05:46:51.706776Z")
        api = alert("ServiceContainerOOMKilled", started="2026-11-01T05:46:51.706Z")
        self.assertEqual(receiver.object_key(webhook), collect.s3_key(api))
        self.assertEqual(collect.alert_identity(webhook), collect.alert_identity(api))

    def test_webhook_redelivery_does_not_diagnose_twice(self):
        candidate = alert("ServiceContainerOOMKilled")
        candidate["status"] = "firing"
        from botocore.exceptions import ClientError
        with patch.object(receiver, "s3") as s3, patch.object(receiver, "diagnose", return_value={"analysis": "원인"}) as diagnose:
            s3.put_object.side_effect = [None, None,
                                         ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")]
            self.assertEqual(receiver.process_alert(candidate), "succeeded")
            self.assertEqual(receiver.process_alert(candidate), "duplicate")
            diagnose.assert_called_once()

    def test_diagnosis_failure_is_persisted(self):
        candidate = alert("ServiceContainerOOMKilled")
        with patch.object(receiver, "s3") as s3, patch.object(receiver, "diagnose", side_effect=TimeoutError("Holmes timeout")):
            self.assertEqual(receiver.process_alert(candidate), "failed")
            body = json.loads(s3.put_object.call_args_list[1].kwargs["Body"])
            self.assertEqual(body["status"], "failed")

    def test_result_write_failure_releases_claim_for_webhook_retry(self):
        candidate = alert("ServiceContainerOOMKilled")
        from botocore.exceptions import ClientError
        write_error = ClientError({"Error": {"Code": "InternalError"}}, "PutObject")
        with patch.object(receiver, "s3") as s3, patch.object(receiver, "diagnose", return_value={"analysis": "원인"}):
            s3.put_object.side_effect = [None, write_error]
            with self.assertRaises(ClientError):
                receiver.process_alert(candidate)
            s3.delete_object.assert_called_once_with(
                Bucket=receiver.BUCKET, Key=receiver.object_key(candidate) + ".claim")

    def test_batch_evaluation_selects_version_and_draft_without_rejudging(self):
        base = Path(__file__).parent / ("test_data_" + uuid.uuid4().hex)
        base.mkdir()
        try:
            cases = base / "cases" / "case"
            cases.mkdir(parents=True)
            (cases / "metadata.json").write_text(json.dumps({"title": "Test"}))
            for category, version, content in (("", "v1", "confirmed v1"), ("", "v2", "confirmed v2"),
                                               ("draft", "v1", "draft v1")):
                path = base / "results" / category / "case" / version / "1"
                path.mkdir(parents=True)
                (path / "report.md").write_text(content)
                (path / "run_info.json").write_text('{"status":"collected"}')
            result = {"root_cause_score": 30, "evidence_score": 30, "remediation_score": 20,
                      "factual_accuracy_score": 20, "total_score": 100, "verdict": "PASS", "judge_feedback": "okay"}
            with patch.object(evaluate, "run_single_judge", return_value=result) as judge:
                evaluate.evaluate_version("v1", base / "cases", base / "results", "test", target_run="all")
                evaluate.evaluate_version("v1", base / "cases", base / "results", "test", target_run="all")
                evaluate.evaluate_version("v1", base / "cases", base / "results", "test", target_run="all", draft=True)
                self.assertEqual(judge.call_count, 2)
                self.assertEqual(judge.call_args_list[0].args[0], "confirmed v1")
                self.assertEqual(judge.call_args_list[1].args[0], "draft v1")
            self.assertFalse((base / "results" / "case" / "v2" / "1" / "evaluation.json").exists())
        finally:
            if base.resolve().parent == Path(__file__).parent.resolve():
                shutil.rmtree(base)

    def test_ambiguous_alert_still_restores_fault(self):
        base = Path(__file__).parent / ("test_data_" + uuid.uuid4().hex)
        base.mkdir()
        try:
            metadata = {"case_id": "case", "namespace": "onlineboutique", "expected_alert": self.meta["expected_alert"]}
            with (patch.object(collect, "ensure_alertmanager_portforward", return_value=(None, "http://localhost:9093")),
                  patch.object(collect, "precheck_cluster", return_value=True),
                  patch.object(collect, "get_active_alerts", return_value=[]),
                  patch.object(collect, "inject_fault", return_value=True),
                  patch.object(collect, "wait_for_alert", return_value=(None, "ambiguous", 2.0)),
                  patch.object(collect, "cleanup_fault", return_value=True) as cleanup):
                state = collect.collect(metadata, base, base / "1", "http://localhost:9093", "bucket", 5, 5)
            self.assertEqual(state["status"], "ambiguous")
            cleanup.assert_called_once()
            self.assertFalse((base / "1" / "report.md").exists())
        finally:
            if base.resolve().parent == Path(__file__).parent.resolve():
                shutil.rmtree(base)

    def test_failed_aws_diagnosis_is_collected_without_scoring_and_fault_is_restored(self):
        base = Path(__file__).parent / ("test_data_" + uuid.uuid4().hex)
        base.mkdir()
        try:
            metadata = {"case_id": "case", "namespace": "onlineboutique", "title": "Test",
                        "expected_alert": self.meta["expected_alert"]}
            selected = alert("ServiceContainerOOMKilled")
            result = {"fingerprint": selected["fingerprint"], "startsAt": selected["startsAt"],
                      "status": "failed", "error": "Holmes unavailable"}
            with (patch.object(collect, "ensure_alertmanager_portforward", return_value=(None, "http://localhost:9093")),
                  patch.object(collect, "precheck_cluster", return_value=True),
                  patch.object(collect, "get_active_alerts", return_value=[]),
                  patch.object(collect, "inject_fault", return_value=True),
                  patch.object(collect, "wait_for_alert", return_value=(selected, "selected", 2.0)),
                  patch.object(collect, "wait_for_result", return_value=(result, "alerts/key/result.json")),
                  patch.object(collect, "cleanup_fault", return_value=True) as cleanup):
                state = collect.collect(metadata, base, base / "1", "http://localhost:9093", "bucket", 5, 5)
            self.assertEqual(state["status"], "diagnosis_failed")
            self.assertTrue((base / "1" / "result.json").exists())
            self.assertFalse((base / "1" / "report.md").exists())
            self.assertFalse((base / "1" / "evaluation.json").exists())
            cleanup.assert_called_once()
        finally:
            if base.resolve().parent == Path(__file__).parent.resolve():
                shutil.rmtree(base)

    def test_successful_collection_records_s3_path_in_local_files(self):
        base = Path(__file__).parent / ("test_data_" + uuid.uuid4().hex)
        base.mkdir()
        try:
            metadata = {"case_id": "case", "namespace": "onlineboutique", "title": "Test",
                        "expected_alert": self.meta["expected_alert"]}
            selected = alert("ServiceContainerOOMKilled")
            result = {"fingerprint": selected["fingerprint"], "startsAt": selected["startsAt"],
                      "status": "succeeded", "holmesResponse": {"analysis": "진단 결과"}}
            with (patch.object(collect, "ensure_alertmanager_portforward", return_value=(None, "http://localhost:9093")),
                  patch.object(collect, "precheck_cluster", return_value=True),
                  patch.object(collect, "get_active_alerts", return_value=[]),
                  patch.object(collect, "inject_fault", return_value=True),
                  patch.object(collect, "wait_for_alert", return_value=(selected, "selected", 2.0)),
                  patch.object(collect, "wait_for_result", return_value=(result, collect.s3_key(selected))),
                  patch.object(collect, "cleanup_fault", return_value=True)):
                state = collect.collect(metadata, base, base / "1", "http://localhost:9093", "bucket", 5, 5)
            self.assertEqual(state["status"], "collected")
            self.assertEqual(state["alert_fingerprint"], selected["fingerprint"])
            self.assertIn(collect.s3_key(selected), (base / "1" / "report.md").read_text(encoding="utf-8"))
            self.assertEqual(json.loads((base / "1" / "run_info.json").read_text(encoding="utf-8"))["s3_key"],
                             collect.s3_key(selected))
            self.assertFalse((base / "1" / "evaluation.md").exists())
        finally:
            if base.resolve().parent == Path(__file__).parent.resolve():
                shutil.rmtree(base)

    def test_runner_classifies_versions_and_drafts_locally(self):
        base = Path(__file__).parent / ("test_data_" + uuid.uuid4().hex)
        case = base / "cases" / "case"
        case.mkdir(parents=True)
        (case / "metadata.json").write_text(json.dumps({"case_id": "case", "namespace": "onlineboutique"}))
        try:
            with patch.object(collect, "__file__", str(base / "collect.py")), \
                 patch.object(collect, "collect", return_value={"status": "collected"}) as run:
                with patch.object(sys, "argv", ["runner.py", "--case", "case", "--version", "v1", "--bucket", "bucket"]):
                    self.assertEqual(collect.main(), 0)
                self.assertEqual(run.call_args.args[2], base / "results" / "case" / "v1" / "1")
                with patch.object(sys, "argv", ["runner.py", "--case", "case", "--version", "v2", "--draft", "--bucket", "bucket"]):
                    self.assertEqual(collect.main(), 0)
                self.assertEqual(run.call_args.args[2], base / "results" / "draft" / "case" / "v2" / "1")
        finally:
            if base.resolve().parent == Path(__file__).parent.resolve():
                shutil.rmtree(base)


if __name__ == "__main__":
    unittest.main()
