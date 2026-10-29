#!/usr/bin/env bash
# cleanup.sh: recommendationservice CPU 스트레스 해제 및 정상화 (Bash)
set -euo pipefail
NAMESPACE="onlineboutique"

echo "[Cleanup] recommendationservice 파드를 정상화합니다 (롤아웃 재시작)..."
kubectl rollout restart deployment/recommendationservice -n "${NAMESPACE}"

echo "   ⏳ recommendationservice 롤아웃 완료 대기 중..."
kubectl rollout status deployment/recommendationservice -n "${NAMESPACE}" --timeout=60s

echo "[Cleanup] recommendationservice 정상 복구 완료."
