$ErrorActionPreference = "Stop"

Write-Host "[Cleanup] recommendationservice 파드의 재시작 이력 초기화를 위해 롤아웃 재시작을 수행합니다..." -ForegroundColor Cyan
kubectl rollout restart deployment/recommendationservice -n onlineboutique
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

kubectl rollout status deployment/recommendationservice -n onlineboutique --timeout=120s
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "[Cleanup] recommendationservice 초기화 완료." -ForegroundColor Green
