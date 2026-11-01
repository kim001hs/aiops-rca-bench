# inject.ps1: cartservice 잘못된 Redis 주소 주입 (PowerShell)
$ErrorActionPreference = "Stop"
$namespace = "onlineboutique"

Write-Host "[Inject] cartservice 배포에 잘못된 Redis 주소 환경변수를 주입합니다..." -ForegroundColor Yellow
kubectl set env deployment/cartservice -n $namespace REDIS_ADDR="invalid-redis-host:6379"

Write-Host "   ⏳ 새 환경변수 적용 및 롤링 재배포 대기 중 (15초)..." -ForegroundColor Cyan
Start-Sleep -Seconds 15

Write-Host "[Inject] 현재 cartservice 상태 확인:" -ForegroundColor Yellow
kubectl get pods -l app=cartservice -n $namespace

Write-Host "[Inject] Redis 설정 오류 주입 완료." -ForegroundColor Green

