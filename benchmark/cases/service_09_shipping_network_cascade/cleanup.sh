#!/usr/bin/env bash
set -euo pipefail

echo "[Cleanup] Restoring service/shippingservice targetPort to 50051..."
kubectl patch service shippingservice -n onlineboutique --type='json' \
  -p='[{"op": "replace", "path": "/spec/ports/0/targetPort", "value": 50051}]'

echo "[Cleanup] Restarting checkoutservice to restore gRPC connection..."
kubectl rollout restart deployment/checkoutservice -n onlineboutique
kubectl rollout status deployment/checkoutservice -n onlineboutique --timeout=90s

kubectl get svc shippingservice -n onlineboutique
kubectl get endpoints shippingservice -n onlineboutique
echo "[Cleanup] shippingservice service port restored."
