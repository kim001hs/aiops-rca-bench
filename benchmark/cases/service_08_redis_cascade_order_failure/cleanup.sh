#!/usr/bin/env bash
set -euo pipefail

echo "[Cleanup] redis-cart 파드를 1개로 복구하고 cartservice 준비 상태를 확인합니다..."
kubectl scale deployment redis-cart -n onlineboutique --replicas=1

kubectl rollout status deployment/redis-cart -n onlineboutique --timeout=120s
kubectl rollout status deployment/cartservice -n onlineboutique --timeout=120s
echo "[Cleanup] redis-cart 및 cartservice 정상 복구 완료."

