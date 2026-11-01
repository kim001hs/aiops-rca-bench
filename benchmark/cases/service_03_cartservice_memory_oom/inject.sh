#!/usr/bin/env bash
# inject.sh: cartservice 메모리 제한 축소를 통한 OOMKilled 유발 (Bash)
set -euo pipefail
NAMESPACE="onlineboutique"

echo "[Inject] cartservice 배포의 메모리 제한(limit)을 20Mi로 축소하여 OOMKilled를 유발합니다..."
kubectl set resources deployment cartservice -n "${NAMESPACE}" --limits=memory=20Mi --requests=memory=20Mi

echo "   ⏳ OOM 발생 및 CrashLoopBackOff 전환 대기 중 (15초)..."
sleep 15

echo "[Inject] 현재 cartservice 상태 확인:"
kubectl get pods -l app=cartservice -n "${NAMESPACE}"

echo "[Inject] cartservice Memory OOM 주입 완료."

