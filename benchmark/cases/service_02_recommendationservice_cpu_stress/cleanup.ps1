# cleanup.ps1: recommendationservice CPU 스트레스 해제 및 정상화 (PowerShell)
$ErrorActionPreference = "Stop"
$namespace = "onlineboutique"

Write-Host "[Cleanup] recommendationservice 파드를 정상화합니다 (롤아웃 재시작)..." -ForegroundColor Cyan
kubectl rollout restart deployment/recommendationservice -n $namespace

Write-Host "   ⏳ recommendationservice 롤아웃 완료 대기 중..."
kubectl rollout status deployment/recommendationservice -n $namespace --timeout=60s

Write-Host "[Cleanup] recommendationservice 정상 복구 완료." -ForegroundColor Green
