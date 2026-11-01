---
name: smart-commit
description: >-
  Git 변경사항 분석, 주제별 브랜치 분할, 원자적 커밋, GitHub Flow 기반 PR 생성 및 최신화, 그리고 AI 코드 리뷰(치명적/경고/제안) 코멘트 자동 등록을 수행하는 엔드투엔드 워크플로우입니다. 커밋, 푸시, PR 생성 및 업데이트, 코드리뷰 요청 시 사용합니다.
---

# 🚀 Smart Commit & AI Code Review Workflow

Git 변경사항 분석부터 원자적 커밋, 브랜치 전략(GitHub Flow), PR 템플릿 기반 생성/최신화, 그리고 AI 코드 리뷰 코멘트 분리 등록까지 자동화하는 엔드투엔드 가이드입니다.

---

## 📌 핵심 원칙 및 규격 (Standards)

1. **브랜치 및 PR 접두사 6대 규격**:
   - `feat/` (`feat:`): 새로운 기능 구현
   - `fix/` (`fix:`): 버그 수정
   - `refactor/` (`refactor:`): 코드 구조 개선 (로직 불변)
   - `test/` (`test:`): 단위/통합 테스트 추가 및 커버리지 확보
   - `docs/` (`docs:`): 문서 작성 및 수정 (README, 명세서 등)
   - `chore/` (`chore:`): 빌드/환경 설정, 라이브러리 추가, .gitignore 등
2. **언어 표준**: 모든 결과 보고 및 PR 본문/리뷰 댓글은 **한국어**로 작성합니다.
3. **직접 push 금지 브랜치**: `main` (작업 브랜치에서 PR을 통해 main에 병합).
4. **리뷰 분리 원칙**: PR 본문 생성 직후, 별도의 댓글(Comment)로 AI 코드 리뷰를 자동 등록합니다.

---

## 0. GitHub CLI 환경 설정 및 인증 확인

```bash
# 1. GH CLI 설치 및 경로 확인
if ! command -v gh &> /dev/null; then
    if [ -f "/opt/homebrew/bin/gh" ]; then
        export PATH="/opt/homebrew/bin:$PATH"
    elif [ -f "/usr/local/bin/gh" ]; then
        export PATH="/usr/local/bin:$PATH"
    fi
fi

# 2. Auth 상태 확인
if ! gh auth status &> /dev/null; then
    echo "❌ Error: GitHub CLI is not authenticated. Please run 'gh auth login'."
    exit 1
fi
```

---

## 0-1. 브랜치 전략 및 명명 규칙 준수 확인

```bash
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
```

| 현재 브랜치 | push 가능 여부 | 조치 |
| :--- | :---: | :--- |
| `main` | ❌ 금지 | "main에 직접 push할 수 없습니다. 작업 브랜치(`feat/*` 등)를 생성하세요." 안내 후 작업 브랜치 생성 |
| `feat/*`, `fix/*`, `refactor/*`, `test/*`, `docs/*`, `chore/*` | ✅ 허용 | 계속 진행 |
| 기타 브랜치명 | ⚠️ 주의 | 규격 브랜치(`feat/*`, `fix/*` 등)로 새 브랜치 생성 권장 |

```bash
# main 브랜치인 경우 목적에 맞는 브랜치로 전환
git checkout -b feat/<기능명>
```

---

## 1. 현재 상태 및 도메인 주제 분석

```bash
git status -s
```

변경된 파일들의 도메인/주제(Scope)를 분석하여 **단일 주제**인지 **다중 주제**인지 판단합니다:
- 🏗️ **인프라 / IaC:** `terraform/**`, `docs/infra-project/**`
- ⚙️ **CI/CD 및 워크플로우:** `.github/**`
- 📦 **애플리케이션 및 쿠버네티스:** `src/**`, `helm-chart/**`, `kubernetes-manifests/**`, `monitoring/**`
- 📝 **문서 및 기타:** `README.md`, `project.md`, `docs/**`

### 🔀 분기 판단
1. **단일 주제인 경우**: 현재 브랜치에서 원자적 커밋 후 단일 PR 진행 (아래 2단계로 이동)
2. **다중 주제인 경우**: 성격이 다른 변경사항들을 묶지 말고, 주제별 독립 브랜치를 생성하여 각각 별도 PR로 분리 (아래 1-A단계 진행)

---

## 1-A. 다중 주제 브랜치 및 다중 PR 분할 워크플로우

성격이 다른 변경사항들이 섞여 있는 경우(예: 인프라 수정 + CI 템플릿 개편):

1. **주제 A 브랜치 생성 및 PR**:
   ```bash
   git checkout -b feat/infra-update main
   git add terraform/ docs/infra-project/
   git commit -m "feat(infra): EKS 및 Terraform 설정 최신화"
   git push -u origin feat/infra-update
   # 4-A 단계에 따라 Topic A 독립 PR 및 AI 리뷰 코멘트 생성
   ```
2. **주제 B 브랜치 생성 및 PR**:
   ```bash
   git checkout -b chore/ci-templates main
   git add .github/
   git commit -m "chore(ci): GitHub Actions 워크플로우 개선"
   git push -u origin chore/ci-templates
   # 4-A 단계에 따라 Topic B 독립 PR 및 AI 리뷰 코멘트 생성
   ```
3. 생성된 각 PR 링크를 최종 보고합니다.

---

## 2. 조건부 원자적 커밋 및 푸시 (단일 주제)

