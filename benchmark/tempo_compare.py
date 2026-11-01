"""Alternate Holmes' Tempo access while keeping the tracing stack running."""
import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASE = "service_05_order_latency"


def run(*args):
    subprocess.run(args, check=True)


def set_arm(enabled):
    values = ROOT / "monitoring" / ("holmes-tempo-enabled.yaml" if enabled else "holmes-tempo-disabled.yaml")
    run("helm", "upgrade", "holmes", "robusta/holmes", "--version", "0.45.0",
        "--namespace", "monitoring", "--values", str(ROOT / "monitoring" / "holmes-values.yaml"),
        "--values", str(values), "--wait", "--timeout", "5m")
    current = json.loads(subprocess.check_output(
        ["helm", "get", "values", "holmes", "-n", "monitoring", "-o", "json"], text=True))
    actual = current.get("toolsets", {}).get("grafana/tempo", {}).get("enabled")
    if actual is not enabled:
        raise RuntimeError(f"Holmes Tempo toolset arm mismatch: expected {enabled}, got {actual}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pairs", type=int, default=3)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--first", choices=("baseline", "tempo"), default="baseline")
    args = parser.parse_args()
    if args.pairs < 1:
        parser.error("--pairs must be positive")
    arms = [args.first, "tempo" if args.first == "baseline" else "baseline"]
    for pair in range(args.pairs):
        for arm in arms:
            set_arm(arm == "tempo")
            run(sys.executable, str(ROOT / "benchmark" / "collect.py"),
                "--case", CASE, "--version", arm, "--bucket", args.bucket)
            print(f"pair {pair + 1}: {arm} collected", flush=True)
    print("Collection complete. Run evaluate.py for baseline and tempo with the same judge model.")


if __name__ == "__main__":
    main()
