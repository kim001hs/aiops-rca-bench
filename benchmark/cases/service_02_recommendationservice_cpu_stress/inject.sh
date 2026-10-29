#!/usr/bin/env bash
# inject.sh: recommendationservice CPU 과부하 주입 (Bash)
set -euo pipefail
NAMESPACE="onlineboutique"

echo "[Inject] recommendationservice 파드를 탐색합니다..."
POD=$(kubectl get pods -l app=recommendationservice -n "${NAMESPACE}" -o jsonpath="{.items[0].metadata.name}")

if [ -z "${POD}" ]; then
  echo "❌ recommendationservice 파드를 찾을 수 없습니다." >&2
  exit 1
fi

echo "[Inject] 타겟 파드: ${POD}"
echo "[Inject] recommendationservice 파드에 CPU 100% 스트레스 프로세스를 주입합니다..."

PY_CODE="import multiprocessing, time; [multiprocessing.Process(target=lambda: [None for _ in iter(int, 1)]).start() for _ in range(4)]; time.sleep(600)"
kubectl exec "${POD}" -n "${NAMESPACE}" -- python3 -c "${PY_CODE}" >/dev/null 2>&1 &

echo "   ⏳ CPU 부하 상승 대기 중 (5초)..."
sleep 5

echo "[Inject] recommendationservice CPU 스트레스 주입 완료."