> [!IMPORTANT]
> **원자적 커밋(Atomic Commits)**: 변경사항이 서로 다른 기능을 포함한다면 기능별로 나누어 스테이징 및 커밋합니다.

```bash
# Conventional Commit 규격 준수 (<type>: <설명>)
git add <대상_파일들>
git commit -m "<type>: <설명>"
git push origin $CURRENT_BRANCH
```

---

## 3. Target Branch 및 PR 존재 여부 확인

본 프로젝트는 GitHub Flow 전략을 따르므로 타겟 브랜치는 **`main`**입니다.

```bash
TARGET_BRANCH="main"
PR_URL=$(gh pr view --json url,state --jq 'select(.state == "OPEN") | .url' 2>/dev/null || echo "")
```

- `PR_URL`이 비어 있음 ➡️ **4-A. PR 신규 생성**
- `PR_URL`이 존재함 ➡️ **4-B. 기존 PR 업데이트**

---

## 4-A. PR 신규 생성 (PR이 없는 경우)

### Step 1: 원격과의 커밋 차이 확인
```bash
git fetch origin $TARGET_BRANCH
COMMITS=$(git log origin/$TARGET_BRANCH..$CURRENT_BRANCH --oneline)
```

### Step 2: PR 본문 및 AI 코드 리뷰 초안 작성
1. **PR 본문 작성:** [pull_request_template.md](./references/pull_request_template.md) 양식을 참조하여 `.pr_body_temp.md`를 작성합니다:
   - 📌 배경 및 목적 (Background)
   - 📋 주요 변경 사항 (Change Summary)
   - 📁 변경된 파일 (Changed Files)
   - 🧪 검증 절차 및 결과 (Testing Procedure)
   - 📝 추가 참고사항 (Additional Notes)
2. **AI 코드 리뷰 작성:** [code_review_template.md](./references/code_review_template.md) 가이드라인(Terraform, K8s v1.36, OTel, 보안 체크포인트)을 확인하고 `git diff origin/$TARGET_BRANCH...$CURRENT_BRANCH`를 분석하여 `.pr_review_temp.md`를 작성합니다:
   - 📊 종합 요약
   - 🔴 치명적 (N건)
   - ⚠️ 경고 (N건)
   - 💡 개선 제안 (1~3건)

### Step 3: Issue 자동 생성 및 연결
```bash
PR_TITLE="<prefix>: <종합된 변경 제목>"

# 브랜치명에서 이슈 번호 감지 (예: feat/12-foo -> 12)
DETECTED_ISSUE_NUM=$(echo "$CURRENT_BRANCH" | grep -oE '/[0-9]+(-|$)' | tr -d '/-')
EXISTING_ISSUE_FOUND=false

if [ -n "$DETECTED_ISSUE_NUM" ]; then
  if gh issue view "$DETECTED_ISSUE_NUM" > /dev/null 2>&1; then
    ISSUE_NUM="$DETECTED_ISSUE_NUM"
    EXISTING_ISSUE_FOUND=true
  fi
fi

if [ "$EXISTING_ISSUE_FOUND" = false ]; then
  ISSUE_URL=$(gh issue create --title "$PR_TITLE" --body-file .pr_body_temp.md --assignee "@me")
  ISSUE_NUM=${ISSUE_URL##*/}
fi

echo -e "\n\nCloses #$ISSUE_NUM" >> .pr_body_temp.md
```

### Step 4: PR 생성 및 AI 리뷰 코멘트 분리 등록
```bash
# 1. PR 생성
PR_URL=$(gh pr create --title "$PR_TITLE" --body-file .pr_body_temp.md --base $TARGET_BRANCH)
echo "✅ PR 생성 완료: $PR_URL"

# 2. PR 생성 직후 별도 댓글로 AI 코드 리뷰 필수 등록
gh pr comment "$PR_URL" --body-file .pr_review_temp.md
echo "🤖 AI 코드 리뷰 코멘트 등록 완료."

# 3. 임시 파일 정리
rm -f .pr_body_temp.md .pr_review_temp.md
```

---

## 4-B. 기존 PR 업데이트 (PR이 이미 있는 경우)

1. 최신 커밋 로그 및 diff 분석 (`git log origin/$TARGET_BRANCH..$CURRENT_BRANCH --oneline`)
2. 최신 변경사항을 반영하여 `.pr_body_temp.md` 및 `.pr_review_temp.md` 재작성
3. PR 본문 갱신 및 새 리뷰 댓글 등록:
   ```bash
   gh pr edit --title "<prefix>: <제목>" --body-file .pr_body_temp.md
   gh pr comment "$PR_URL" --body-file .pr_review_temp.md
   rm -f .pr_body_temp.md .pr_review_temp.md
   ```

---

## 5. 최종 결과 보고 서식

작업이 완료되면 사용자에게 다음 테이블로 명확히 보고합니다:

| 항목 | 내용 |
| :--- | :--- |
| **작업 브랜치** | `$CURRENT_BRANCH` |
| **커밋 상태** | 커밋 완료 (커밋 메시지) |
| **푸시 상태** | 푸시 완료 (`origin/$CURRENT_BRANCH`) |
| **PR 상태** | 신규 생성 / 업데이트 완료 |
| **PR 링크** | `[PR 바로가기](<URL>)` |
| **연결 이슈** | `#<Issue_Number>` |
| **AI 코드 리뷰** | 코멘트 등록 완료 (🔴 치명적 N건, ⚠️ 경고 N건, 💡 제안 N건) |

