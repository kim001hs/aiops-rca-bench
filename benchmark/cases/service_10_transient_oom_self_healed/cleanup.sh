#!/usr/bin/env bash
set -euo pipefail

echo "[Cleanup] Resetting recommendationservice pod state via rollout restart..."
kubectl rollout restart deployment/recommendationservice -n onlineboutique
kubectl rollout status deployment/recommendationservice -n onlineboutique --timeout=120s
echo "[Cleanup] recommendationservice reset complete."
