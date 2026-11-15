#!/usr/bin/env bash
set -euo pipefail

echo "[Inject] redis-cart 파드를 0개로 축소하여 다단계 연쇄 장애를 유발합니다..."
kubectl scale deployment redis-cart -n onlineboutique --replicas=0

sleep 5
kubectl get deployment redis-cart -n onlineboutique
echo "[Inject] redis-cart 중단 완료."

