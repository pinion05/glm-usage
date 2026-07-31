#!/usr/bin/env python3
"""Query Z.ai GLM Coding Plan usage via the dashboard's JSON API."""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

API_BASE = "https://api.z.ai/api/monitor/usage"
TOKEN_KEY = "z-ai-open-platform-token-production"
CONFIG_DIR = Path.home() / ".config" / "glm-usage"
TOKEN_FILE = CONFIG_DIR / "token"
KST = ZoneInfo("Asia/Seoul")


class UsageError(RuntimeError):
    pass


class AuthExpired(UsageError):
    pass


def _secure_config_dir() -> None:
    try:
        CONFIG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(CONFIG_DIR, flags)
        try:
            if not stat.S_ISDIR(os.fstat(fd).st_mode):
                raise UsageError("인증 설정 경로가 디렉터리가 아님")
            os.fchmod(fd, 0o700)
        finally:
            os.close(fd)
    except OSError as exc:
        raise UsageError("인증 설정 디렉터리를 안전하게 열 수 없음") from exc


def read_token() -> str | None:
    env_token = os.environ.get("ZAI_DASHBOARD_TOKEN", "").strip()
    if env_token:
        return env_token.removeprefix("Bearer ").strip()

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(TOKEN_FILE, flags)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise UsageError("토큰 파일을 안전하게 열 수 없음") from exc

    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise UsageError("토큰 경로가 일반 파일이 아님")
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            fd = -1
            token = handle.read(65_537).strip()
    except OSError as exc:
        raise UsageError("토큰 파일을 안전하게 읽을 수 없음") from exc
    finally:
        if fd >= 0:
            os.close(fd)

    if len(token) > 65_536:
        raise UsageError("토큰 파일이 비정상적으로 큼")
    if token:
        return token.removeprefix("Bearer ").strip()
    return None


def save_token(token: str) -> None:
    token = token.removeprefix("Bearer ").strip()
    if not token:
        raise UsageError("빈 인증 토큰은 저장할 수 없음")
    _secure_config_dir()

    fd = -1
    verify_fd = -1
    tmp: Path | None = None
    try:
        fd, tmp_name = tempfile.mkstemp(prefix=".token-", dir=CONFIG_DIR)
        tmp = Path(tmp_name)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = -1
            handle.write(token)
            handle.write("\n")
        os.replace(tmp, TOKEN_FILE)
        tmp = None

        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        verify_fd = os.open(TOKEN_FILE, flags)
        if not stat.S_ISREG(os.fstat(verify_fd).st_mode):
            raise UsageError("저장된 토큰 경로가 일반 파일이 아님")
        os.fchmod(verify_fd, 0o600)
    except OSError as exc:
        raise UsageError("토큰을 안전하게 저장할 수 없음") from exc
    finally:
        if fd >= 0:
            os.close(fd)
        if verify_fd >= 0:
            os.close(verify_fd)
        if tmp is not None:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass


