---
description: (Fixed) 변경사항 분석, 커밋, 푸시 후 PR 상태를 확인하여 생성하거나 최신화합니다. GH CLI 자동 경로 설정 포함.
---

> **참고:** 브랜치명, PR 제목, 커밋 컨벤션은 아래의 6대 접두사 규격을 엄격히 준수합니다:
> 1. `feat/` (`feat:`): 새로운 기능 구현
> 2. `fix/` (`fix:`): 버그 수정
> 3. `refactor/` (`refactor:`): 코드 구조 개선 (로직 불변)
> 4. `test/` (`test:`): 단위/통합 테스트 추가 및 코드 커버리지 확보 작업
> 5. `docs/` (`docs:`): 문서 작성 및 수정 (README, 명세서 등)
> 6. `chore/` (`chore:`): 빌드/환경 설정, 라이브러리 추가, .gitignore 등
> **언어:** 모든 결과 보고 및 PR 본문/댓글은 **한글**로 작성합니다.

// turbo-all

---

## 0. GitHub CLI 환경 설정 및 인증 확인 (필수!)

**GH CLI 설정**:

```bash
# GH CLI 경로 설정
if ! command -v gh &> /dev/null; then
    if [ -f "/opt/homebrew/bin/gh" ]; then
        export PATH="/opt/homebrew/bin:$PATH"
    elif [ -f "/usr/local/bin/gh" ]; then
        export PATH="/usr/local/bin:$PATH"
    fi
fi

# GH CLI 확인
if ! command -v gh &> /dev/null; then
    echo "❌ Error: 'gh' command not found. Please install GitHub CLI."
    exit 1
fi

# Auth Status 확인
if ! gh auth status &> /dev/null; then
    echo "❌ Error: GitHub CLI is not authenticated. Please run 'gh auth login'."
    exit 1
fi
```

---

## 0-1. 브랜치 전략 및 명명 규칙 준수 확인 (필수!)

**⚠️ 직접 push 금지 브랜치**: `main` (GitHub Flow 전략: 작업 브랜치에서 PR을 통해 main에 병합)

```bash
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
```

### 📌 브랜치 명명 규칙 (Branch Naming Convention)
모든 작업 브랜치는 반드시 다음 6가지 접두사 중 목적에 맞는 것을 사용해야 합니다:
1. `feat/<기능명>` : 새로운 기능 구현
2. `fix/<버그설명>` : 버그 수정
3. `refactor/<개선내용>` : 코드 구조 개선 (로직 불변)
4. `test/<테스트내용>` : 단위/통합 테스트 추가 및 코드 커버리지 확보 작업
5. `docs/<문서내용>` : 문서 작성 및 수정 (README, 명세서 등)
6. `chore/<작업내용>` : 빌드/환경 설정, 라이브러리 추가, .gitignore 등

| 현재 브랜치                                                               | push 가능? | 조치                                                                                               |
| -------------------------------------------------------------------------- | ---------- | -------------------------------------------------------------------------------------------------- |
| `main`                                                                     | ❌ 금지    | "main에 직접 push할 수 없습니다. 작업 브랜치(`feat/*` 등)를 생성하세요." 안내 후 **중단**            |
| `feat/*`, `fix/*`, `refactor/*`, `test/*`, `docs/*`, `chore/*`             | ✅ 허용    | 계속 진행                                                                                          |
| 기타 브랜치명                                                              | ⚠️ 주의    | 규격 브랜치(`feat/*`, `fix/*` 등)로 새 브랜치를 생성하여 작업 권장                                   |

**main에 있는 경우 → 새 브랜치 생성 제안**:

```bash
# 목적에 맞는 브랜치 생성 (예시)
git checkout -b feat/<기능명>
git checkout -b fix/<버그설명>
git checkout -b refactor/<개선내용>
git checkout -b test/<테스트내용>
git checkout -b docs/<문서내용>
git checkout -b chore/<작업내용>
```

---

## 1. 현재 상태 및 변경사항 분석 (주제별 분리 필수!)

```bash
git status -s
```

변경된 파일들의 도메인/주제(Scope)를 분석하여 **단일 주제**인지 **다중 주제**인지 판단합니다:
- 🏗️ **인프라 / IaC:** `terraform/**`, `docs/infra-project/**`
- ⚙️ **CI/CD 및 워크플로우:** `.github/**`
- 📦 **애플리케이션 및 쿠버네티스:** `src/**`, `helm-chart/**`, `kubernetes-manifests/**`
- 📝 **문서 및 기타:** `README.md`, `project.md`, `docs/**`

