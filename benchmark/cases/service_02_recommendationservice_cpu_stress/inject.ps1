# inject.ps1: recommendationservice CPU 과부하 주입 (PowerShell)
$ErrorActionPreference = "Stop"
$namespace = "onlineboutique"

Write-Host "[Inject] recommendationservice 파드를 탐색합니다..." -ForegroundColor Cyan
$pod = (kubectl get pods -l app=recommendationservice -n $namespace -o jsonpath='{.items[0].metadata.name}')

if (-not $pod) {
    Write-Error "❌ recommendationservice 파드를 찾을 수 없습니다."
    exit 1
}

Write-Host "[Inject] 타겟 파드: $pod" -ForegroundColor Green
Write-Host "[Inject] recommendationservice 파드에 CPU 100% 스트레스 프로세스를 주입합니다..." -ForegroundColor Yellow

# Linux 컨테이너 환경에서 Python 3.14 호환 os.fork 기반 고부하 프로세스 4개 기동 (Pickle 에러 방지)
$pyCode = 'import os, time; [os.fork() for _ in range(2)]; end = time.time() + 600; exec("while time.time() < end: pass")'
Start-Process -FilePath "kubectl" -ArgumentList @("exec", $pod, "-n", $namespace, "--", "python3", "-c", $pyCode) -WindowStyle Hidden

Write-Host "   ⏳ CPU 부하 상승 대기 중 (5초)..."
Start-Sleep -Seconds 5

Write-Host "[Inject] recommendationservice CPU 스트레스 주입 완료." -ForegroundColor Green
