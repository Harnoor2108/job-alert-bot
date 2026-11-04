"""Tests for state.json handling (issue #2).

Run with:  python -m unittest discover tests
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import check_jobs  # noqa: E402

TODAY = "2026-11-04"


class NormalizeStateTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(check_jobs.normalize_company_state(None, TODAY), {})
        self.assertEqual(check_jobs.normalize_company_state([], TODAY), {})

    def test_migrates_old_list_format(self):
        result = check_jobs.normalize_company_state(["/job/a", "/job/b"], TODAY)
        self.assertEqual(
            result,
            {
                "/job/a": {"first_seen": TODAY, "last_seen": TODAY},
                "/job/b": {"first_seen": TODAY, "last_seen": TODAY},
            },
        )

    def test_keeps_new_format(self):
        raw = {"/job/a": {"first_seen": "2026-10-01", "last_seen": "2026-11-01"}}
        self.assertEqual(check_jobs.normalize_company_state(raw, TODAY), raw)


class PruneStateTests(unittest.TestCase):
    def test_drops_only_entries_not_seen_within_retention(self):
        state = {
            "/job/recent": {"first_seen": "2026-01-01", "last_seen": "2026-11-03"},
            "/job/edge": {"first_seen": "2026-09-05", "last_seen": "2026-09-05"},  # exactly 60 days
            "/job/stale": {"first_seen": "2026-08-01", "last_seen": "2026-09-04"},  # 61 days
        }
        pruned = check_jobs.prune_company_state(state, TODAY)
        self.assertEqual(set(pruned), {"/job/recent", "/job/edge"})

    def test_no_count_cap(self):
        # The old code kept a random 300 IDs; large companies must keep everything recent.
        state = {f"/job/{i}": {"first_seen": TODAY, "last_seen": TODAY} for i in range(1000)}
        self.assertEqual(len(check_jobs.prune_company_state(state, TODAY)), 1000)


class MainLoopTests(unittest.TestCase):
    """Run main() end to end with fake fetches and a temp state file."""

    COMPANY = {"name": "TD Bank", "ats": "workday", "tenant": "td", "wd_number": "3", "site": "TD_Bank_Careers"}
    CONFIG = {"keywords": ["engineer"], "exclude_keywords": [], "location_keywords": [], "max_days_old": None,
              "companies": [COMPANY]}

    def run_main(self, state, postings):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            state_path = Path(tmp) / "state.json"
            config_path.write_text(json.dumps(self.CONFIG))
            state_path.write_text(json.dumps(state))
            with mock.patch.object(check_jobs, "CONFIG_PATH", config_path), \
                 mock.patch.object(check_jobs, "STATE_PATH", state_path), \
                 mock.patch.object(check_jobs, "fetch_jobs", return_value=postings), \
                 mock.patch.object(check_jobs.DiscordPoster, "post", return_value=True) as post, \
                 mock.patch.object(check_jobs, "today_str", return_value=TODAY), \
                 mock.patch.object(check_jobs.time, "sleep"):
                check_jobs.main()
                return post, json.loads(state_path.read_text())

    @staticmethod
    def posting(path):
        return {"title": "Software Engineer I", "externalPath": path, "locationsText": "Toronto", "postedOn": "Posted Today"}

    def test_migration_from_list_does_not_repost(self):
        old_ids = [f"/job/{i}" for i in range(300)]
        postings = [self.posting(p) for p in old_ids]
        post, new_state = self.run_main({"td:TD_Bank_Careers": old_ids}, postings)
        post.assert_not_called()
        self.assertEqual(len(new_state["td:TD_Bank_Careers"]), 300)

    def test_new_job_posted_once_and_recorded(self):
        post, state = self.run_main({}, [self.posting("/job/new")])
        self.assertEqual(post.call_count, 1)
        self.assertEqual(state["td:TD_Bank_Careers"]["/job/new"], {"first_seen": TODAY, "last_seen": TODAY})

        post, state = self.run_main(state, [self.posting("/job/new")])
        post.assert_not_called()

    def test_seen_job_updates_last_seen_keeps_first_seen(self):
        state = {"td:TD_Bank_Careers": {"/job/a": {"first_seen": "2026-10-01", "last_seen": "2026-10-20"}}}
        post, state = self.run_main(state, [self.posting("/job/a")])
        post.assert_not_called()
        self.assertEqual(state["td:TD_Bank_Careers"]["/job/a"], {"first_seen": "2026-10-01", "last_seen": TODAY})

    def test_jobs_missing_from_results_are_kept_until_retention(self):
        state = {"td:TD_Bank_Careers": {"/job/gone": {"first_seen": "2026-10-01", "last_seen": "2026-10-30"}}}
        _, state = self.run_main(state, [])
        self.assertIn("/job/gone", state["td:TD_Bank_Careers"])


if __name__ == "__main__":
    unittest.main()
