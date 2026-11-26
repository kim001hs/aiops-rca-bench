$ErrorActionPreference = "Stop"

Write-Host "[Cleanup] service/shippingservice 의 targetPort를 50051로 복구합니다..." -ForegroundColor Cyan
kubectl patch service shippingservice -n onlineboutique --type='json' -p='[{"op": "replace", "path": "/spec/ports/0/targetPort", "value": 50051}]'
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "[Cleanup] checkoutservice gRPC 커넥션 복구를 위해 롤아웃 재시작합니다..." -ForegroundColor Cyan
kubectl rollout restart deployment/checkoutservice -n onlineboutique
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
kubectl rollout status deployment/checkoutservice -n onlineboutique --timeout=90s
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

kubectl get svc shippingservice -n onlineboutique
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
kubectl get endpoints shippingservice -n onlineboutique
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "[Cleanup] shippingservice 서비스 포트 정상 복구 완료." -ForegroundColor Green
