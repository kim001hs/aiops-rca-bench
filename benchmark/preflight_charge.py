"""One reversible live delay check before collecting an alert diagnosis."""
import json
import time

import order_case


def main():
    namespace = "onlineboutique"
    original = order_case.original_delay(namespace)
    try:
        order_case.set_delay(namespace, "1000")
        deployment = json.loads(order_case.kubectl("get", "deployment", "paymentservice", "-n", namespace, "-o", "json"))
        pods = json.loads(order_case.kubectl("get", "pods", "-n", namespace, "-l", "app=paymentservice", "-o", "json"))
        print(json.dumps({"deployment_generation": deployment["metadata"]["generation"],
                          "observed_generation": deployment["status"].get("observedGeneration"),
                          "pods": [{"name": p["metadata"]["name"],
                                    "delay": next((e.get("value") for e in p["spec"]["containers"][0].get("env", [])
                                                   if e["name"] == "BENCHMARK_CHARGE_DELAY_MS"), None),
                                    "ready": p["status"].get("phase")} for p in pods["items"]]}, indent=2))
        time.sleep(5)
        samples, _ = order_case.order_samples()
        print(json.dumps(samples, indent=2))
    finally:
        order_case.set_delay(namespace, original)


if __name__ == "__main__":
    main()
