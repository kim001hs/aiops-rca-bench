# cleanup.ps1: cartservice Redis 주소 정상 복구 (PowerShell)
$ErrorActionPreference = "Stop"
$namespace = "onlineboutique"

Write-Host "[Cleanup] cartservice Redis 환경변수를 원래 주소(redis-cart:6379)로 복구합니다..." -ForegroundColor Cyan
kubectl set env deployment/cartservice -n $namespace REDIS_ADDR="redis-cart:6379"

Write-Host "   ⏳ cartservice 롤아웃 정상화 대기 중..." -ForegroundColor Cyan
kubectl rollout status deployment/cartservice -n $namespace --timeout=60s

Write-Host "[Cleanup] 현재 cartservice 상태 확인:" -ForegroundColor Yellow
kubectl get pods -l app=cartservice -n $namespace

Write-Host "[Cleanup] Redis 설정 정상 복구 완료." -ForegroundColor Green

