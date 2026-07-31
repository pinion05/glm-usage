from __future__ import annotations

import importlib.util
import copy
import os
import stat
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "scripts" / "glm-usage.py"
SPEC = importlib.util.spec_from_file_location("glm_usage", SCRIPT)
assert SPEC and SPEC.loader
GLM = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GLM)


SAMPLE_QUOTA = {
    "code": 200,
    "success": True,
    "data": {
        "level": "pro",
        "limits": [
            {
                "type": "TIME_LIMIT",
                "usage": 1000,
                "currentValue": 340,
                "remaining": 660,
                "percentage": 34,
                "nextResetTime": 1786518556982,
                "usageDetails": [
                    {"modelCode": "search-prime", "usage": 294},
                    {"modelCode": "web-reader", "usage": 46},
                    {"modelCode": "zread", "usage": 0},
                ],
            },
            {"type": "TOKENS_LIMIT", "percentage": 1},
        ],
    },
}

SAMPLE_DETAILS = {
    "code": 200,
    "success": True,
    "data": {
        "totalUsage": {
            "totalModelCallCount": 9745,
            "totalTokensUsage": 1_134_473_530,
            "modelSummaryList": [
                {"modelName": "GLM-5.2", "totalTokens": 1_134_454_424},
                {"modelName": "GLM-4.6V", "totalTokens": 19_106},
            ],
        }
    },
}


