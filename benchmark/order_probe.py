"""Exercise a real Online Boutique order and expose observed checkout latency."""
import http.cookiejar
import json
import os
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

FRONTEND = os.environ.get("FRONTEND_URL", "http://frontend.onlineboutique.svc.cluster.local")
INTERVAL = float(os.environ.get("ORDER_PROBE_INTERVAL", "5"))
BUCKETS = (0.25, 0.5, 0.9, 1.5, 2.5, 5, 10)
lock = threading.Lock()
count = {"success": 0, "failure": 0}
duration_sum = {"success": 0.0, "failure": 0.0}
histogram = {status: {bucket: 0 for bucket in BUCKETS} for status in count}
last = {"success": 0.0, "failure": 0.0}


def place_order(base=FRONTEND):
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    def post(path, fields):
        body = urllib.parse.urlencode(fields).encode()
        with opener.open(urllib.request.Request(base + path, data=body), timeout=15) as response:
            return response.status, response.read().decode("utf-8", errors="replace")

    status, _ = post("/cart", {"product_id": "OLJCESPC7Z", "quantity": "1"})
    if status != 200:
        raise RuntimeError(f"cart request returned {status}")
    started = time.monotonic()
    status, body = post("/cart/checkout", {
        "email": "benchmark@example.com", "street_address": "1 Benchmark St",
        "zip_code": "12345", "city": "Seoul", "state": "CA", "country": "US",
        "credit_card_number": "4111111111111111", "credit_card_expiration_month": "12",
        "credit_card_expiration_year": str(time.gmtime().tm_year + 2), "credit_card_cvv": "123",
    })
    elapsed = time.monotonic() - started
    if status != 200 or "Your order is complete!" not in body:
        raise RuntimeError(f"checkout did not complete (HTTP {status})")
    return elapsed


def probe_once():
    started = time.monotonic()
    try:
        duration = place_order()
        status = "success"
    except Exception as exc:
        duration = time.monotonic() - started
        status = "failure"
        print(f"order probe failed: {exc}", flush=True)
    with lock:
        count[status] += 1
        duration_sum[status] += duration
        last[status] = time.time()
        for bucket in BUCKETS:
            if duration <= bucket:
                histogram[status][bucket] += 1
    return status, duration


def metrics():
    lines = ["# HELP order_probe_duration_seconds Real frontend checkout response time.",
             "# TYPE order_probe_duration_seconds histogram"]
    with lock:
        for status in count:
            for bucket in BUCKETS:
                lines.append(f'order_probe_duration_seconds_bucket{{outcome="{status}",le="{bucket}"}} {histogram[status][bucket]}')
            lines.append(f'order_probe_duration_seconds_bucket{{outcome="{status}",le="+Inf"}} {count[status]}')
            lines.append(f'order_probe_duration_seconds_count{{outcome="{status}"}} {count[status]}')
            lines.append(f'order_probe_duration_seconds_sum{{outcome="{status}"}} {duration_sum[status]}')
            lines.append(f'order_probe_last_timestamp_seconds{{outcome="{status}"}} {last[status]}')
    return "\n".join(lines) + "\n"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ("/metrics", "/healthz", "/probe"):
            self.send_error(404)
            return
        if self.path == "/probe":
            outcome, duration = probe_once()
            body = json.dumps({"outcome": outcome, "duration_seconds": duration}).encode()
        else:
            body = (metrics() if self.path == "/metrics" else "ok\n").encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json" if self.path == "/probe" else "text/plain; version=0.0.4")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    def loop():
        while True:
            probe_once()
            time.sleep(INTERVAL)
    threading.Thread(target=loop, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