### 🔀 분기 판단:
1. **단일 주제인 경우:**
   - 현재 브랜치에서 변경사항을 원자적으로 커밋하고 푸시하여 **단일 PR**을 생성합니다. (아래 **2단계** 진행)
2. **다중 주제(예: 인프라 수정 + CI 템플릿 개편 등 성격이 다른 변경이 공존)인 경우:**
   - ⚠️ **단순히 커밋만 나누는 것으로는 부족하며, 반드시 주제별로 별도의 독립 브랜치를 생성하여 각각 독립된 PR로 분리해야 합니다!**
   - 아래 **"1-A. 다중 주제 브랜치 및 다중 PR 분할 워크플로우"**에 따라 작업을 분리하여 수행합니다.

---

## 1-A. 다중 주제 브랜치 및 다중 PR 분할 워크플로우 (Multi-Topic Branch & PR Split)

성격이 다른 변경사항들이 섞여 있는 경우, 아래 절차에 따라 각 주제별로 브랜치를 파서 독립적인 PR을 순차적으로 생성합니다:

> **핵심 원리:** `main` 브랜치 기준으로 첫 번째 기능 브랜치를 파서 해당 파일만 커밋/PR을 올리고, 다시 `main` 기준으로 두 번째 기능 브랜치를 파서 나머지 파일들을 커밋/PR로 올립니다.

### [실행 절차 예시] 인프라(Topic A)와 CI/템플릿(Topic B)이 섞여 있는 경우:

#### 1단계: 첫 번째 주제 (Topic A: Infra) 브랜치 및 PR 생성
```bash
# 1. main 기준으로 첫 번째 기능 브랜치 생성 (워킹 트리의 변경사항은 그대로 보존됨)
git checkout -b feat/infra-eks-s3-migration main

# 2. Topic A에 해당하는 파일들만 선택적으로 스테이징
git add terraform/ docs/infra-project/

# 3. Topic A 원자적 커밋 및 푸시
git commit -m "feat(infra): EKS v1.36 다운그레이드 및 S3 원격 백엔드 마이그레이션"
git push -u origin feat/infra-eks-s3-migration

# 4. Topic A에 대한 독립 PR 생성 및 AI 리뷰 댓글 등록
# (4-A 단계의 Step 2~Step 4와 동일하게 수행)
```

#### 2단계: 두 번째 주제 (Topic B: CI/CD) 브랜치 및 PR 생성
```bash
# 1. 다시 main 기준으로 두 번째 기능 브랜치 생성 (남아있는 Topic B 변경사항 보존됨)
git checkout -b chore/ci-templates-redesign main

# 2. Topic B에 해당하는 파일들 스테이징
git add .github/

# 3. Topic B 원자적 커밋 및 푸시
git commit -m "chore(ci): GitHub PR/코드리뷰 템플릿 일원화 및 smart-commit 개편"
git push -u origin chore/ci-templates-redesign

# 4. Topic B에 대한 독립 PR 생성 및 AI 리뷰 댓글 등록
# (4-A 단계의 Step 2~Step 4와 동일하게 수행)
```

