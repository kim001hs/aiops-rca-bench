"""Kubernetes controls and observations for the order latency experiment."""
import json
import socket
import subprocess
import time
import urllib.parse
import urllib.request
from contextlib import contextmanager


def kubectl(*args):
    result = subprocess.run(["kubectl", *args], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"kubectl {' '.join(args)}: {result.stderr.strip()}")
    return result.stdout


@contextmanager
def forward(service, namespace, remote_port):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    proc = subprocess.Popen(["kubectl", "port-forward", f"svc/{service}", f"{port}:{remote_port}",
                             "-n", namespace], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(30):
            if proc.poll() is not None:
                raise RuntimeError(f"port-forward for {service} exited")
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1):
                    break
            except OSError:
                time.sleep(0.2)
        else:
            raise TimeoutError(f"port-forward for {service} timed out")
        yield f"http://127.0.0.1:{port}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()


def order_samples(n=3):
    with forward("order-probe", "monitoring", 8080) as base:
        samples = []
        for _ in range(n):
            with urllib.request.urlopen(base + "/probe", timeout=20) as response:
                samples.append(json.load(response))
        with urllib.request.urlopen(base + "/metrics", timeout=5) as response:
            metrics = response.read().decode()
    return samples, metrics


def original_delay(namespace, deployment="paymentservice", env_name="BENCHMARK_CHARGE_DELAY_MS",
                   require_image="paymentservice-benchmark"):
    dep = json.loads(kubectl("get", "deployment", deployment, "-n", namespace, "-o", "json"))
    container = next(c for c in dep["spec"]["template"]["spec"]["containers"] if c["name"] == "server")
    if require_image and require_image not in container["image"]:
        raise RuntimeError(f"benchmark {deployment} image ({require_image}) is required")
    return next((e.get("value") for e in container.get("env", []) if e["name"] == env_name), None)


def set_delay(namespace, value, deployment="paymentservice", env_name="BENCHMARK_CHARGE_DELAY_MS"):
    assignment = f"{env_name}-" if value is None else f"{env_name}={value}"
    kubectl("set", "env", f"deployment/{deployment}", "-n", namespace, assignment)
    kubectl("rollout", "status", f"deployment/{deployment}", "-n", namespace, "--timeout=180s")
    wait_for_pod_delay(namespace, value, deployment=deployment, env_name=env_name)


def wait_for_pod_delay(namespace, value, timeout=60, deployment="paymentservice",
                       env_name="BENCHMARK_CHARGE_DELAY_MS"):
    """Rollout status can finish while an old pod still receives traffic."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pods = json.loads(kubectl("get", "pods", "-n", namespace, "-l", f"app={deployment}", "-o", "json"))["items"]
        if pods and all(next((env.get("value") for c in pod["spec"]["containers"]
                              for env in c.get("env", []) if env["name"] == env_name), None) == value
                        for pod in pods):
            return
        time.sleep(2)
    raise TimeoutError(f"{deployment} old pod still present after delay rollout")


def wait_for_order_ready(timeout=30, interval=2.0):
    """Wait until one order probe succeeds after a deployment rollout."""
    deadline = time.monotonic() + timeout
    with forward("order-probe", "monitoring", 8080) as base:
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(base + "/probe", timeout=20) as response:
                    sample = json.load(response)
                if sample.get("outcome") == "success":
                    return sample
            except (OSError, ValueError):
                pass
            time.sleep(interval)
    raise TimeoutError("order probe did not stabilize after delay rollout")


def require_valid(samples, minimum=None, maximum=None):
    if any(s["outcome"] != "success" for s in samples):
        raise ValueError("invalid experiment: at least one order failed")
    durations = [s["duration_seconds"] for s in samples]
    if minimum is not None and min(durations) < minimum:
        raise ValueError(f"invalid experiment: observed checkout under {minimum}s")
    if maximum is not None and max(durations) >= maximum:
        raise ValueError(f"invalid experiment: baseline checkout at or above {maximum}s")


def tempo_search(start, end, target_service="paymentservice", expected_span="PaymentService/Charge"):
    """Preserve raw Tempo search results for the run's alert window."""
    with forward_tempo() as base:
        query = urllib.parse.urlencode({"q": f'{{ resource.service.name = "{target_service}" }}',
                                        "start": int(start), "end": int(end), "limit": 50})
        with urllib.request.urlopen(f"{base}/api/search?{query}", timeout=10) as response:
            found = json.load(response)
        linked = []
        for trace in found.get("traces", [])[:50]:
            trace_id = trace.get("traceID")
            if not trace_id:
                continue
            with urllib.request.urlopen(f"{base}/api/traces/{trace_id}", timeout=10) as response:
                detail = json.load(response)
            services = set()
            matched_span = False
            for batch in detail.get("batches", []):
                services.add((batch.get("process") or {}).get("serviceName"))
                for scope in batch.get("scopeSpans", []):
                    matched_span |= any(expected_span in span.get("name", "") for span in scope.get("spans", []))
            for batch in detail.get("batches", []) + detail.get("resourceSpans", []):
                for attribute in (batch.get("resource") or {}).get("attributes", []):
                    if attribute.get("key") == "service.name":
                        services.add((attribute.get("value") or {}).get("stringValue"))
                for scope in batch.get("scopeSpans", []):
                    matched_span |= any(expected_span in span.get("name", "") for span in scope.get("spans", []))
            required_services = {"frontend", "checkoutservice", target_service}
            if required_services <= services and matched_span:
                linked.append(trace_id)
        found["linked_order_trace_ids"] = linked
        return found


def prometheus_query(expression):
    with forward_http("kube-prometheus-stack-prometheus", "monitoring", 9090, "/-/ready") as base:
        query = urllib.parse.urlencode({"query": expression})
        with urllib.request.urlopen(f"{base}/api/v1/query?{query}", timeout=10) as response:
            data = json.load(response)
    if data.get("status") != "success":
        raise RuntimeError(f"Prometheus query failed: {data}")
    return data["data"]["result"]


@contextmanager
def forward_tempo():
    with forward_http("tempo", "monitoring", 3200, "/ready") as base:
        yield base


@contextmanager
def forward_http(service, namespace, remote_port, ready_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    proc = subprocess.Popen(["kubectl", "port-forward", f"svc/{service}", f"{port}:{remote_port}", "-n", namespace],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(30):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}{ready_path}", timeout=1):
                    break
            except OSError:
                time.sleep(0.2)
        else:
            raise TimeoutError(f"{service} port-forward timed out")
        yield f"http://127.0.0.1:{port}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
