#!/usr/bin/env python3
"""Query Z.ai GLM Coding Plan usage via the dashboard's JSON API."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
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


def read_token() -> str | None:
    env_token = os.environ.get("ZAI_DASHBOARD_TOKEN", "").strip()
    if env_token:
        return env_token.removeprefix("Bearer ").strip()
    try:
        token = TOKEN_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    if token:
        os.chmod(TOKEN_FILE, stat.S_IRUSR | stat.S_IWUSR)
        return token.removeprefix("Bearer ").strip()
    return None


def save_token(token: str) -> None:
    token = token.removeprefix("Bearer ").strip()
    if not token:
        raise UsageError("빈 인증 토큰은 저장할 수 없음")
    CONFIG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(CONFIG_DIR, 0o700)
    tmp = TOKEN_FILE.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(token)
            handle.write("\n")
        os.replace(tmp, TOKEN_FILE)
        os.chmod(TOKEN_FILE, 0o600)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def _agent_browser(port: int, *args: str) -> dict:
    binary = shutil.which("agent-browser")
    if not binary:
        raise UsageError("agent-browser가 없어 브라우저 인증을 갱신할 수 없음")
    proc = subprocess.run(
        [binary, "--cdp", str(port), *args, "--json"],
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        message = detail[-1] if detail else f"exit {proc.returncode}"
        raise UsageError(f"브라우저 연결 실패: {message}")
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise UsageError("agent-browser 응답을 해석하지 못함") from exc
    if not payload.get("success"):
        raise UsageError(f"브라우저 작업 실패: {payload.get('error') or 'unknown error'}")
    return payload.get("data") or {}


def refresh_token_from_browser(port: int) -> str:
    tabs_data = _agent_browser(port, "tab", "list")
    tabs = tabs_data.get("tabs") or []
    previous = next((tab.get("tabId") for tab in tabs if tab.get("active")), None)
    zai_tab = next(
        (
            tab.get("tabId")
            for tab in tabs
            if str(tab.get("url", "")).startswith(("https://z.ai/", "https://api.z.ai/"))
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
        if not zai_tab:
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
        raise UsageError("Z.ai 브라우저 탭을 만들지 못함")

    try:
        _agent_browser(port, "tab", str(zai_tab))
        if created_tab:
            _agent_browser(port, "wait", "2500")
        result = _agent_browser(port, "eval", f'localStorage.getItem("{TOKEN_KEY}")')
        token = result.get("result")
        if not isinstance(token, str) or not token.strip():
            raise UsageError("Z.ai 로그인 토큰이 없음. 대시보드에서 먼저 로그인해야 함")
        save_token(token)
        return token.strip()
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
            raise UsageError("AUTH_EXPIRED") from exc
        detail = exc.read(500).decode("utf-8", errors="replace")
        raise UsageError(f"Z.ai API HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise UsageError(f"Z.ai API 연결 실패: {exc.reason}") from exc

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise UsageError("Z.ai API가 JSON이 아닌 응답을 반환함") from exc
    if payload.get("code") != 200 or not payload.get("success", True):
        raise UsageError(f"Z.ai API 오류: {payload.get('msg') or payload.get('code')}")
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
    except UsageError as exc:
        if str(exc) != "AUTH_EXPIRED":
            raise
    refreshed = refresh_token_from_browser(port)
    payload, headers = api_get(path, refreshed, params)
    return payload, headers, refreshed


def find_limit(limits: list[dict], limit_type: str) -> dict:
    return next((item for item in limits if item.get("type") == limit_type), {})


def clamp_percent(value: object) -> int:
    try:
        number = round(float(value))
    except (TypeError, ValueError):
        number = 0
    return max(0, min(100, number))


def reset_time_kst(milliseconds: object) -> str | None:
    try:
        value = float(milliseconds)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(value / 1000, tz=KST).strftime("%Y-%m-%d %H:%M KST")


def normalize_quota(payload: dict, fetched_at: datetime) -> dict:
    data = payload.get("data") or {}
    limits = data.get("limits") or []
    five_hours = find_limit(limits, "TOKENS_LIMIT")
    mcp = find_limit(limits, "TIME_LIMIT")

    five_used = clamp_percent(five_hours.get("percentage"))
    mcp_used = clamp_percent(mcp.get("percentage"))
    mcp_total = mcp.get("usage")
    mcp_current = mcp.get("currentValue")
    mcp_remaining_value = mcp.get("remaining")

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
            "next_reset": reset_time_kst(mcp.get("nextResetTime")),
            "tools": {
                str(item.get("modelCode")): item.get("usage")
                for item in (mcp.get("usageDetails") or [])
                if item.get("modelCode")
            },
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
    total = ((payload.get("data") or {}).get("totalUsage") or {})
    return {
        "days": days,
        "period": period,
        "model_calls": total.get("totalModelCallCount", 0),
        "total_tokens": total.get("totalTokensUsage", 0),
        "models": {
            str(item.get("modelName")): item.get("totalTokens", 0)
            for item in (total.get("modelSummaryList") or [])
            if item.get("modelName")
        },
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
        message = str(exc)
        if message == "AUTH_EXPIRED":
            message = "Z.ai 인증이 만료됐고 브라우저에서 갱신하지 못함"
        print(f"오류: {message}", file=sys.stderr)
        print(
            "해결: Z.ai 대시보드에 로그인한 Chrome(CDP 9222)을 켠 뒤 "
            "`glm-usage --refresh-auth` 실행",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
