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

# Linux 컨테이너 환경에서 Python 3.14 호환 os.fork 기반 고부하 프로세스 4개 기동 (Pickle 에러 방지)
PY_CODE='import os, time; [os.fork() for _ in range(2)]; end = time.time() + 600; exec("while time.time() < end: pass")'
kubectl exec "${POD}" -n "${NAMESPACE}" -- python3 -c "${PY_CODE}" >/dev/null 2>&1 &

echo "   ⏳ CPU 부하 상승 대기 중 (5초)..."
sleep 5

echo "[Inject] recommendationservice CPU 스트레스 주입 완료."
