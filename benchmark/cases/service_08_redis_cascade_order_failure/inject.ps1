$ErrorActionPreference = "Stop"

Write-Host "[Inject] redis-cart 파드를 0개로 축소하여 다단계 연쇄 장애를 유발합니다..." -ForegroundColor Yellow
kubectl scale deployment redis-cart -n onlineboutique --replicas=0
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Start-Sleep -Seconds 5
kubectl get deployment redis-cart -n onlineboutique
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "[Inject] redis-cart 중단 완료." -ForegroundColor Green

