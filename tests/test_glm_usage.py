from __future__ import annotations

import importlib.util
import unittest
from datetime import datetime
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
