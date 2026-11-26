#!/usr/bin/env bash
set -euo pipefail

echo "[Inject] Patching service/shippingservice targetPort to 50099..."
kubectl patch service shippingservice -n onlineboutique --type='json' \
  -p='[{"op": "replace", "path": "/spec/ports/0/targetPort", "value": 50099}]'

echo "[Inject] Restarting checkoutservice to reset persistent gRPC connection..."
kubectl rollout restart deployment/checkoutservice -n onlineboutique
kubectl rollout status deployment/checkoutservice -n onlineboutique --timeout=90s

kubectl get svc shippingservice -n onlineboutique
kubectl get pods -l app=shippingservice -n onlineboutique
echo "[Inject] shippingservice service port mismatch injected."
