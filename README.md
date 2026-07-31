# 📊 GLM Usage

<div align="center">

**Z.ai GLM Coding Plan 잔여량을 한 명령으로 확인하세요.**

[![CI](https://github.com/pinion05/glm-usage/actions/workflows/ci.yml/badge.svg)](https://github.com/pinion05/glm-usage/actions/workflows/ci.yml)

</div>

매번 대시보드를 열고 새로고침할 필요 없습니다. `glm-usage`를 실행하면 5시간 할당량, MCP 잔여량, 리셋 시각을 바로 보여줍니다.

## ✨ 한눈에 보기

| 기능 | 설명 |
|---|---|
| ⚡ 빠른 조회 | 5시간·MCP 할당량의 사용률과 잔여율 표시 |
| 🔎 상세 조회 | 기간별 모델 호출 수와 토큰 사용량 확인 |
| 🤖 Hermes 스킬 | “GLM 남은 거” 같은 요청에 바로 실행 |
| 🔐 안전한 인증 | 토큰을 코드나 출력에 노출하지 않고 로컬에 `0600`으로 보관 |
| 🧩 JSON 지원 | 셸 자동화와 후속 분석용 구조화 출력 |

## 🚀 설치

```bash
git clone https://github.com/pinion05/glm-usage.git
cd glm-usage

install -Dm755 scripts/glm-usage.py ~/.local/bin/glm-usage
mkdir -p ~/.hermes/skills/devops/glm-usage
cp SKILL.md ~/.hermes/skills/devops/glm-usage/
cp -r scripts ~/.hermes/skills/devops/glm-usage/
```

Hermes에서는 다음 세션부터 `glm-usage` 스킬을 자동으로 인식합니다. CLI에서 즉시 불러오려면:

```bash
hermes chat --skills glm-usage
```

## 💡 사용법

빠른 잔여량 조회:

```bash
glm-usage
```

최근 7일 모델 사용량 포함:

```bash
glm-usage --details
```

기간 지정과 JSON 출력:

```bash
glm-usage --details --days 30
glm-usage --json
```

인증을 다시 가져와야 할 때:

```bash
glm-usage --refresh-auth
```

## 출력 예시

```text
GLM Coding Plan
5시간: 1% 사용 · 99% 남음
MCP: 34% 사용 · 66% 남음 (340/1,000)
도구: search-prime 294 · web-reader 46 · zread 0
리셋: 2026-08-12 16:09 KST
```

## 🔐 인증과 보안

- `ZAI_DASHBOARD_TOKEN` 환경 변수 또는 `~/.config/glm-usage/token`을 사용합니다.
- 토큰 파일은 자동으로 `0600`, 설정 디렉터리는 `0700` 권한을 유지합니다.
- 토큰이 없거나 만료되면 로그인된 Z.ai Chrome CDP 세션에서 다시 가져옵니다.
- 인증값은 기본 출력과 JSON 출력에 포함되지 않습니다.

> 이 도구는 Z.ai 대시보드의 공개 문서화되지 않은 내부 API를 사용합니다. Z.ai가 URL이나 응답 구조를 변경하면 업데이트가 필요할 수 있으며, 화면 안내 기준 데이터는 약 10분 지연될 수 있습니다.

## 📄 구성

- `SKILL.md`
- `scripts/glm-usage.py`
- `tests/test_glm_usage.py`

## License

[MIT](LICENSE)