#### 3단계: 다중 PR 최종 보고
- 생성된 각 브랜치의 PR 링크(PR #1, PR #2)를 일목요연하게 보고합니다.

---

## 2. 조건부 커밋 및 푸시 (단일 주제인 경우)

**단일 주제 변경사항이 있는 경우에만 실행**:

> [!IMPORTANT]
> **원자적 커밋(Atomic Commits)**: 변경사항이 여러 기능이나 서로 다른 수정 사항을 포함하고 있다면, `git add -p` 등을 사용하여 **기능별로 커밋을 나누어** 진행하십시오. 한 번에 모든 변경사항을 하나의 커밋으로 묶지 마십시오.

```bash
# 기능별로 나누어 스테이징 및 커밋 (필요시 반복)
# git add <file_functional_group>
# git commit -m "<type>(<scope>): <설명>"

# Conventional Commit 메시지 생성 (diff 분석 기반)
# Hook 실행을 위해 --no-verify 제거 (Lint/Type Check 수행)
git commit -m "<type>: <설명>"

# 원격에 푸시
# Hook 실행을 위해 --no-verify 제거 (Type Check 수행)
git push origin $CURRENT_BRANCH
```

> [!IMPORTANT]
> **PR 브랜치에 push한 경우 AI 코드 리뷰 댓글 등록은 필수입니다.** 기존 리뷰 댓글이 있더라도 최신 HEAD 전체 diff를 다시 분석하여 새 리뷰 댓글을 남겨야 하며, 리뷰 결과가 `치명적 0건`이어도 생략할 수 없습니다.

---

## 3. Target Branch 결정
- 본 프로젝트는 단일 프로비저닝 환경(EKS dev)에 맞춘 **GitHub Flow**를 적용하여 모든 PR의 기본 타겟 브랜치를 **`main`**으로 설정합니다.

```bash
TARGET_BRANCH="main"
```

---

## 4. PR 존재 여부 확인 (필수!)

```bash
PR_URL=$(gh pr view --json url,state --jq 'select(.state == "OPEN") | .url' 2>/dev/null || echo "")
```

| 결과     | 상태                                  |
| -------- | ------------------------------------- |
| URL 있음 | PR이 이미 존재 → **4-B로** (업데이트) |
| 비어있음 | PR 없음 → **4-A로** (생성)            |

---

## 4-A. PR 신규 생성 (PR이 없는 경우)

**반드시 실행해야 하는 단계**:

### Step 1: 원격과의 차이 확인

```bash
git fetch origin $TARGET_BRANCH
COMMITS=$(git log origin/$TARGET_BRANCH..$CURRENT_BRANCH --oneline)
```

- 커밋이 없으면: "base 브랜치 대비 새로운 커밋이 없습니다." 보고 후 종료

### Step 2: PR 본문 및 AI 코드 리뷰 작성 (템플릿 기반 분리 구조)

1. **PR 본문 작성:** 반드시 **`.github/pull_request_template.md`** 파일의 양식을 읽고 참조하여 `.pr_body_temp.md`를 작성합니다:
   - 📌 배경 및 목적 (Background)
   - 📋 주요 변경 사항 (Change Summary)
   - 📁 변경된 파일 (Changed Files)
   - 🧪 검증 절차 및 결과 (Testing Procedure)
   - 📝 추가 참고사항 (Additional Notes)
   - 🔀 Merge 가이드 (Target: `<TARGET_BRANCH>`, Squash and Merge 권장)
2. **AI 코드 리뷰 작성:** 반드시 **`.github/code_review_template.md`** 파일의 체크포인트(Terraform, K8s v1.36, 보안, OTel)와 3단계 우선순위(🔴치명적 / ⚠️경고 / 💡제안)를 확인합니다.
   - `git diff origin/$TARGET_BRANCH...$CURRENT_BRANCH`를 분석하여 `code_review_template.md`의 **"리뷰 출력 포맷"**에 맞추어 `.pr_review_temp.md`에 작성합니다.
   - > **⚠️ 주의:** 코드 리뷰는 PR 검토자의 이해를 돕기 위한 사전 보고서이며, 경고나 제안이 있더라도 PR 생성을 중단하거나 차단하지 않습니다.

### Step 3: Issue 자동 생성 및 연결 (현재 레포 기준)

PR과 연결할 이슈를 확인하거나, 없으면 현재 저장소에 새로 생성하여 연결합니다.

#### 📌 PR 제목 명명 규칙 (PR Title Convention)
PR 제목은 반드시 다음 6가지 규격 중 목적에 맞는 접두사를 사용해야 합니다:
1. `feat: <내용>` (또는 `feat/ : <내용>`) : 새로운 기능 구현
2. `fix: <내용>` (또는 `fix/ : <내용>`) : 버그 수정
3. `refactor: <내용>` (또는 `refactor/ : <내용>`) : 코드 구조 개선 (로직 불변)
4. `test: <내용>` (또는 `test/ : <내용>`) : 단위/통합 테스트 추가 및 코드 커버리지 확보 작업
5. `docs: <내용>` (또는 `docs/ : <내용>`) : 문서 작성 및 수정 (README, 명세서 등)
6. `chore: <내용>` (또는 `chore/ : <내용>`) : 빌드/환경 설정, 라이브러리 추가, .gitignore 등

```bash
# 1. PR 제목 정의 (위 6대 규칙 준수)
# 예: PR_TITLE="feat: 카오스 엔지니어링 장애 주입 모듈 구현"
#     PR_TITLE="fix: Redis 연결 풀 고갈 버그 수정"
#     PR_TITLE="docs: 인시던트 진단 런북 명세서 추가"
PR_TITLE="<prefix>: <종합된 변경 제목>"

# 2. 브랜치 이름에서 이슈 번호 감지 (예: feat/12-foo -> 12)
DETECTED_ISSUE_NUM=$(echo "$CURRENT_BRANCH" | grep -oE '/[0-9]+(-|$)' | tr -d '/-')

EXISTING_ISSUE_FOUND=false

if [ -n "$DETECTED_ISSUE_NUM" ]; then
  # 현재 저장소에 해당 이슈가 존재하는지 확인
  if gh issue view "$DETECTED_ISSUE_NUM" > /dev/null 2>&1; then
    echo "✅ 기존 이슈 #$DETECTED_ISSUE_NUM 확인됨. 해당 이슈에 연결합니다."
    ISSUE_NUM="$DETECTED_ISSUE_NUM"
    EXISTING_ISSUE_FOUND=true
  fi
fi

# 3. 기존 이슈가 없으면 현재 저장소에 새 이슈 자동 생성
if [ "$EXISTING_ISSUE_FOUND" = false ]; then
  echo "🆕 현재 저장소에 새 이슈를 생성합니다..."
  ISSUE_URL=$(gh issue create \
    --title "$PR_TITLE" \
    --body-file .pr_body_temp.md \
    --assignee "@me")

  ISSUE_NUM=${ISSUE_URL##*/}
  echo "✅ 이슈 #$ISSUE_NUM 생성 완료."
fi

# 4. PR 본문 하단에 자동 Close 키워드 추가
echo -e "\n\nCloses #$ISSUE_NUM" >> .pr_body_temp.md
```

### Step 4: PR 생성 및 AI 코드 리뷰 코멘트 분리 등록

```bash
# 1. PR 생성 (.github/pull_request_template.md 기반 본문)
PR_URL=$(gh pr create \
  --title "$PR_TITLE" \
  --body-file .pr_body_temp.md \
  --base $TARGET_BRANCH)

echo "✅ PR 생성 완료: $PR_URL"

# 2. PR 생성 직후 별도 댓글(Comment)로 AI 코드 리뷰 필수 등록
test -s .pr_review_temp.md || {
  echo "❌ Error: AI 코드 리뷰 파일이 없거나 비어 있습니다."
  exit 1
}

gh pr comment "$PR_URL" --body-file .pr_review_temp.md
echo "🤖 AI 코드 리뷰 코멘트 등록 완료."
```

### Step 5: 임시 파일 정리

```bash
rm -f .pr_body_temp.md .pr_review_temp.md
```

---

## 4-B. 기존 PR 업데이트 (PR이 있는 경우)

### Step 1: 변경 내역 분석

```bash
git fetch origin $TARGET_BRANCH
git log origin/$TARGET_BRANCH..$CURRENT_BRANCH --oneline
```

### Step 2: PR 본문 및 AI 코드 리뷰 재작성

`.github/pull_request_template.md` 및 `.github/code_review_template.md`를 참조하여 최신 변경사항을 `.pr_body_temp.md` 및 `.pr_review_temp.md`에 재작성합니다.

> **필수:** PR 브랜치에 새 커밋을 push할 때마다 최신 HEAD 기준 전체 diff를 다시 검토하고 새 AI 코드 리뷰 댓글을 등록합니다. 이전 리뷰가 존재하거나 지적 사항이 0건이어도 생략하지 않습니다. 리뷰 본문에는 검토한 HEAD SHA와 검증 결과를 포함합니다.

### Step 3: PR 본문 업데이트 및 새 리뷰 코멘트 등록

```bash
# 1. PR 본문 갱신 (PR 제목 6대 규격: feat:, fix:, refactor:, test:, docs:, chore: 준수)
gh pr edit \
  --title "<prefix>: <종합된 변경 제목>" \
  --body-file .pr_body_temp.md

# 2. 최신 HEAD 기준 AI 코드 리뷰 댓글 필수 등록
test -s .pr_review_temp.md || {
  echo "❌ Error: AI 코드 리뷰 파일이 없거나 비어 있습니다."
  exit 1
}

gh pr comment "$PR_URL" --body-file .pr_review_temp.md
```

### Step 4: 정리

```bash
rm -f .pr_body_temp.md .pr_review_temp.md
```

---

## 5. 최종 보고

반드시 아래 내용을 보고:

| 항목    | 값                                     |
| ------- | -------------------------------------- |
| 브랜치  | `$CURRENT_BRANCH`                      |
| 커밋    | O / X (커밋 메시지)                    |
| 푸시    | O / X                                  |
| PR 상태 | 신규 생성 / 업데이트 / 변경없음        |
| PR URL  | `<URL>`                                |
| Issue   | `#<Number>` (신규 생성 또는 기존 연결) |
| Target  | `$TARGET_BRANCH`                       |
| AI 리뷰 | O / X (최신 HEAD SHA 및 댓글 URL)      |

---

## ⚠️ 주의사항

1. **main에 직접 push 금지** - 작업 목적에 맞는 브랜치(`feat/*`, `fix/*`, `refactor/*`, `test/*`, `docs/*`, `chore/*`) 사용 및 PR 병합 필수
2. **브랜치명 및 PR 제목 규칙 준수 필수**:
   - `feat/`, `feat:` : 새로운 기능 구현
   - `fix/`, `fix:` : 버그 수정
   - `refactor/`, `refactor:` : 코드 구조 개선 (로직 불변)
   - `test/`, `test:` : 단위/통합 테스트 추가 및 코드 커버리지 확보 작업
   - `docs/`, `docs:` : 문서 작성 및 수정 (README, 명세서 등)
   - `chore/`, `chore:` : 빌드/환경 설정, 라이브러리 추가, .gitignore 등
3. **PR 본문 없이 생성 금지** - 항상 `.github/pull_request_template.md` 참조 후 생성
4. **PR 존재 확인 필수** - gh pr view로 확인 후 생성/업데이트 결정
5. **변경사항 없어도 PR 상태 확인** - 기존 PR이 있으면 업데이트 가능
6. **이슈 자동 연결**: 브랜치 이름에 번호(예: `feat/12-foo`)가 있으면 해당 이슈를 연결하고, 없으면 새로 생성합니다.
7. **기능별 커밋 분리**: 하나의 커밋에 너무 많은 변경사항을 담지 말고 기능 단위로 나누어 커밋하십시오.
8. **push 후 리뷰 생략 금지**: PR 브랜치에 push했다면 최신 HEAD 전체 diff에 대한 AI 코드 리뷰 댓글을 반드시 새로 등록합니다.

---

## 흐름도

```
시작
  │
  ▼
0. GH CLI 환경 및 인증 확인
  │
  ▼
1. 변경사항 및 주제(Domain) 분석 (git status -s)
  │
  ├── [다중 주제 감지] (예: Infra + CI/CD 변경사항 혼재)
  │     │
  │     ├── [Topic A 브랜치] (main 기준 `git checkout -b feat/infra-... main`)
  │     │     │
  │     │     ▼
  │     │   Topic A 관련 파일 선택적 스테이징 (`git add terraform/ ...`)
  │     │     │
  │     │     ▼
  │     │   Topic A 원자적 커밋 & 푸시
  │     │     │
  │     │     ▼
  │     │   Topic A 독립 PR 생성 (.github/pull_request_template.md)
  │     │     │
  │     │     ▼
  │     │   Topic A AI 리뷰 댓글 자동 등록 (.github/code_review_template.md)
  │     │
  │     └── [Topic B 브랜치] (main 기준 `git checkout -b chore/ci-... main`)
  │           │
  │           ▼
  │         Topic B 관련 파일 스테이징 (`git add .github/ ...`)
  │           │
  │           ▼
  │         Topic B 원자적 커밋 & 푸시
  │           │
  │           ▼
  │         Topic B 독립 PR 생성 & AI 리뷰 댓글 등록
  │           │
  │           ▼
  │         다중 PR 최종 보고
  │
  └── [단일 주제]
        │
        ▼
      현재 브랜치 확인
        │
        ├── `main` 브랜치 ──▶ 목적에 맞는 새 브랜치(`git checkout -b feat/<기능명>` 등) 생성
        │
        └── 규격 브랜치(`feat/*`, `fix/*` 등) 유지
              │
              ▼
            원자적 커밋 & 푸시
              │
              ▼
            PR 존재 여부 확인 (gh pr view)
              │
              ├── [기존 PR 존재] ──▶ PR 본문 최신화 & 새 리뷰 댓글 등록
              │
              └── [신규 PR 필요]
                    │
                    ▼
                  이슈 연결 (기존 이슈 또는 gh issue create)
                    │
                    ▼
                  PR 신규 생성 (.github/pull_request_template.md)
                    │
                    ▼
                  AI 코드 리뷰 댓글 자동 등록 (.github/code_review_template.md)
                    │
                    ▼
                  최종 보고
```
