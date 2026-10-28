Write-Host "[Inject] recommendationservice 파드에 CPU 100% 과부하를 주입합니다..." -ForegroundColor Yellow

$pod = (kubectl get pods -n onlineboutique -l app=recommendationservice -o jsonpath='{.items[0].metadata.name}')

if (-not $pod) {
    Write-Error "recommendationservice 파드를 찾을 수 없습니다."
    exit 1
}

# 파드 내부에서 CPU 집약 멀티프로세스를 백그라운드로 실행
kubectl exec -n onlineboutique $pod -- sh -c 'nohup python3 -c "import multiprocessing as mp; [mp.Process(target=lambda: [x**2 for x in iter(int, 1)]).start() for _ in range(4)]" > /dev/null 2>&1 &'

# CPU 부하가 올라갈 때까지 잠시 대기
Start-Sleep -Seconds 5
Write-Host "[Inject] recommendationservice CPU 부하 주입 완료 ($pod)" -ForegroundColor Green
