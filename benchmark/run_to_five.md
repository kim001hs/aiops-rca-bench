# 벤치마크 케이스별 v1·v2 결과 5회 채우기

저장소 루트에서 PowerShell로 실행한다. 아래 횟수는 `benchmark/results/<case>/<version>/<run>/run_info.json`의 `status`가 `collected`인 회차를 기준으로 2026-11-02 service_05 v2 5회차 재실행 성공 후 계산했다. 실행 전 4절에서 현재 횟수를 다시 확인한다. 실패한 회차 폴더도 번호를 차지하므로 최종 회차 번호가 5보다 클 수 있다.

| 케이스 | v1 수집 완료 | v1 추가 | v2 수집 완료 | v2 추가 |
| --- | ---: | ---: | ---: | ---: |
| `service_01_cartservice_down` | 5 | 0 | 5 | 0 |
| `service_02_recommendationservice_cpu_stress` | 0 | 5 | 5 | 0 |
| `service_03_cartservice_memory_oom` | 5 | 0 | 5 | 0 |
| `service_04_cartservice_redis_misconfig` | 5 | 0 | 5 | 0 |
| `service_05_order_latency` | 5 | 0 | 5 | 0 |

`service_02`의 v1 1회차와 v2 1·2회차는 CPU 알람이 발생하지 않아 `missing`이었다. 수정된 CPU 주입 스크립트로 실행한 v2 3회차는 `collected`다. `missing` 회차는 성공 횟수에 포함하지 않는다.

`service_05` v2 3회차는 첫 Holmes 시도에서 HTTP 500을 받은 뒤 재시도해 총 477.8초 만에 S3 결과가 도착했다. 기본 S3 대기 시간 360초를 넘어 러너가 `s3_timeout`으로 끝났지만, 뒤늦게 저장된 성공 결과와 Tempo trace를 같은 회차에 복구했다. 이 회차는 `collected`에 포함한다. 남은 주문 지연 실험에는 `-ResultTimeout 900`을 사용한다.

v2 4회차도 `collected`다. 처음 실행한 5회차는 Holmes Pod가 2026-11-02 15:26:22 KST에 512Mi 메모리 한도로 `OOMKilled`된 직후 진단 요청 3회가 모두 `Connection refused`로 실패했다. 이 실패 폴더를 삭제하고 같은 5번으로 다시 실행한 결과는 `collected`다. 현재 v2 1~5회차가 수집 완료됐다.

## 1. 클러스터 접속, 버킷, 반복 함수 준비

2026-11-02에 기본 kubeconfig가 이전 EKS API 주소를 가리켜 Helm 접속이 실패했다. 갱신된 `benchmark/live-kubeconfig`를 현재 PowerShell 창에서 사용한다. 클러스터를 다시 생성한 경우 첫 번째 `aws eks update-kubeconfig` 명령으로 파일을 갱신한다.

버킷 이름은 `msa-demo-alert-results-ssouyy`다. S3의 `alerts/2026-11-01/...`는 버킷 내부 객체 경로이므로 `--bucket`에는 붙이지 않는다. 아래 코드 블록 전체를 저장소 루트에서 **현재 사용 중인 PowerShell 창에 먼저 붙여 넣어 실행**한다. `Invoke-BenchmarkRuns`는 이 PowerShell 창에서만 사용할 임시 함수이며, Python 파일에 추가하거나 별도 파일로 저장할 필요가 없다. 새 PowerShell 창을 열면 kubeconfig 환경 변수와 함수를 다시 설정해야 한다. `runner.py`의 현재 실행 진입점은 `collect.main()`이므로 아래 명령은 AWS 알람 진단 수집 흐름을 실행한다. `--run`을 생략하면 기존 숫자 폴더의 다음 번호가 자동으로 붙는다.

```powershell
aws eks update-kubeconfig --region ap-northeast-2 --name msa-demo-dev-eks --kubeconfig benchmark/live-kubeconfig
if ($LASTEXITCODE -ne 0) { throw "EKS kubeconfig 갱신 실패" }
$env:KUBECONFIG = (Resolve-Path benchmark/live-kubeconfig).Path
kubectl get namespace monitoring -o name
if ($LASTEXITCODE -ne 0) { throw "EKS 접속 실패" }

$env:ALERT_RESULTS_BUCKET = "msa-demo-alert-results-ssouyy"

function Invoke-BenchmarkRuns {
    param([string]$Case, [string]$Version, [int]$Times, [int]$ResultTimeout = 360)

    foreach ($i in 1..$Times) {
        python benchmark/runner.py --case $Case -v $Version --bucket $env:ALERT_RESULTS_BUCKET --result-timeout $ResultTimeout
        if ($LASTEXITCODE -ne 0) {
            throw "$Case $Version 추가 실행 $i/$Times 실패. 해당 회차의 run_info.json을 확인하세요."
        }
    }
}
```

## 2. 기본군 v1: Holmes Tempo 도구 비활성화 후 실행

```powershell
helm upgrade holmes robusta/holmes --version 0.45.0 --namespace monitoring --values monitoring/holmes-values.yaml --values monitoring/holmes-tempo-disabled.yaml --wait --timeout 5m
if ($LASTEXITCODE -ne 0) { throw "Holmes Tempo 비활성화 실패" }

Invoke-BenchmarkRuns service_02_recommendationservice_cpu_stress v1 5
```

## 3. Tempo군 v2: Holmes Tempo 도구 활성화 후 실행

```powershell
helm upgrade holmes robusta/holmes --version 0.45.0 --namespace monitoring --values monitoring/holmes-values.yaml --values monitoring/holmes-tempo-enabled.yaml --wait --timeout 5m
if ($LASTEXITCODE -ne 0) { throw "Holmes Tempo 활성화 실패" }

# service_05_order_latency v2 1~5회차 수집 완료. 추가 실행 불필요.
```

`-v`는 결과 버전을 지정한다. Tempo 도구의 활성화는 앞의 Helm 설정이 결정한다. 두 실험군 모두 `--draft` 없이 확정 결과 폴더에 저장한다.

## 4. 완료 횟수 확인

```powershell
Get-ChildItem benchmark/results -Directory | Where-Object Name -like 'service_*' | ForEach-Object {
    $caseName = $_.Name
    foreach ($version in @('v1', 'v2')) {
        $versionPath = Join-Path $_.FullName $version
        $successful = @(
            Get-ChildItem $versionPath -Directory -ErrorAction SilentlyContinue |
                Where-Object Name -match '^\d+$' |
                Where-Object {
                    $info = Join-Path $_.FullName 'run_info.json'
                    (Test-Path $info) -and ((Get-Content $info -Raw | ConvertFrom-Json).status -eq 'collected')
                }
        ).Count
        [pscustomobject]@{ Case = $caseName; Version = $version; Collected = $successful }
    }
} | Format-Table -AutoSize
```

각 행의 `Collected`가 5이면 완료다. 실행이 중간에 멈췄다면 위 표로 현재 횟수를 확인하고, 부족한 횟수만 `Invoke-BenchmarkRuns`로 다시 실행한다. 현재 수집 흐름은 채점을 별도로 수행한다.
