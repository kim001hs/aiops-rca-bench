#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="onlineboutique"
POD=$(kubectl get pods -l app=recommendationservice -n "$NAMESPACE" -o jsonpath='{.items[0].metadata.name}')
if [[ -z "$POD" ]]; then
  echo "❌ recommendationservice pod not found." >&2
  exit 1
fi

BEFORE_RESTARTS=$(kubectl get pod "$POD" -n "$NAMESPACE" -o jsonpath='{.status.containerStatuses[0].restartCount}')
echo "[Inject] Target pod: $POD (restarts=$BEFORE_RESTARTS)"

for attempt in 1 2 3; do
  echo "[Inject] Triggering multi-worker memory spike (20Mi x 22) attempt $attempt/3..."
  kubectl exec "$POD" -n "$NAMESPACE" -- python3 -c "import subprocess, sys, time; w = 'import time; b=bytearray(20*1024*1024); time.sleep(15)'; ps = [subprocess.Popen([sys.executable, '-c', w]) for _ in range(22)]; time.sleep(15)" || true

  for _ in $(seq 1 15); do
    sleep 2
    READY=$(kubectl get pod "$POD" -n "$NAMESPACE" -o jsonpath='{.status.containerStatuses[0].ready}')
    AFTER_RESTARTS=$(kubectl get pod "$POD" -n "$NAMESPACE" -o jsonpath='{.status.containerStatuses[0].restartCount}')
    LAST_REASON=$(kubectl get pod "$POD" -n "$NAMESPACE" -o jsonpath='{.status.containerStatuses[0].lastState.terminated.reason}')
    if [[ "$READY" == "true" ]] && [[ "$AFTER_RESTARTS" -gt "$BEFORE_RESTARTS" ]] && [[ "$LAST_REASON" == "OOMKilled" ]]; then
      echo "[Inject] Self-healed: $POD (ready=$READY, restarts=$BEFORE_RESTARTS->$AFTER_RESTARTS, lastReason=$LAST_REASON)"
      exit 0
    fi
  done
  echo "⚠️ Attempt $attempt/3 did not trigger PID 1 restart; retrying..."
done

echo "❌ Failed to trigger OOMKilled restart after 3 attempts." >&2
kubectl get pod "$POD" -n "$NAMESPACE" -o wide
exit 1
