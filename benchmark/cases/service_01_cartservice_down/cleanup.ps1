Write-Host "[Cleanup] cartservice 파드를 1개로 복구합니다..." -ForegroundColor Cyan
kubectl scale deployment cartservice -n onlineboutique --replicas=1

# 파드가 1/1 Running 및 Ready가 될 때까지 대기
kubectl rollout status deployment/cartservice -n onlineboutique --timeout=60s
Write-Host "[Cleanup] cartservice 정상 복구 완료." -ForegroundColor Green
