#!/usr/bin/env bash
set -euo pipefail

echo "[Inject] cartservice 파드를 0개로 축소하여 장애를 유발합니다..."
kubectl scale deployment cartservice -n onlineboutique --replicas=0

# 파드가 종료될 때까지 잠시 대기
sleep 5
kubectl get deployment cartservice -n onlineboutique
echo "[Inject] cartservice 중단 완료."
