#!/usr/bin/env bash
# cleanup.sh: cartservice 메모리 제한 복구 및 정상화 (Bash)
set -euo pipefail
NAMESPACE="onlineboutique"

echo "[Cleanup] cartservice 배포의 리소스 제한을 원래 상태(limits: 128Mi, requests: 64Mi)로 복구합니다..."
kubectl set resources deployment cartservice -n "${NAMESPACE}" --limits=cpu=300m,memory=128Mi --requests=cpu=200m,memory=64Mi

echo "   ⏳ cartservice 롤아웃 정상화 대기 중..."
kubectl rollout status deployment/cartservice -n "${NAMESPACE}" --timeout=60s

echo "[Cleanup] cartservice 정상 복구 완료."