class GlmUsageTests(unittest.TestCase):
    def test_quota_normalization(self) -> None:
        fetched = datetime(2026, 7, 31, 13, 40, tzinfo=GLM.KST)
        quota = GLM.normalize_quota(SAMPLE_QUOTA, fetched)

        self.assertEqual(quota["five_hours"]["used_percent"], 1)
        self.assertEqual(quota["five_hours"]["remaining_percent"], 99)
        self.assertEqual(quota["mcp"]["used_percent"], 34)
        self.assertEqual(quota["mcp"]["remaining_percent"], 66)
        self.assertEqual(quota["mcp"]["used"] + quota["mcp"]["remaining"], 1000)
        self.assertEqual(quota["mcp"]["tools"]["search-prime"], 294)
        self.assertEqual(quota["mcp"]["next_reset"], "2026-08-12 16:09 KST")

    def test_details_normalization(self) -> None:
        details = GLM.normalize_details(SAMPLE_DETAILS, "2026-07-25~2026-07-31", 7)

        self.assertEqual(details["model_calls"], 9745)
        self.assertEqual(details["total_tokens"], sum(details["models"].values()))
        self.assertEqual(details["models"]["GLM-4.6V"], 19_106)

    def test_text_contains_remaining_quota_without_secrets(self) -> None:
        fetched = datetime(2026, 7, 31, 13, 40, tzinfo=GLM.KST)
        result = {"quota": GLM.normalize_quota(SAMPLE_QUOTA, fetched)}
        text = GLM.format_text(result)

        self.assertIn("5시간: 1% 사용 · 99% 남음", text)
        self.assertIn("MCP: 34% 사용 · 66% 남음", text)
        self.assertNotIn("Authorization", text)
        self.assertNotIn("Bearer", text)

    def test_missing_quota_limit_is_rejected(self) -> None:
        payload = copy.deepcopy(SAMPLE_QUOTA)
        payload["data"]["limits"] = [payload["data"]["limits"][0]]

        with self.assertRaisesRegex(GLM.UsageError, "TOKENS_LIMIT 누락"):
            GLM.normalize_quota(payload, datetime.now(GLM.KST))

    def test_missing_details_aggregate_is_rejected(self) -> None:
        payload = copy.deepcopy(SAMPLE_DETAILS)
        del payload["data"]["totalUsage"]["totalTokensUsage"]

        with self.assertRaisesRegex(GLM.UsageError, "totalTokensUsage 누락"):
            GLM.normalize_details(payload, "2026-07-25~2026-07-31", 7)

    def test_non_numeric_tool_usage_is_rejected(self) -> None:
        payload = copy.deepcopy(SAMPLE_QUOTA)
        payload["data"]["limits"][0]["usageDetails"][0]["usage"] = None

        with self.assertRaisesRegex(GLM.UsageError, "숫자가 아님"):
            GLM.normalize_quota(payload, datetime.now(GLM.KST))

    def test_token_symlink_is_not_followed(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            config = Path(root) / "config"
            config.mkdir()
            target = Path(root) / "target"
            target.write_text("do-not-read", encoding="utf-8")
            token_file = config / "token"
            token_file.symlink_to(target)

            with (
                mock.patch.object(GLM, "CONFIG_DIR", config),
                mock.patch.object(GLM, "TOKEN_FILE", token_file),
                mock.patch.dict(os.environ, {}, clear=True),
            ):
                with self.assertRaisesRegex(GLM.UsageError, "안전하게 열 수 없음"):
                    GLM.read_token()

    def test_save_token_replaces_symlink_without_touching_target(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            config = Path(root) / "config"
            config.mkdir()
            target = Path(root) / "target"
            target.write_text("do-not-touch", encoding="utf-8")
            token_file = config / "token"
            token_file.symlink_to(target)

            with (
                mock.patch.object(GLM, "CONFIG_DIR", config),
                mock.patch.object(GLM, "TOKEN_FILE", token_file),
            ):
                GLM.save_token("safe-token")

            self.assertEqual(target.read_text(encoding="utf-8"), "do-not-touch")
            self.assertFalse(token_file.is_symlink())
            self.assertEqual(token_file.read_text(encoding="utf-8").strip(), "safe-token")
            self.assertEqual(stat.S_IMODE(token_file.stat().st_mode), 0o600)

    def test_browser_refresh_ignores_api_origin_tab(self) -> None:
        calls: list[tuple[str, ...]] = []

        def fake_browser(_port: int, *args: str) -> dict:
            calls.append(args)
            if args == ("tab", "list"):
                return {
                    "tabs": [
                        {"tabId": "api", "url": "https://api.z.ai/status", "active": True},
                        {"tabId": "web", "url": "https://z.ai/manage-apikey/usage"},
                    ]
                }
            if args[0] == "eval":
                return {"result": "browser-token"}
            return {}

        with (
            mock.patch.object(GLM, "_agent_browser", side_effect=fake_browser),
            mock.patch.object(GLM, "save_token"),
        ):
            self.assertEqual(GLM.refresh_token_from_browser(9222), "browser-token")

        self.assertIn(("tab", "web"), calls)
        self.assertIn(("tab", "api"), calls)

    def test_browser_refresh_polls_new_tab_until_token_exists(self) -> None:
        eval_count = 0

        def fake_browser(_port: int, *args: str) -> dict:
            nonlocal eval_count
            if args == ("tab", "list"):
                return {"tabs": []}
            if args[:2] == ("tab", "new"):
                return {"tabId": "new-tab"}
            if args[0] == "eval":
                eval_count += 1
                return {"result": "browser-token" if eval_count == 3 else None}
            return {}

        with (
            mock.patch.object(GLM, "_agent_browser", side_effect=fake_browser),
            mock.patch.object(GLM, "save_token"),
            mock.patch.object(GLM.time, "sleep"),
        ):
            self.assertEqual(GLM.refresh_token_from_browser(9222), "browser-token")

        self.assertEqual(eval_count, 3)

    def test_subprocess_error_does_not_expose_output(self) -> None:
        completed = mock.Mock(returncode=1, stderr="Bearer sensitive-token", stdout="")
        with (
            mock.patch.object(GLM.shutil, "which", return_value="agent-browser"),
            mock.patch.object(GLM.subprocess, "run", return_value=completed),
        ):
            with self.assertRaises(GLM.UsageError) as caught:
                GLM._agent_browser(9222, "tab", "list")

        message = str(caught.exception)
        self.assertNotIn("sensitive-token", message)
        self.assertNotIn("Bearer", message)


if __name__ == "__main__":
    unittest.main()
