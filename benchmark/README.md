# AWS 알람 진단 수집

Alertmanager는 `monitoring/alert-receiver.yaml`의 수신 서비스로 firing 웹훅을 보냅니다. 수신 서비스가 알람마다 Holmes `/api/chat`을 호출하고 한국어 지침을 `additional_system_prompt`로 전달합니다. 원본 알람, Holmes 응답, 시각 및 상태는 `s3://<bucket>/alerts/<UTC date>/<fingerprint>/<UTC startsAt>/result.json`에 보존됩니다. 웹훅 재전송은 같은 키의 `.claim` 객체로 중복 진단을 막습니다. 실패한 진단도 `status: failed`로 저장됩니다. 클러스터와 별도인 bootstrap Terraform 버킷은 공개 접근을 차단하고 자동 삭제를 막습니다.


## 배포

1. 기존 `terraform/bootstrap` 상태를 적용해 영구 버킷, `alert-receiver` 및 Redis 상태 확인 기능이 포함된 `cartservice` ECR 저장소와 SSM Parameter Store 항목을 만듭니다. 새 상태로 다시 적용하면 다른 버킷이 생길 수 있습니다.
2. `terraform_apply.yaml`을 실행합니다. 워크플로우는 SSM에서 버킷과 저장소 주소를 읽고, 두 이미지를 빌드해 ECR에 올립니다. Helm은 새 `cartservice` 이미지를 사용하고 dev Terraform은 수신 서비스의 IRSA 역할을 만든 뒤 수신 서비스를 배포합니다. `terraform_destroy.yaml`은 dev 클러스터와 역할만 제거하며 bootstrap 버킷은 유지합니다.

로컬에서 직접 적용할 때도 `TF_VAR_alert_results_bucket`, `TF_VAR_alert_receiver_image`를 지정해야 합니다. 수신 서비스 역할은 `alerts/*`의 `s3:PutObject`와 결과 저장 실패 시 `.claim`만 해제하는 `s3:DeleteObject`를 허용합니다. 로컬 러너는 별도 AWS 읽기 자격증명(`s3:GetObject`)이 필요합니다.

## 수집과 채점

```sh
python benchmark/runner.py --case service_02_recommendationservice_cpu_stress --version v1 --bucket <bucket>
python benchmark/runner.py --case service_02_recommendationservice_cpu_stress --version v2 --draft --bucket <bucket>
python benchmark/evaluate.py --version v1 --run all
python benchmark/evaluate.py --version v2 --run all --draft
```

러너는 주입 전 활성 알람을 기록하고, 메타데이터의 알람 종류와 서비스에 맞는 새로운 알람만 선택합니다. 같은 우선순위의 후보가 둘 이상이면 `ambiguous`, 없으면 `missing`으로 기록하고 복구합니다. S3 결과가 늦으면 `--result-timeout`까지 기다립니다. 결과는 확정 `results/<case>/<version>/<run>/`, 초안 `results/draft/<case>/<version>/<run>/`에 저장합니다. `run_info.json`, `result.json`, `report.md`가 수집 산출물입니다. 채점은 `evaluate.py`만 수행하며, 동일 보고서와 채점 모델의 기존 점수는 재사용합니다.

로컬 `run_info.json`의 `alert_fingerprint`, `alert_starts_at`, `s3_key`, `s3_uri`로 S3 원본을 찾을 수 있습니다. 새 `report.md`와 채점 후 `evaluation.md`에도 알람 fingerprint와 S3 원본 주소를 표시합니다. 같은 fingerprint가 다른 시각에 다시 발생할 수 있으므로 전체 S3 경로로 회차를 구분합니다. AWS Holmes는 응답 JSON만 만들며, `report.md`는 로컬 수집기가 응답에서 생성하고 `evaluation.md`는 `evaluate.py`가 생성합니다. `benchmark/live-kubeconfig`는 실환경 EKS 접근을 위해 만든 임시 로컬 kubeconfig이며 Git에서 제외됩니다.

S3 콘솔에서 `run_info.json`의 `s3_key` 경로를 따라가면 원본 `result.json`을 열 수 있습니다. 이전 로컬 러너가 직접 만든 `rca_raw.json`·`report.md`만 있는 회차에는 S3 원본이 없습니다.

새 `report.md`에는 Holmes 응답의 `metadata.costs`에 기록된 진단 전체 토큰과 USD 비용을 표시합니다. 새 `evaluation.md`에는 LiteLLM 채점 응답의 토큰과 모델 요금표로 계산한 USD 추정 비용을 표시합니다. 채점 응답의 사용량·비용은 `evaluation.json`에도 저장됩니다. 과거 채점 응답에서 사용량을 저장하지 않았다면 재호출 없이 비용을 복원할 수 없어 `기록 없음`으로 표시합니다.

Holmes가 Gemini 크레딧 부족 등으로 실패하면 S3에는 `status: failed`가 남고 로컬 수집에는 점수를 만들지 않습니다. 같은 `fingerprint`와 `startsAt`은 재전송해도 다시 진단하지 않으므로, 외부 원인을 해결한 뒤 새 알람 발생 시각으로 확인해야 합니다. S3 결과 쓰기가 실패하면 `.claim`을 해제하고 웹훅에 오류를 반환해 재전송 시 진단을 다시 시도합니다.

4번 Redis 주소 오류 실험은 `cartservice`의 Redis 접근 상태를 gRPC 헬스 체크로 확인합니다. 주소가 잘못되면 Pod 준비 상태가 실패하고 `ServicePodNotHealthy` 알람이 발생합니다. 해당 동작에는 워크플로우가 빌드한 `cartservice` 이미지가 필요합니다.

## 실환경 확인

러너를 실행하지 않고 테스트용 firing 알람이 생기게 한 뒤, 수신 서비스 로그와 S3 `result.json`을 확인합니다. 같은 웹훅을 다시 보내도 Holmes 호출과 결과 객체가 늘지 않는지 확인합니다. 이후 `destroy → apply`에서 버킷 원본이 남고 새 수신 서비스가 알람을 받는지 확인합니다. 이 검증은 실제 AWS/클러스터에서 수행해야 합니다.