def _agent_browser(port: int, *args: str) -> dict:
    binary = shutil.which("agent-browser")
    if not binary:
        raise UsageError("agent-browser가 없어 브라우저 인증을 갱신할 수 없음")
    try:
        proc = subprocess.run(
            [binary, "--cdp", str(port), *args, "--json"],
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise UsageError("브라우저 작업 시간 초과") from exc
    except OSError as exc:
        raise UsageError("agent-browser 실행 실패") from exc
    if proc.returncode != 0:
        raise UsageError(f"브라우저 작업 실패(exit {proc.returncode})")
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise UsageError("agent-browser 응답을 해석하지 못함") from exc
    if not payload.get("success"):
        raise UsageError("브라우저 작업 실패")
    return payload.get("data") or {}


def refresh_token_from_browser(port: int) -> str:
    tabs_data = _agent_browser(port, "tab", "list")
    tabs = tabs_data.get("tabs") or []
    previous = next((tab.get("tabId") for tab in tabs if tab.get("active")), None)
    zai_tab = next(
        (
            tab.get("tabId")
            for tab in tabs
            if str(tab.get("url", "")).startswith("https://z.ai/")
        ),
        None,
    )
    created_tab = False
    if not zai_tab:
        created = _agent_browser(
            port,
            "tab",
            "new",
            "https://z.ai/manage-apikey/coding-plan/personal/usage",
        )
        zai_tab = created.get("tabId") or created.get("id")
        created_tab = True
        for _ in range(10):
            if zai_tab:
                break
            tabs = (_agent_browser(port, "tab", "list").get("tabs") or [])
            zai_tab = next(
                (
                    tab.get("tabId")
                    for tab in tabs
                    if str(tab.get("url", "")).startswith("https://z.ai/")
                ),
                None,
            )
            if not zai_tab:
                time.sleep(0.5)
    if not zai_tab:
        raise UsageError("Z.ai 브라우저 탭을 만들지 못함")

    try:
        _agent_browser(port, "tab", str(zai_tab))
        token = None
        attempts = 40 if created_tab else 1
        for attempt in range(attempts):
            result = _agent_browser(port, "eval", f'localStorage.getItem("{TOKEN_KEY}")')
            candidate = result.get("result")
            if isinstance(candidate, str) and candidate.strip():
                token = candidate.strip()
                break
            if attempt + 1 < attempts:
                time.sleep(0.5)
        if not isinstance(token, str) or not token.strip():
            raise UsageError("Z.ai 페이지 로딩이 끝나지 않았거나 로그인이 필요함")
        save_token(token)
        return token
    finally:
        if previous and previous != zai_tab:
            try:
                _agent_browser(port, "tab", str(previous))
            except UsageError:
                pass


def api_get(path: str, token: str, params: dict[str, str] | None = None) -> tuple[dict, dict]:
    url = f"{API_BASE}/{path.lstrip('/')}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "glm-usage/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read().decode("utf-8")
            response_headers = dict(response.headers.items())
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise AuthExpired("Z.ai 인증이 만료됨") from exc
        raise UsageError(f"Z.ai API HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise UsageError("Z.ai API 연결 실패") from exc

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise UsageError("Z.ai API가 JSON이 아닌 응답을 반환함") from exc
    if payload.get("code") in (401, 403, "401", "403"):
        raise AuthExpired("Z.ai 인증이 만료됨")
    if payload.get("code") != 200 or not payload.get("success", True):
        raise UsageError(f"Z.ai API 오류(code={payload.get('code')})")
    return payload, response_headers


def request_with_auth_retry(
    path: str,
    token: str,
    port: int,
    params: dict[str, str] | None = None,
) -> tuple[dict, dict, str]:
    try:
        payload, headers = api_get(path, token, params)
        return payload, headers, token
    except AuthExpired:
        pass
    refreshed = refresh_token_from_browser(port)
    payload, headers = api_get(path, refreshed, params)
    return payload, headers, refreshed


def find_limit(limits: list[dict], limit_type: str) -> dict:
    limit = next(
        (item for item in limits if isinstance(item, dict) and item.get("type") == limit_type),
        None,
    )
    if limit is None:
        raise UsageError(f"Z.ai API 응답 스키마 변경: {limit_type} 누락")
    return limit


def required_number(value: object, label: str) -> float:
    if isinstance(value, bool):
        raise UsageError(f"Z.ai API 응답 스키마 변경: {label}이 숫자가 아님")
    if not isinstance(value, (int, float, str)):
        raise UsageError(f"Z.ai API 응답 스키마 변경: {label}이 숫자가 아님")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise UsageError(f"Z.ai API 응답 스키마 변경: {label}이 숫자가 아님") from exc
    if not math.isfinite(number):
        raise UsageError(f"Z.ai API 응답 스키마 변경: {label}이 유한수가 아님")
    return number


def required_int(mapping: dict, key: str, context: str) -> int:
    if key not in mapping:
        raise UsageError(f"Z.ai API 응답 스키마 변경: {context}.{key} 누락")
    number = required_number(mapping[key], f"{context}.{key}")
    if not number.is_integer():
        raise UsageError(f"Z.ai API 응답 스키마 변경: {context}.{key}이 정수가 아님")
    return int(number)


def clamp_percent(value: object, label: str) -> int:
    number = required_number(value, label)
    if number < 0 or number > 100:
        raise UsageError(f"Z.ai API 응답 스키마 변경: {label} 범위 오류")
    return round(number)


def reset_time_kst(milliseconds: object) -> str:
    value = required_number(milliseconds, "TIME_LIMIT.nextResetTime")
    return datetime.fromtimestamp(value / 1000, tz=KST).strftime("%Y-%m-%d %H:%M KST")


def normalize_quota(payload: dict, fetched_at: datetime) -> dict:
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("limits"), list):
        raise UsageError("Z.ai API 응답 스키마 변경: data.limits 누락")
    limits = data["limits"]
    five_hours = find_limit(limits, "TOKENS_LIMIT")
    mcp = find_limit(limits, "TIME_LIMIT")

    if "percentage" not in five_hours or "percentage" not in mcp:
        raise UsageError("Z.ai API 응답 스키마 변경: quota percentage 누락")
    five_used = clamp_percent(five_hours["percentage"], "TOKENS_LIMIT.percentage")
    mcp_used = clamp_percent(mcp["percentage"], "TIME_LIMIT.percentage")
    mcp_total = required_int(mcp, "usage", "TIME_LIMIT")
    mcp_current = required_int(mcp, "currentValue", "TIME_LIMIT")
    mcp_remaining_value = required_int(mcp, "remaining", "TIME_LIMIT")
    if mcp_total < 0 or mcp_current < 0 or mcp_remaining_value < 0:
        raise UsageError("Z.ai API 응답 스키마 변경: MCP 사용량이 음수임")
    if mcp_current + mcp_remaining_value != mcp_total:
        raise UsageError("Z.ai API 응답 스키마 변경: MCP 합계가 일치하지 않음")

    usage_details = mcp.get("usageDetails", [])
    if not isinstance(usage_details, list):
        raise UsageError("Z.ai API 응답 스키마 변경: TIME_LIMIT.usageDetails 형식 오류")
    tools: dict[str, int] = {}
    for index, item in enumerate(usage_details):
        if not isinstance(item, dict) or not item.get("modelCode"):
            raise UsageError(f"Z.ai API 응답 스키마 변경: usageDetails[{index}] 형식 오류")
        tools[str(item["modelCode"])] = required_int(
            item, "usage", f"TIME_LIMIT.usageDetails[{index}]"
        )

    if "nextResetTime" not in mcp:
        raise UsageError("Z.ai API 응답 스키마 변경: TIME_LIMIT.nextResetTime 누락")

    return {
        "plan_level": data.get("level"),
        "five_hours": {
            "used_percent": five_used,
            "remaining_percent": 100 - five_used,
        },
        "mcp": {
            "used_percent": mcp_used,
            "remaining_percent": 100 - mcp_used,
            "used": mcp_current,
            "limit": mcp_total,
            "remaining": mcp_remaining_value,
            "next_reset": reset_time_kst(mcp["nextResetTime"]),
            "tools": tools,
        },
        "fetched_at": fetched_at.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
        "delay_notice": "대시보드 안내 기준 약 10분 지연 가능",
    }


def model_usage_params(days: int, now: datetime) -> tuple[dict[str, str], str]:
    end_date = now.astimezone(KST).date()
    start_date = end_date - timedelta(days=days - 1)
    params = {
        "startTime": f"{start_date.isoformat()} 00:00:00",
        "endTime": f"{end_date.isoformat()} 23:59:59",
    }
    return params, f"{start_date.isoformat()}~{end_date.isoformat()}"


def normalize_details(payload: dict, period: str, days: int) -> dict:
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("totalUsage"), dict):
        raise UsageError("Z.ai API 응답 스키마 변경: data.totalUsage 누락")
    total = data["totalUsage"]
    model_calls = required_int(total, "totalModelCallCount", "totalUsage")
    total_tokens = required_int(total, "totalTokensUsage", "totalUsage")
    summaries = total.get("modelSummaryList", [])
    if not isinstance(summaries, list):
        raise UsageError("Z.ai API 응답 스키마 변경: modelSummaryList 형식 오류")
    models: dict[str, int] = {}
    for index, item in enumerate(summaries):
        if not isinstance(item, dict) or not item.get("modelName"):
            raise UsageError(f"Z.ai API 응답 스키마 변경: modelSummaryList[{index}] 형식 오류")
        models[str(item["modelName"])] = required_int(
            item, "totalTokens", f"modelSummaryList[{index}]"
        )
    return {
        "days": days,
        "period": period,
        "model_calls": model_calls,
        "total_tokens": total_tokens,
        "models": models,
    }


