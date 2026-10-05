Write-Host "[Inject] cartservice 파드를 0개로 축소하여 장애를 유발합니다..." -ForegroundColor Yellow
kubectl scale deployment cartservice -n onlineboutique --replicas=0

# 파드가 종료될 때까지 잠시 대기
Start-Sleep -Seconds 5
kubectl get deployment cartservice -n onlineboutique
Write-Host "[Inject] cartservice 중단 완료." -ForegroundColor Green
