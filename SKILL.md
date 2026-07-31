---
name: glm-usage
description: "Z.ai GLM Coding Plan 잔여량·MCP·토큰 사용량 조회 시 사용."
version: 1.0.0
metadata:
  hermes:
    tags: [zai, glm, coding-plan, quota, usage]
---

# GLM Usage

Z.ai GLM Coding Plan의 5시간 할당량, MCP 할당량, 도구별 사용량, 모델별 토큰 사용량을 대시보드 내부 JSON API로 조회한다. 사용자가 “GLM 남은 거”, “Z.ai 사용량”, “Coding Plan 쿼터”, “MCP 얼마나 남았어”라고 요청하면 이 스킬을 사용한다.

## 성공 기준

- 대시보드를 수동으로 열지 않고 API 응답으로 현재 할당량을 조회한다.
- 5시간 할당량과 MCP 할당량의 사용률·잔여율을 함께 보고한다.
- 인증값을 출력, 로그, 코드 또는 최종 응답에 노출하지 않는다.
- 상세 요청에만 기간별 모델 호출 수와 토큰 사용량을 추가한다.
- 결과가 대시보드 안내 기준 약 10분 지연될 수 있음을 명시한다.

## 기본 워크플로

### 1. CLI 존재 확인

```bash
command -v glm-usage
```

명령이 없으면 이 스킬의 `scripts/glm-usage.py`를 설치한다. `skill_view(name='glm-usage')`가 반환하는 `skill_dir`를 기준으로 복사한다.

```bash
install -Dm755 <skill_dir>/scripts/glm-usage.py ~/.local/bin/glm-usage
```

`~/.local/bin`이 `PATH`에 없으면 절대 경로로 실행하거나 사용자 셸의 `PATH`에 추가한다.

### 2. 빠른 잔여량 조회

```bash
glm-usage
```

기본 출력에서 다음 값을 읽는다.

- `5시간`: `TOKENS_LIMIT.percentage`를 사용률로 보고 `100 - 사용률`을 잔여율로 계산한다.
- `MCP`: `TIME_LIMIT.percentage`, `currentValue`, `usage`, `remaining`을 사용한다.
- `도구`: `TIME_LIMIT.usageDetails`의 `modelCode`와 `usage`를 사용한다.
- `리셋`: `TIME_LIMIT.nextResetTime`을 Asia/Seoul 시각으로 변환한다.

### 3. 상세 사용량 조회

사용자가 모델별 토큰, 호출 수, 기간별 사용량을 요청했을 때만 실행한다.

```bash
glm-usage --details
```

기간 지정:

```bash
glm-usage --details --days 30
```

### 4. 구조화 결과 조회

후속 자동화나 정확한 파싱에는 JSON 출력을 사용한다.

```bash
glm-usage --json
glm-usage --details --days 7 --json
```

## 인증 처리

정상 조회는 브라우저 없이 Bearer 인증으로 API를 직접 호출한다.

인증 우선순위:

1. `ZAI_DASHBOARD_TOKEN` 환경 변수
2. `~/.config/glm-usage/token`
3. Chrome CDP `9222`의 로그인된 Z.ai 탭에서 `localStorage` 토큰 재추출

인증이 만료됐거나 토큰 파일이 없으면 다음을 실행한다.

```bash
glm-usage --refresh-auth
```

재추출 조건:

- Chrome이 `--remote-debugging-port=9222`로 실행 중이어야 한다.
- `https://z.ai/`에 로그인된 탭이 있어야 한다.
- 탭이 없으면 CLI가 Usage 대시보드 탭을 열고 로그인 상태를 확인한다.

로그인이 풀렸으면 `browser-cdp-automation` 스킬을 함께 로드하고 GitHub OAuth 로그인 화면까지만 자동으로 연다. 비밀번호, MFA, 권한 승인 화면은 사용자 확인 없이 처리하지 않는다.

## 내부 API

현재 확인된 엔드포인트:

```text
GET https://api.z.ai/api/monitor/usage/quota/limit
GET https://api.z.ai/api/monitor/usage/model-usage?startTime=YYYY-MM-DD+00:00:00&endTime=YYYY-MM-DD+23:59:59
```

필수 요청 헤더는 `Authorization: Bearer <token>`이다. 조직·프로젝트 헤더 없이도 현재 계정에서 정상 응답함을 실측했다.

쿼터 응답 핵심 구조:

```json
{
  "data": {
    "limits": [
      {
        "type": "TIME_LIMIT",
        "usage": 1000,
        "currentValue": 340,
        "remaining": 660,
        "percentage": 34,
        "nextResetTime": 1786518556982,
        "usageDetails": []
      },
      {
        "type": "TOKENS_LIMIT",
        "percentage": 1
      }
    ],
    "level": "pro"
  }
}
```

`TIME_LIMIT`와 `TOKENS_LIMIT` 명칭은 UI 라벨과 직관적으로 반대처럼 보일 수 있다. 화면과 실제 응답을 교차 검증한 현재 매핑은 다음과 같다.

- `TOKENS_LIMIT` → **5 Hours Quota**
- `TIME_LIMIT` → **MCP Quota**

## 사용자 응답 형식

결론부터 짧게 보고한다.

```text
GLM Coding Plan
- 5시간: {used}% 사용 · {remaining}% 남음
- MCP: {used}% 사용 · {remaining}% 남음 ({current}/{limit})
- 도구: search-prime {n} · web-reader {n} · zread {n}
- 리셋: YYYY-MM-DD HH:MM KST
- 기준: API 조회 시각, 약 10분 지연 가능
```

상세 요청이 아니면 누적 토큰과 호출 수를 덧붙이지 않는다.

## 보안 규칙

- 토큰 원문을 `cat`, `read_file`, 로그, 코드블록, 최종 응답에 출력하지 않는다.
- 토큰을 SKILL.md나 `scripts/`에 하드코딩하지 않는다.
- `~/.config/glm-usage`는 `0700`, 토큰 파일은 `0600`을 유지한다.
- JSON 출력에도 Authorization 헤더나 토큰이 포함되지 않는지 검증한다.
- 임시 네트워크 캡처에 인증 헤더가 들어갔으면 검증 직후 삭제한다.

## 검증

설치 후 반드시 실제 API로 확인한다.

```bash
glm-usage
glm-usage --details --days 7
```

브라우저 도구 없이 direct API만으로 동작하는지 확인한다.

```bash
PATH=/usr/bin:/bin ~/.local/bin/glm-usage --json
```

권한을 확인한다.

```bash
stat -c '%a %n' ~/.config/glm-usage ~/.config/glm-usage/token
```

기대값:

```text
700 ~/.config/glm-usage
600 ~/.config/glm-usage/token
```

## 장애 복구

- `401` 또는 `403`: `glm-usage --refresh-auth`를 실행한다.
- CDP 연결 실패: `9222`의 Chrome 루트 프로세스와 `--user-data-dir`을 확인한다.
- 로그인 토큰 없음: Z.ai 대시보드에서 로그인한 뒤 인증 갱신을 다시 실행한다.
- 응답 스키마 변경: `browser-cdp-automation`의 authenticated XHR discovery 절차로 Usage 페이지의 XHR를 재수집한다.
- 수치가 화면과 다름: API와 대시보드 모두 약 10분 지연될 수 있으므로 새로고침 시각을 맞춰 비교한다.

이 API는 공개 문서가 아닌 대시보드 내부 엔드포인트다. URL이나 응답 스키마가 변경되면 실측 후 이 스킬과 `scripts/glm-usage.py`를 함께 갱신한다.