def human_number(value: object) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    for divisor, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if abs(number) >= divisor:
            text = f"{number / divisor:.2f}".rstrip("0").rstrip(".")
            return f"{text}{suffix}"
    return f"{number:,.0f}"


def format_text(result: dict) -> str:
    five = result["quota"]["five_hours"]
    mcp = result["quota"]["mcp"]
    lines = [
        "GLM Coding Plan",
        f"5시간: {five['used_percent']}% 사용 · {five['remaining_percent']}% 남음",
        (
            f"MCP: {mcp['used_percent']}% 사용 · {mcp['remaining_percent']}% 남음"
            + (
                f" ({mcp['used']:,}/{mcp['limit']:,})"
                if isinstance(mcp.get("used"), (int, float))
                and isinstance(mcp.get("limit"), (int, float))
                else ""
            )
        ),
    ]
    if mcp.get("tools"):
        tools = " · ".join(f"{name} {value:,}" for name, value in mcp["tools"].items())
        lines.append(f"도구: {tools}")
    if mcp.get("next_reset"):
        lines.append(f"리셋: {mcp['next_reset']}")

    details = result.get("details")
    if details:
        lines.append(
            f"최근 {details['days']}일: {human_number(details['total_tokens'])} tokens"
            f" · {details['model_calls']:,} calls"
        )
        if details.get("models"):
            models = " · ".join(
                f"{name} {human_number(value)}" for name, value in details["models"].items()
            )
            lines.append(f"모델: {models}")

    lines.append(f"조회: {result['quota']['fetched_at']} (약 10분 지연 가능)")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Z.ai GLM Coding Plan 잔여량을 대시보드 내부 API로 조회"
    )
    parser.add_argument("--details", action="store_true", help="모델별 토큰 사용량도 조회")
    parser.add_argument("--days", type=int, default=7, help="상세 사용량 기간 (기본 7일)")
    parser.add_argument("--json", action="store_true", help="정규화된 JSON 출력")
    parser.add_argument(
        "--refresh-auth",
        action="store_true",
        help="CDP 브라우저의 Z.ai 로그인 토큰을 다시 가져옴",
    )
    parser.add_argument("--port", type=int, default=9222, help="Chrome CDP 포트 (기본 9222)")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.days < 1 or args.days > 366:
        print("오류: --days는 1~366 범위여야 함", file=sys.stderr)
        return 2

    try:
        token = refresh_token_from_browser(args.port) if args.refresh_auth else read_token()
        if not token:
            token = refresh_token_from_browser(args.port)

        now = datetime.now(KST)
        quota_payload, _headers, token = request_with_auth_retry(
            "quota/limit", token, args.port
        )
        result = {"quota": normalize_quota(quota_payload, now)}

        if args.details:
            params, period = model_usage_params(args.days, now)
            details_payload, _headers, token = request_with_auth_retry(
                "model-usage", token, args.port, params
            )
            result["details"] = normalize_details(details_payload, period, args.days)

        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(format_text(result))
        return 0
    except UsageError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        print(
            "해결: Z.ai 대시보드에 로그인한 Chrome(CDP 9222)을 켠 뒤 "
            "`glm-usage --refresh-auth` 실행",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
