import asyncio
import json
import os
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

from app.models import PRReflection
from app.github_client import GitHubContextHydrator
from app.email_dispatcher import (
    generate_plain_text_body,
    generate_html_body,
    dispatch_reflection_email,
)
from app.webhook import verify_signature, PROCESSED_DELIVERIES
from app.logger import LOG_FILE_PATH, logger
from main import app
from fastapi.testclient import TestClient


class TestPRReflectionSchema(unittest.TestCase):
    """Tests Pydantic validation rules for PRReflection."""

    def test_valid_reflection(self):
        reflection = PRReflection(
            issue_details="Race condition in cache invalidation during rapid token refresh.",
            execution_summary="Added asyncio.Lock() around token rotation logic in auth/service.py.",
            key_learnings=[
                "Concurrent async coroutines require explicit mutex synchronization.",
                "FastAPI lifespan manages connection teardown deterministically.",
                "Stateless token handlers prevent memory leaks under sustained load."
            ]
        )
        self.assertEqual(len(reflection.key_learnings), 3)
        self.assertTrue(reflection.issue_details.startswith("Race condition"))

    def test_reflection_validation_errors(self):
        # Must have at least 3 key learnings
        with self.assertRaises(Exception):
            PRReflection(
                issue_details="Root cause",
                execution_summary="Execution",
                key_learnings=["Only one learning"]
            )

        # Must not exceed 4 key learnings
        with self.assertRaises(Exception):
            PRReflection(
                issue_details="Root cause",
                execution_summary="Execution",
                key_learnings=["1", "2", "3", "4", "5"]
            )


class TestGitHubContextHydration(unittest.TestCase):
    """Tests GitHub issue extraction regex and hydration logic."""

    def setUp(self):
        self.hydrator = GitHubContextHydrator()

    def test_extract_issue_numbers(self):
        body_with_single = "This PR refactors auth. Fixes #42."
        self.assertEqual(self.hydrator.extract_issue_numbers(body_with_single), [42])

        body_with_multiple = "Addresses bug reports:\n- Closes #101\n- Resolves #102\n- Fixed #103"
        self.assertEqual(self.hydrator.extract_issue_numbers(body_with_multiple), [101, 102, 103])

        body_with_url = "Fixes https://github.com/tiangolo/fastapi/issues/999"
        self.assertEqual(self.hydrator.extract_issue_numbers(body_with_url), [999])

        body_with_none = "Just a minor documentation fix with no issue."
        self.assertEqual(self.hydrator.extract_issue_numbers(body_with_none), [])

        self.assertEqual(self.hydrator.extract_issue_numbers(None), [])


class TestEmailDispatcher(unittest.TestCase):
    """Tests email formatting and subject generation."""

    def setUp(self):
        self.reflection = PRReflection(
            issue_details="Memory leak in long-lived SSE connections.",
            execution_summary="Refactored event stream generator to properly close unconsumed generators.",
            key_learnings=[
                "Async generators require explicit close() when clients disconnect.",
                "Starlette Response streaming uses background worker tasks.",
                "TCP disconnect detection depends on heartbeat pings."
            ]
        )

    def test_plain_text_formatting(self):
        text = generate_plain_text_body(
            repo_name="encode/starlette",
            pr_number=2048,
            pr_title="Fix SSE disconnect cleanup",
            pr_url="https://github.com/encode/starlette/pull/2048",
            reflection=self.reflection,
        )
        self.assertIn("1. ISSUE DETAILS", text)
        self.assertIn("2. EXECUTION SUMMARY", text)
        self.assertIn("3. KEY LEARNINGS", text)
        self.assertIn("Memory leak in long-lived SSE", text)
        self.assertIn("1. Async generators", text)

    def test_html_formatting(self):
        html = generate_html_body(
            repo_name="encode/starlette",
            pr_number=2048,
            pr_title="Fix SSE disconnect cleanup",
            pr_url="https://github.com/encode/starlette/pull/2048",
            reflection=self.reflection,
        )
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("1. Issue Details", html)
        self.assertIn("2. Execution Summary", html)
        self.assertIn("3. Key Learnings", html)
        self.assertIn("Takeaway 1:", html)
        self.assertIn("[OS-Log] PR Merged: encode/starlette (#2048)", html)


