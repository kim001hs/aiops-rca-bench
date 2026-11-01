# inject.ps1: cartservice 메모리 제한 축소를 통한 OOMKilled 유발 (PowerShell)
$ErrorActionPreference = "Stop"
$namespace = "onlineboutique"

Write-Host "[Inject] cartservice 배포의 메모리 제한(limit)을 20Mi로 축소하여 OOMKilled를 유발합니다..." -ForegroundColor Yellow
kubectl set resources deployment cartservice -n $namespace --limits=memory=20Mi --requests=memory=20Mi

Write-Host "   ⏳ OOM 발생 및 CrashLoopBackOff 전환 대기 중 (15초)..." -ForegroundColor Cyan
Start-Sleep -Seconds 15

Write-Host "[Inject] 현재 cartservice 상태 확인:" -ForegroundColor Yellow
kubectl get pods -l app=cartservice -n $namespace

Write-Host "[Inject] cartservice Memory OOM 주입 완료." -ForegroundColor Green

