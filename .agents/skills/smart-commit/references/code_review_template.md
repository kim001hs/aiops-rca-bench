# 🤖 AI 코드 리뷰 가이드라인 및 템플릿 (Code Review Guidelines & Template)

본 문서는 에이전트가 PR 생성 또는 업데이트 시 코드 변경사항(diff)을 분석하여 PR 댓글(Comment)에 남길 리뷰 가이드라인과 체크포인트를 정의합니다.

> **원칙:** 코드 리뷰는 정보 제공 및 품질 가이드 목적이며, **위험 경고가 있더라도 PR 생성을 중단하거나 차단하지 않습니다.** (Non-blocking Report)

---

## 1. 리뷰 관점 및 프로젝트 컨텍스트

* **도메인:** Kubernetes 기반 마이크로서비스(Online Boutique), Observability(Prometheus/Loki/Tempo), AIOps(Keep), Chaos Engineering(Chaos Mesh)
* **인프라 & IaC:** AWS EKS v1.36, x86_64(`t3.large`), Terraform 모듈 구조, `fck-nat`
* **언어 & 스택:** HCL(Terraform), YAML(K8s/Helm), Go, Python, C#, Java

---

## 2. 리뷰 우선순위

1. **🔴 치명적 (Critical):**
   * AWS 자격증명, API Secret, 토큰 등 민감 정보 하드코딩
   * 인프라 파괴적 변경 (예: 의도치 않은 데이터 손실, 보안 그룹 0.0.0.0/0 무차별 개방)
   * 빌드 실패 또는 심각한 런타임 크래시를 유발하는 설정 오류
2. **⚠️ 경고 (Warning):**
   * Kubernetes v1.36 지원 중단(Deprecated) API 사용
   * 파드 리소스 Limits/Requests 누락으로 인한 노드 고갈 위험
   * OTel 분산 추적(TraceContext) 전파 단절 또는 메트릭 라벨 폭발(High Cardinality) 패턴
   * EKS 워커 노드 아키텍처(x86_64 vs ARM64) 불일치 위험
3. **💡 제안 (Suggestion):**
   * Terraform 변수 모듈화, 네이밍 일관성, FinOps 비용 최적화 여지
   * 가독성 및 주석 보완 (선택사항)

---

## 3. 핵심 체크포인트

### Terraform & AWS
- [ ] `.tfvars` 파일이나 하드코딩된 AWS 키가 커밋에 포함되지 않았는가?
- [ ] 보안 그룹(Security Group) 인바운드가 불필요하게 전체 개방되지 않았는가?
- [ ] IAM 역할 및 정책이 최소 권한 원칙(Least Privilege)을 따르는가?
- [ ] EKS 버전(`1.36`) 및 인스턴스 타입(`t3.large`, x86_64)이 표준을 유지하는가?

### Kubernetes & Helm
- [ ] 컨테이너 포트, Liveness/Readiness Probe가 정상 설정되었는가?
- [ ] 리소스 Request/Limit이 적절히 정의되어 있는가?
- [ ] Helm 템플릿 문법 및 values 매핑이 일치하는가?

### AIOps & Observability
- [ ] 메트릭 수집 및 라벨링이 적절한가?
- [ ] 로깅 포맷이 분산 추적(Trace ID)과 연계될 수 있는 구조인가?

---

## 4. 리뷰 출력 포맷 (PR 댓글 등록용 서식)

```markdown
## 🤖 AI Code Reviewer Report

> 본 리뷰는 `.github/code_review_template.md` 가이드라인에 따라 자동 분석된 사전 검토 피드백입니다.

### 📊 종합 요약
(1-2문장으로 전체 변경사항과 인프라/애플리케이션 영향 요약)

### 🔴 치명적 (N건)
*(없으면 생략 또는 '해당 없음')*
**파일:라인** - 이슈 제목
- 문제: 설명
- 개선: 코드 예시

### ⚠️ 경고 (N건)
*(없으면 생략 또는 '해당 없음')*
**파일:라인** - 이슈 제목
> 설명

### 💡 개선 제안
- (개선 제안 1-3개, 없으면 생략)
```