class TestWebhookEndpoint(unittest.TestCase):
    """Tests FastAPI POST /webhook/github endpoint filtering and responses."""

    def setUp(self):
        self.client = TestClient(app)
        PROCESSED_DELIVERIES.clear()

    def test_health_check(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "healthy")

    def test_ignored_opened_pr(self):
        payload = {
            "action": "opened",
            "number": 10,
            "pull_request": {"number": 10, "merged": False, "title": "New feature"},
            "repository": {"full_name": "octocat/Hello-World"}
        }
        response = self.client.post("/webhook/github", json=payload)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ignored")
        self.assertIn("not 'closed'", data["reason"])

    def test_ignored_closed_unmerged_pr(self):
        payload = {
            "action": "closed",
            "number": 11,
            "pull_request": {"number": 11, "merged": False, "title": "Discarded PR"},
            "repository": {"full_name": "octocat/Hello-World"}
        }
        response = self.client.post("/webhook/github", json=payload)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ignored")
        self.assertIn("merged status is False", data["reason"])

    def test_ignored_synchronize_pr(self):
        payload = {
            "action": "synchronize",
            "number": 12,
            "pull_request": {"number": 12, "merged": False, "title": "Updated commits"},
            "repository": {"full_name": "octocat/Hello-World"}
        }
        response = self.client.post("/webhook/github", json=payload)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ignored")

    @patch("app.webhook.hydrator.hydrate_full_pr_context")
    @patch("app.webhook.synthesize_pr_reflection")
    @patch("app.webhook.dispatch_reflection_email")
    def test_accepted_merged_pr(self, mock_dispatch, mock_synthesize, mock_hydrate):
        mock_hydrate.return_value = {
            "changed_files_summary": "main.py, config.py",
            "diff_text": "+++ diff code here +++",
            "total_diff_chars": 1234,
            "issue_context_str": "Fixes #5",
        }
        mock_reflection = PRReflection(
            issue_details="Root cause described in issue #5.",
            execution_summary="Updated config loader.",
            key_learnings=["Learned 1", "Learned 2", "Learned 3"]
        )
        mock_synthesize.return_value = mock_reflection
        mock_dispatch.return_value = (True, "Delivered")

        payload = {
            "action": "closed",
            "number": 100,
            "pull_request": {
                "number": 100,
                "merged": True,
                "title": "Merge feature into main",
                "body": "Fixes #5",
                "html_url": "https://github.com/octocat/Hello-World/pull/100"
            },
            "repository": {"full_name": "octocat/Hello-World"}
        }

        response = self.client.post(
            "/webhook/github",
            json=payload,
            headers={"X-GitHub-Delivery": "test-delivery-unique-1"}
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "processed")
        self.assertEqual(data["pr_number"], 100)
        self.assertEqual(data["diff_characters"], 1234)
        self.assertEqual(data["reflection"]["issue_details"], "Root cause described in issue #5.")

    def test_idempotency_prevents_duplicate_processing(self):
        PROCESSED_DELIVERIES.add("delivery-already-seen")
        payload = {
            "action": "closed",
            "number": 100,
            "pull_request": {"number": 100, "merged": True, "title": "Merge feature"},
            "repository": {"full_name": "octocat/Hello-World"}
        }
        response = self.client.post(
            "/webhook/github",
            json=payload,
            headers={"X-GitHub-Delivery": "delivery-already-seen"}
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ignored")
        self.assertIn("Duplicate event delivery", data["reason"])

    def test_hmac_signature_verification(self):
        secret = "super_secret_webhook_key"
        body = b'{"action":"closed"}'
        import hashlib, hmac
        valid_mac = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        invalid_mac = "sha256=invalidhashvalue"

        self.assertTrue(verify_signature(secret, body, valid_mac))
        self.assertFalse(verify_signature(secret, body, invalid_mac))
        self.assertFalse(verify_signature(secret, body, None))


class TestObservabilityLogging(unittest.TestCase):
    """Verifies that pr_chronicler.log is created and records events."""

    def test_log_file_written(self):
        logger.info("[TEST_OBSERVABILITY] Validating log file presence and write path.")
        self.assertTrue(LOG_FILE_PATH.exists())
        with open(LOG_FILE_PATH, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("[TEST_OBSERVABILITY]", content)


if __name__ == "__main__":
    unittest.main()
