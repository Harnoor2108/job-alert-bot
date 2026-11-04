"""Tests for routing alerts to separate Discord channels (issue #9).

Run with:  python -m unittest discover tests
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import check_jobs  # noqa: E402

CONFIG = json.loads((ROOT / "config.json").read_text())

EXPECTED = {
    "Software Engineer II": "dev-jobs",
    "Associate Software Engineer": "dev-jobs",
    "Consultant, Full Stack Developer": "dev-jobs",
    "Quality Engineer I": "dev-jobs",
    "Back-end Software Engineer (Kotlin)": "dev-jobs",
    "ServiceNow Associate Software Engineer": "dev-jobs",
    "Data Engineer II": "data-ai-jobs",
    "Machine Learning Engineer": "data-ai-jobs",
    "Data Scientist II": "data-ai-jobs",
    "Business Intelligence Analyst": "data-ai-jobs",
    "Developer - Data, AI, Platform & Support": "data-ai-jobs",
    "IT Operations Analyst II": "it-jobs",
    "IT Support Analyst IV": "it-jobs",
    "Security Platform Analyst": "it-jobs",
    "Technical Support Coordinator": "it-jobs",
    "Business Systems Analyst": "analyst-jobs",
    "Business Analyst": "analyst-jobs",
    "Salesforce Functional Analyst": "analyst-jobs",
    "TDS Operations Analyst II": "analyst-jobs",
}


def ok():
    resp = requests.Response()
    resp.status_code = 204
    resp._content = b""
    return resp


class CategorizeTests(unittest.TestCase):
    def test_real_titles(self):
        for title, expected in EXPECTED.items():
            with self.subTest(title=title):
                category = check_jobs.categorize(title, CONFIG)
                self.assertIsNotNone(category)
                self.assertEqual(category["name"], expected)

    def test_no_match_returns_none(self):
        self.assertIsNone(check_jobs.categorize("Branch Manager", CONFIG))

    def test_no_categories_configured(self):
        self.assertIsNone(check_jobs.categorize("Software Engineer", {}))

    def test_first_matching_category_wins(self):
        config = {"categories": [{"name": "a", "keywords": ["engineer"]}, {"name": "b", "keywords": ["engineer"]}]}
        self.assertEqual(check_jobs.categorize("Engineer", config)["name"], "a")


class DestinationTests(unittest.TestCase):
    def test_uses_category_secret(self):
        category = {"webhook_env": "DISCORD_WEBHOOK_DEV", "ping_role_id": "123"}
        with mock.patch.dict("os.environ", {"DISCORD_WEBHOOK_DEV": "https://discord.test/dev"}):
            self.assertEqual(check_jobs.category_destination(category), ("https://discord.test/dev", "123"))

    def test_missing_secret_falls_back_to_default(self):
        category = {"webhook_env": "DISCORD_WEBHOOK_DEV", "ping_role_id": ""}
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(check_jobs.category_destination(category), (None, None))

    def test_empty_secret_falls_back_to_default(self):
        # GitHub passes an empty string for a secret that doesn't exist.
        category = {"webhook_env": "DISCORD_WEBHOOK_DEV"}
        with mock.patch.dict("os.environ", {"DISCORD_WEBHOOK_DEV": ""}):
            self.assertEqual(check_jobs.category_destination(category), (None, None))

    def test_no_category(self):
        self.assertEqual(check_jobs.category_destination(None), (None, None))


class PosterRoutingTests(unittest.TestCase):
    ARGS = ("TD Bank", "Software Engineer I", "Toronto", "Posted Today", "https://example.com/job")

    def setUp(self):
        sleep = mock.patch.object(check_jobs.time, "sleep")
        sleep.start()
        self.addCleanup(sleep.stop)
        self.poster = check_jobs.DiscordPoster("https://discord.test/default")

    def test_posts_to_channel_webhook(self):
        with mock.patch.object(check_jobs.requests, "post", return_value=ok()) as post:
            self.assertTrue(self.poster.post(*self.ARGS, webhook_url="https://discord.test/dev"))
        self.assertEqual(post.call_args.args[0], "https://discord.test/dev")
        self.assertNotIn("content", post.call_args.kwargs["json"])

    def test_defaults_to_main_webhook(self):
        with mock.patch.object(check_jobs.requests, "post", return_value=ok()) as post:
            self.poster.post(*self.ARGS)
        self.assertEqual(post.call_args.args[0], "https://discord.test/default")

    def test_role_ping_only_pings_that_role(self):
        with mock.patch.object(check_jobs.requests, "post", return_value=ok()) as post:
            self.poster.post(*self.ARGS, webhook_url="https://discord.test/dev", ping_role_id="987654321")
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["content"], "<@&987654321>")
        self.assertEqual(payload["allowed_mentions"], {"roles": ["987654321"]})

    def test_one_broken_channel_does_not_stop_others(self):
        bad = requests.Response()
        bad.status_code = 404
        bad._content = b'{"message": "Unknown Webhook"}'
        with mock.patch.object(check_jobs.requests, "post", return_value=bad):
            for _ in range(check_jobs.DiscordPoster.MAX_CONSECUTIVE_FAILURES):
                self.poster.post(*self.ARGS, webhook_url="https://discord.test/deleted")
        with mock.patch.object(check_jobs.requests, "post", return_value=ok()) as post:
            self.assertFalse(self.poster.post(*self.ARGS, webhook_url="https://discord.test/deleted"))
            self.assertTrue(self.poster.post(*self.ARGS, webhook_url="https://discord.test/dev"))
        self.assertEqual(post.call_count, 1)


class EndToEndRoutingTests(unittest.TestCase):
    def test_main_routes_each_job(self):
        config = {
            "keywords": ["engineer", "analyst", "developer"],
            "location_keywords": [],
            "max_days_old": None,
            "categories": CONFIG["categories"],
            "companies": [{"name": "TD Bank", "ats": "workday", "tenant": "td", "wd_number": "3",
                           "site": "TD_Bank_Careers"}],
        }
        postings = [
            {"title": "Software Engineer I", "externalPath": "/job/dev", "locationsText": "Toronto"},
            {"title": "Data Engineer I", "externalPath": "/job/data", "locationsText": "Toronto"},
            {"title": "Business Systems Analyst", "externalPath": "/job/ba", "locationsText": "Toronto"},
        ]
        env = {"DISCORD_WEBHOOK_DEV": "https://discord.test/dev", "DISCORD_WEBHOOK_DATA": "https://discord.test/data"}
        with tempfile.TemporaryDirectory() as tmp:
            config_path, state_path = Path(tmp) / "config.json", Path(tmp) / "state.json"
            config_path.write_text(json.dumps(config))
            state_path.write_text("{}")
            with mock.patch.object(check_jobs, "CONFIG_PATH", config_path), \
                 mock.patch.object(check_jobs, "STATE_PATH", state_path), \
                 mock.patch.object(check_jobs, "DISCORD_WEBHOOK_URL", "https://discord.test/default"), \
                 mock.patch.object(check_jobs, "fetch_jobs", return_value=postings), \
                 mock.patch.dict("os.environ", env, clear=True), \
                 mock.patch.object(check_jobs.requests, "post", return_value=ok()) as post, \
                 mock.patch.object(check_jobs.time, "sleep"):
                check_jobs.main()
        sent_to = sorted(call.args[0] for call in post.call_args_list)
        # The analyst channel has no webhook yet, so that job goes to the default channel.
        self.assertEqual(sent_to, ["https://discord.test/data", "https://discord.test/default", "https://discord.test/dev"])


if __name__ == "__main__":
    unittest.main()
