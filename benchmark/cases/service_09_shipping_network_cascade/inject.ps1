$ErrorActionPreference = "Stop"

Write-Host "[Inject] service/shippingservice 의 targetPort를 50099로 변경하여 무상태 gRPC 연쇄 장애를 유발합니다..." -ForegroundColor Yellow
kubectl patch service shippingservice -n onlineboutique --type='json' -p='[{"op": "replace", "path": "/spec/ports/0/targetPort", "value": 50099}]'
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "[Inject] checkoutservice 기존 gRPC 커넥션 풀 재설정을 위해 롤아웃 재시작합니다..." -ForegroundColor Cyan
kubectl rollout restart deployment/checkoutservice -n onlineboutique
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
kubectl rollout status deployment/checkoutservice -n onlineboutique --timeout=90s
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

kubectl get svc shippingservice -n onlineboutique
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
kubectl get pods -l app=shippingservice -n onlineboutique
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "[Inject] shippingservice 서비스 포트 단절 주입 완료 (파드 1/1 Running 유지)." -ForegroundColor Green
