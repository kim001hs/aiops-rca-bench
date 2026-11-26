$ErrorActionPreference = "Stop"
$namespace = "onlineboutique"

Write-Host "[Inject] recommendationservice 파드를 탐색합니다..." -ForegroundColor Cyan
$pod = (kubectl get pods -l app=recommendationservice -n $namespace -o jsonpath='{.items[0].metadata.name}').Trim()
if (-not $pod) {
    Write-Error "❌ recommendationservice 파드를 찾을 수 없습니다."
    exit 1
}

$beforeRestarts = [int](kubectl get pod $pod -n $namespace -o jsonpath='{.status.containerStatuses[0].restartCount}')
Write-Host "[Inject] 타겟 파드: $pod (현재 재시작 횟수: $beforeRestarts)" -ForegroundColor Green

$recovered = $false
for ($attempt = 1; $attempt -le 3; $attempt++) {
    Write-Host "[Inject] 다중 소형 워커(20Mi x 22) 메모리 폭증 시도 $attempt/3..." -ForegroundColor Yellow
    # 단일 대용량 프로세스는 exec 자식만 죽고 PID 1이 살 수 있으므로, 소형 워커로 분산해 PID 1 OOMKilled를 유도합니다.
    $ErrorActionPreference = "Continue"
    $oomCmd = "import subprocess, sys, time; w = 'import time; b=bytearray(20*1024*1024); time.sleep(15)'; ps = [subprocess.Popen([sys.executable, '-c', w]) for _ in range(22)]; time.sleep(15)"
    kubectl exec $pod -n $namespace -- python3 -c $oomCmd 2>&1 | Out-Null
    $ErrorActionPreference = "Stop"

    Write-Host "   ⏳ Kubernetes Self-Healing(1/1 Running 복구) 대기 중..." -ForegroundColor Cyan
    for ($i = 0; $i -lt 15; $i++) {
        Start-Sleep -Seconds 2
        $ready = ("$(kubectl get pod $pod -n $namespace -o jsonpath='{.status.containerStatuses[0].ready}')").Trim()
        $afterRestartsRaw = ("$(kubectl get pod $pod -n $namespace -o jsonpath='{.status.containerStatuses[0].restartCount}')").Trim()
        $afterRestarts = if ($afterRestartsRaw) { [int]$afterRestartsRaw } else { 0 }
        $lastReason = ("$(kubectl get pod $pod -n $namespace -o jsonpath='{.status.containerStatuses[0].lastState.terminated.reason}')").Trim()
        if ($ready -eq "true" -and $afterRestarts -gt $beforeRestarts -and $lastReason -eq "OOMKilled") {
            $recovered = $true
            Write-Host "[Inject] 자가 복구 완료: $pod (Ready=$ready, Restarts=$beforeRestarts->$afterRestarts, LastReason=$lastReason)" -ForegroundColor Green
            break
        }
    }
    if ($recovered) { break }
    Write-Warning "⚠️ 시도 $attempt/3 에서 PID 1 재시작이 감지되지 않아 재시도합니다."
}

if (-not $recovered) {
    Write-Error "❌ 3회 시도 후에도 recommendationservice 컨테이너 OOMKilled 재시작이 확인되지 않았습니다."
    kubectl get pod $pod -n $namespace -o wide
    exit 1
}
