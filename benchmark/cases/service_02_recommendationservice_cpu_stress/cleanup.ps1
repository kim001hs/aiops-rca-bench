Write-Host "[Cleanup] recommendationservice 파드를 롤아웃 재시작하여 정상화합니다..." -ForegroundColor Cyan
kubectl rollout restart deployment recommendationservice -n onlineboutique

# 파드가 새로 생성되어 1/1 Running 및 Ready가 될 때까지 대기
kubectl rollout status deployment recommendationservice -n onlineboutique --timeout=60s
Write-Host "[Cleanup] recommendationservice 정상 복구 완료." -ForegroundColor Green

