#!/usr/bin/env bash
set -euo pipefail
NAMESPACE="onlineboutique"

echo "[Inject] cartservice 배포에 잘못된 Redis 주소 환경변수를 주입합니다..."
kubectl set env deployment/cartservice -n "${NAMESPACE}" REDIS_ADDR="invalid-redis-host:6379"

echo "   ⏳ 새 환경변수 적용 및 롤링 재배포 대기 중 (15초)..."
sleep 15

echo "[Inject] 현재 cartservice 상태 확인:"
kubectl get pods -l app=cartservice -n "${NAMESPACE}"

echo "[Inject] Redis 설정 오류 주입 완료."