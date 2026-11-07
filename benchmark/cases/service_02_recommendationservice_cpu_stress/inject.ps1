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

# 컨테이너 안에서 표준 입출력을 분리한 CPU 부하 프로세스를 기동한다.
# kubectl 자체를 별도 프로세스로 띄우면 실패 여부를 알 수 없다.
$pyCode = 'import subprocess, sys; worker = "import time\nend = time.monotonic() + 600\nwhile time.monotonic() < end: pass"; processes = [subprocess.Popen([sys.executable, "-c", worker], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True) for _ in range(4)]; print(" ".join(str(p.pid) for p in processes))'
$workerPids = (& kubectl exec $pod -n $namespace -- python3 -c $pyCode).Trim()
if ($LASTEXITCODE -ne 0 -or -not $workerPids) {
    throw "CPU 부하 프로세스 기동 실패"
}
Write-Host "   기동된 CPU 부하 프로세스: $workerPids"

Write-Host "   ⏳ CPU 부하 상승 대기 중 (5초)..."
Start-Sleep -Seconds 5
$checkCode = 'import os, sys; [os.kill(int(pid), 0) for pid in sys.argv[1:]]'
$pidArgs = @($workerPids -split ' ')
& kubectl exec $pod -n $namespace -- python3 -c $checkCode @pidArgs
if ($LASTEXITCODE -ne 0) {
    throw "CPU 부하 프로세스가 5초 이내에 종료됨"
}

Write-Host "[Inject] recommendationservice CPU 스트레스 주입 완료." -ForegroundColor Green
