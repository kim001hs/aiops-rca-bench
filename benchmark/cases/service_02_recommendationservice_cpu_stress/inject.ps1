# inject.ps1: recommendationservice CPU 과부하 주입 (PowerShell)
$ErrorActionPreference = "Stop"
$namespace = "onlineboutique"

Write-Host "[Inject] recommendationservice 파드를 탐색합니다..." -ForegroundColor Cyan
$pod = kubectl get pods -l app=recommendationservice -n $namespace -o jsonpath="{.items[0].metadata.name}"

if (-not $pod) {
    Write-Error "❌ recommendationservice 파드를 찾을 수 없습니다."
    exit 1
}

Write-Host "[Inject] 타겟 파드: $pod" -ForegroundColor Green
Write-Host "[Inject] recommendationservice 파드에 CPU 100% 스트레스 프로세스를 주입합니다..." -ForegroundColor Yellow

# Python 무한 루프 연산 프로세스 백그라운드 실행 (readOnlyRootFilesystem 제약 무관)
$pyCode = "import multiprocessing, time; [multiprocessing.Process(target=lambda: [None for _ in iter(int, 1)]).start() for _ in range(4)]; time.sleep(600)"
Start-Process -FilePath "kubectl" -ArgumentList "exec $pod -n $namespace -- python3 -c `"$pyCode`"" -WindowStyle Hidden

Write-Host "   ⏳ CPU 부하 상승 대기 중 (5초)..."
Start-Sleep -Seconds 5

Write-Host "[Inject] recommendationservice CPU 스트레스 주입 완료." -ForegroundColor Green
