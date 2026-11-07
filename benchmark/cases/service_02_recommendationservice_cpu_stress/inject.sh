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

# 컨테이너 안에서 표준 입출력을 분리한 CPU 부하 프로세스를 기동한다.
# kubectl exec 자체를 호스트에서 백그라운드로 실행하면 종료 여부를 확인할 수 없다.
PY_CODE='import subprocess, sys; worker = "import time\nend = time.monotonic() + 600\nwhile time.monotonic() < end: pass"; processes = [subprocess.Popen([sys.executable, "-c", worker], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True) for _ in range(4)]; print(" ".join(str(p.pid) for p in processes))'
PIDS=$(kubectl exec "${POD}" -n "${NAMESPACE}" -- python3 -c "${PY_CODE}")
echo "   기동된 CPU 부하 프로세스: ${PIDS}"

echo "   ⏳ CPU 부하 상승 대기 중 (5초)..."
sleep 5
CHECK_CODE='import os, sys; [os.kill(int(pid), 0) for pid in sys.argv[1:]]'
kubectl exec "${POD}" -n "${NAMESPACE}" -- python3 -c "${CHECK_CODE}" ${PIDS}

echo "[Inject] recommendationservice CPU 스트레스 주입 완료."
