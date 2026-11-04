"""Tests for multi-location Workday jobs (issue #3).

The detail responses below follow the shape of Workday's
/wday/cxs/{tenant}/{site}/job/... endpoint (jobPostingInfo.location plus
jobPostingInfo.additionalLocations).

Run with:  python -m unittest discover tests
"""

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import check_jobs  # noqa: E402

TD = {"name": "TD Bank", "ats": "workday", "tenant": "td", "wd_number": "3", "site": "TD_Bank_Careers"}
LOCATION_KEYWORDS = ["canada", "ontario", ", can", "toronto"]


def detail_response(location, additional=None, status=200):
    resp = requests.Response()
    resp.status_code = status
    body = {"jobPostingInfo": {"title": "Software Engineer II", "location": location,
                               "additionalLocations": additional or []}}
    resp._content = json.dumps(body).encode()
    return resp


def multi_posting(path="/job/Toronto-Ontario/Software-Engineer-II_R_1", n=2):
    return {"title": "Software Engineer II", "externalPath": path, "locationsText": f"{n} Locations"}


class MultiLocationTests(unittest.TestCase):
    def setUp(self):
        check_jobs._workday_details_cache.clear()

    def test_single_location_unchanged_and_no_detail_fetch(self):
        posting = {"title": "x", "externalPath": "/job/a", "locationsText": "Toronto, Ontario"}
        with mock.patch.object(check_jobs.requests, "get") as get:
            self.assertEqual(check_jobs.get_location(TD, posting), "Toronto, Ontario")
        get.assert_not_called()

    def test_multi_location_with_toronto_matches(self):
        resp = detail_response("New York, New York", ["Toronto, Ontario"])
        with mock.patch.object(check_jobs.requests, "get", return_value=resp) as get:
            location = check_jobs.get_location(TD, multi_posting())
        self.assertEqual(location, "New York, New York; Toronto, Ontario")
        self.assertTrue(check_jobs.matches_location(location, LOCATION_KEYWORDS))
        get.assert_called_once()
        self.assertEqual(
            get.call_args.args[0],
            "https://td.wd3.myworkdayjobs.com/wday/cxs/td/TD_Bank_Careers/job/Toronto-Ontario/Software-Engineer-II_R_1",
        )

    def test_multi_location_us_only_is_filtered_out(self):
        resp = detail_response("New York, New York", ["Mount Laurel, New Jersey"])
        with mock.patch.object(check_jobs.requests, "get", return_value=resp):
            location = check_jobs.get_location(TD, multi_posting())
        self.assertFalse(check_jobs.matches_location(location, LOCATION_KEYWORDS))

    def test_detail_failure_lets_job_through(self):
        with mock.patch.object(check_jobs.requests, "get", side_effect=requests.ConnectionError("down")):
            location = check_jobs.get_location(TD, multi_posting())
        self.assertEqual(location, check_jobs.MULTI_LOCATION_UNKNOWN)
        self.assertTrue(check_jobs.matches_location(location, LOCATION_KEYWORDS))

    def test_detail_http_error_lets_job_through(self):
        with mock.patch.object(check_jobs.requests, "get", return_value=detail_response("", status=404)):
            location = check_jobs.get_location(TD, multi_posting())
        self.assertEqual(location, check_jobs.MULTI_LOCATION_UNKNOWN)

    def test_details_fetched_once_per_job_per_run(self):
        resp = detail_response("Toronto, Ontario", ["Montreal, Quebec"])
        with mock.patch.object(check_jobs.requests, "get", return_value=resp) as get:
            check_jobs.get_location(TD, multi_posting())  # filter
            check_jobs.get_location(TD, multi_posting())  # Discord post
        get.assert_called_once()

    def test_duplicates_and_blanks_removed(self):
        info = {"location": "Toronto, Ontario", "additionalLocations": ["Toronto, Ontario", "", None, "Ottawa, Ontario"]}
        self.assertEqual(check_jobs.workday_locations(info), ["Toronto, Ontario", "Ottawa, Ontario"])

    def test_locations_text_variants(self):
        for text in ["2 Locations", "10 locations", " 3 Locations "]:
            with self.subTest(text=text):
                self.assertTrue(check_jobs.MULTI_LOCATION_RE.match(text))
        for text in ["Toronto, Ontario", "Locations", "Remote - Canada"]:
            with self.subTest(text=text):
                self.assertFalse(check_jobs.MULTI_LOCATION_RE.match(text))

    def test_lever_untouched(self):
        lever = {"name": "PointClickCare", "ats": "lever", "tenant": "pointclickcare"}
        posting = {"categories": {"location": "Mississauga, Ontario"}}
        with mock.patch.object(check_jobs.requests, "get") as get:
            self.assertEqual(check_jobs.get_location(lever, posting), "Mississauga, Ontario")
        get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
