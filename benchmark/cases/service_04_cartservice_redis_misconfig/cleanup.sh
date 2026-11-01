#!/usr/bin/env bash
# cleanup.sh: cartservice Redis 주소 정상 복구 (Bash)
set -euo pipefail
NAMESPACE="onlineboutique"

echo "[Cleanup] cartservice Redis 환경변수를 원래 주소(redis-cart:6379)로 복구합니다..."
kubectl set env deployment/cartservice -n "${NAMESPACE}" REDIS_ADDR="redis-cart:6379"

echo "   ⏳ cartservice 롤아웃 정상화 대기 중..."
kubectl rollout status deployment/cartservice -n "${NAMESPACE}" --timeout=60s

echo "[Cleanup] 현재 cartservice 상태 확인:"
kubectl get pods -l app=cartservice -n "${NAMESPACE}"

echo "[Cleanup] Redis 설정 정상 복구 완료."
