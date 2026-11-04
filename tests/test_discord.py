"""Tests for Discord posting and retries (issue #4).

Run with:  python -m unittest discover tests
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import check_jobs  # noqa: E402

ARGS = ("TD Bank", "Software Engineer I", "Toronto", "Posted Today", "https://example.com/job")


def response(status, body=None, headers=None):
    resp = requests.Response()
    resp.status_code = status
    resp._content = json.dumps(body or {}).encode()
    resp.headers.update(headers or {})
    return resp


class DiscordPosterTests(unittest.TestCase):
    def setUp(self):
        sleep = mock.patch.object(check_jobs.time, "sleep")
        self.sleep = sleep.start()
        self.addCleanup(sleep.stop)
        self.poster = check_jobs.DiscordPoster("https://discord.test/webhook")

    def post_with(self, *responses):
        with mock.patch.object(check_jobs.requests, "post", side_effect=list(responses)) as post:
            result = self.poster.post(*ARGS)
        return result, post

    def test_success(self):
        ok, post = self.post_with(response(204))
        self.assertTrue(ok)
        self.assertEqual(post.call_count, 1)

    def test_429_waits_retry_after_then_succeeds(self):
        ok, post = self.post_with(response(429, {"retry_after": 1.5}), response(204))
        self.assertTrue(ok)
        self.assertEqual(post.call_count, 2)
        self.sleep.assert_called_once_with(1.5)

    def test_429_uses_header_when_body_has_no_retry_after(self):
        ok, _ = self.post_with(response(429, headers={"Retry-After": "3"}), response(204))
        self.assertTrue(ok)
        self.sleep.assert_called_once_with(3.0)

    def test_retry_wait_is_capped(self):
        self.post_with(response(429, {"retry_after": 9999}), response(204))
        self.sleep.assert_called_once_with(check_jobs.DiscordPoster.MAX_RETRY_WAIT)

    def test_server_error_retried(self):
        ok, post = self.post_with(response(502), response(204))
        self.assertTrue(ok)
        self.assertEqual(post.call_count, 2)

    def test_network_error_retried(self):
        ok, post = self.post_with(requests.ConnectionError("boom"), response(204))
        self.assertTrue(ok)
        self.assertEqual(post.call_count, 2)

    def test_gives_up_after_max_attempts(self):
        ok, post = self.post_with(response(500), response(500), response(500))
        self.assertFalse(ok)
        self.assertEqual(post.call_count, check_jobs.DiscordPoster.MAX_ATTEMPTS)
        self.assertEqual(self.poster.failed, 1)

    def test_client_error_not_retried(self):
        ok, post = self.post_with(response(404, {"message": "Unknown Webhook"}))
        self.assertFalse(ok)
        self.assertEqual(post.call_count, 1)

    def test_stops_posting_after_repeated_failures(self):
        for _ in range(check_jobs.DiscordPoster.MAX_CONSECUTIVE_FAILURES):
            self.post_with(response(404))
        self.assertTrue(self.poster.gave_up)
        with mock.patch.object(check_jobs.requests, "post") as post:
            self.assertFalse(self.poster.post(*ARGS))
        post.assert_not_called()
        self.assertEqual(self.poster.skipped, 1)

    def test_success_resets_failure_streak(self):
        self.post_with(response(404))
        self.post_with(response(404))
        self.post_with(response(204))
        self.assertEqual(self.poster.consecutive_failures, 0)

    def test_no_webhook_returns_false(self):
        poster = check_jobs.DiscordPoster(None)
        with mock.patch.object(check_jobs.requests, "post") as post:
            self.assertFalse(poster.post(*ARGS))
        post.assert_not_called()


class FailedPostNotSavedTests(unittest.TestCase):
    """A job whose alert failed must not be saved, so the next run retries it."""

    CONFIG = {"keywords": ["engineer"], "location_keywords": [], "max_days_old": None,
              "companies": [{"name": "TD Bank", "ats": "workday", "tenant": "td", "wd_number": "3",
                             "site": "TD_Bank_Careers"}]}
    POSTINGS = [
        {"title": "Software Engineer I", "externalPath": "/job/ok", "locationsText": "Toronto"},
        {"title": "Software Engineer II", "externalPath": "/job/fail", "locationsText": "Toronto"},
    ]

    def test_only_successful_posts_are_saved(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path, state_path = Path(tmp) / "config.json", Path(tmp) / "state.json"
            config_path.write_text(json.dumps(self.CONFIG))
            state_path.write_text("{}")
            with mock.patch.object(check_jobs, "CONFIG_PATH", config_path), \
                 mock.patch.object(check_jobs, "STATE_PATH", state_path), \
                 mock.patch.object(check_jobs, "fetch_jobs", return_value=self.POSTINGS), \
                 mock.patch.object(check_jobs.DiscordPoster, "post", side_effect=[True, False]), \
                 mock.patch.object(check_jobs.time, "sleep"):
                check_jobs.main()
            saved = json.loads(state_path.read_text())["td:TD_Bank_Careers"]
        self.assertIn("/job/ok", saved)
        self.assertNotIn("/job/fail", saved)


if __name__ == "__main__":
    unittest.main()
