# cleanup.ps1: cartservice 메모리 제한 복구 및 정상화 (PowerShell)
$ErrorActionPreference = "Stop"
$namespace = "onlineboutique"

Write-Host "[Cleanup] cartservice 배포의 리소스 제한을 원래 상태(limits: 128Mi, requests: 64Mi)로 복구합니다..." -ForegroundColor Cyan
kubectl set resources deployment cartservice -n $namespace --limits=cpu=300m,memory=128Mi --requests=cpu=200m,memory=64Mi

Write-Host "   ⏳ cartservice 롤아웃 정상화 대기 중..." -ForegroundColor Cyan
kubectl rollout status deployment/cartservice -n $namespace --timeout=60s

Write-Host "[Cleanup] cartservice 정상 복구 완료." -ForegroundColor Green

