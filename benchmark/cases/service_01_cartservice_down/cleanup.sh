#!/usr/bin/env bash
set -euo pipefail

echo "[Cleanup] cartservice 파드를 1개로 복구합니다..."
kubectl scale deployment cartservice -n onlineboutique --replicas=1

# 파드가 1/1 Running 및 Ready가 될 때까지 대기
kubectl rollout status deployment/cartservice -n onlineboutique --timeout=60s
echo "[Cleanup] cartservice 정상 복구 완료."
