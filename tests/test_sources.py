"""Tests for job sources: Ashby support and the Workday country filter (issue #5).

Response shapes follow Ashby's public posting API
(api.ashbyhq.com/posting-api/job-board/{name}) and Workday's
/wday/cxs/{tenant}/{site}/jobs `facets`.

Run with:  python -m unittest discover tests
"""

import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import check_jobs  # noqa: E402

ASHBY = {"name": "Wealthsimple", "ats": "ashby", "tenant": "wealthsimple"}
ACCENTURE = {"name": "Accenture", "ats": "workday", "tenant": "accenture", "wd_number": "103",
             "site": "AccentureCareers", "country": "Canada"}
CANADA_ID = "a30a87ed25634629aa6c3958aa2b91ea"


def json_response(body, status=200):
    resp = requests.Response()
    resp.status_code = status
    resp._content = json.dumps(body).encode()
    return resp


def ashby_job(**overrides):
    job = {
        "id": "3ab0ddb6-7eda-4ae0-bcfe-28dffa254d42",
        "title": "Software Developer",
        "location": "Toronto",
        "secondaryLocations": [{"location": "Montreal"}],
        "address": {"postalAddress": {"addressCountry": "Canada", "addressLocality": "Toronto"}},
        "isRemote": False,
        "isListed": True,
        "publishedAt": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
        "jobUrl": "https://jobs.ashbyhq.com/wealthsimple/3ab0ddb6-7eda-4ae0-bcfe-28dffa254d42",
    }
    job.update(overrides)
    return job


class AshbyTests(unittest.TestCase):
    def test_fetch_skips_unlisted_jobs(self):
        body = {"jobs": [ashby_job(), ashby_job(id="hidden", isListed=False)]}
        with mock.patch.object(check_jobs.requests, "get", return_value=json_response(body)) as get:
            jobs = check_jobs.fetch_ashby_jobs(ASHBY)
        self.assertEqual([j["id"] for j in jobs], ["3ab0ddb6-7eda-4ae0-bcfe-28dffa254d42"])
        self.assertEqual(get.call_args.args[0], "https://api.ashbyhq.com/posting-api/job-board/wealthsimple")

    def test_fetch_failure_returns_empty(self):
        with mock.patch.object(check_jobs.requests, "get", side_effect=requests.ConnectionError("down")):
            self.assertEqual(check_jobs.fetch_ashby_jobs(ASHBY), [])

    def test_getters(self):
        job = ashby_job()
        self.assertEqual(check_jobs.get_title(ASHBY, job), "Software Developer")
        self.assertEqual(check_jobs.get_job_id(ASHBY, job), job["id"])
        self.assertEqual(check_jobs.job_url(ASHBY, job), job["jobUrl"])
        self.assertEqual(check_jobs.get_days_old(ASHBY, job), 2)
        self.assertRegex(check_jobs.get_posted(ASHBY, job), r"^\d{4}-\d{2}-\d{2}$")

    def test_location_includes_secondary_and_country(self):
        location = check_jobs.get_location(ASHBY, ashby_job())
        self.assertEqual(location, "Toronto; Montreal (Canada)")
        self.assertTrue(check_jobs.matches_location(location, ["canada", "toronto"]))

    def test_location_remote_flag(self):
        location = check_jobs.get_location(ASHBY, ashby_job(location="Canada", secondaryLocations=[], isRemote=True))
        self.assertEqual(location, "Canada · Remote")

    def test_missing_or_bad_published_date(self):
        self.assertIsNone(check_jobs.get_days_old(ASHBY, ashby_job(publishedAt=None)))
        self.assertIsNone(check_jobs.get_days_old(ASHBY, ashby_job(publishedAt="not a date")))
        self.assertEqual(check_jobs.get_days_old(ASHBY, ashby_job(publishedAt="2020-01-01T00:00:00Z")) > 365, True)


class CountryFacetTests(unittest.TestCase):
    NESTED = [{"facetParameter": "locationMainGroup", "values": [
        {"facetParameter": "locationCountry", "values": [
            {"descriptor": "India", "id": "india-id", "count": 900},
            {"descriptor": "Canada", "id": CANADA_ID, "count": 40},
        ]},
        {"facetParameter": "locations", "values": [{"descriptor": "Canada Toronto", "id": "toronto-id", "count": 20}]},
    ]}]
    FLAT = [{"facetParameter": "Location_Country", "values": [{"descriptor": "Canada", "id": CANADA_ID, "count": 5}]},
            {"facetParameter": "jobFamilyGroup", "values": [{"descriptor": "Canada", "id": "wrong", "count": 1}]}]

    def test_nested_shape(self):
        self.assertEqual(check_jobs.find_country_facet(self.NESTED, "Canada"), {"locationCountry": [CANADA_ID]})

    def test_flat_shape_and_ignores_non_country_facets(self):
        self.assertEqual(check_jobs.find_country_facet(self.FLAT, "canada"), {"Location_Country": [CANADA_ID]})

    def test_not_found(self):
        self.assertIsNone(check_jobs.find_country_facet(self.NESTED, "Brazil"))
        self.assertIsNone(check_jobs.find_country_facet(None, "Canada"))

    def test_searches_use_country_filter(self):
        facets = json_response({"facets": self.NESTED, "jobPostings": []})
        results = json_response({"jobPostings": [{"title": "AI Native Developer", "externalPath": "/job/a"}]})
        with mock.patch.object(check_jobs.requests, "post", side_effect=[facets, results, results]) as post:
            postings = check_jobs.fetch_workday_jobs(ACCENTURE, ["developer", "engineer"])
        self.assertEqual(len(postings), 1)
        self.assertEqual(post.call_count, 3)  # 1 facet lookup + 1 per keyword
        for call in post.call_args_list[1:]:
            self.assertEqual(call.kwargs["json"]["appliedFacets"], {"locationCountry": [CANADA_ID]})

    def test_falls_back_to_all_countries(self):
        with mock.patch.object(check_jobs.requests, "post",
                               side_effect=[requests.ConnectionError("x"), json_response({"jobPostings": []})]) as post:
            check_jobs.fetch_workday_jobs(ACCENTURE, ["developer"])
        self.assertEqual(post.call_args_list[1].kwargs["json"]["appliedFacets"], {})

    def test_no_country_means_no_extra_request(self):
        td = {k: v for k, v in ACCENTURE.items() if k != "country"}
        with mock.patch.object(check_jobs.requests, "post", return_value=json_response({"jobPostings": []})) as post:
            check_jobs.fetch_workday_jobs(td, ["developer"])
        self.assertEqual(post.call_count, 1)
        self.assertEqual(post.call_args.kwargs["json"]["appliedFacets"], {})


class ZeroFetchedWarningTests(unittest.TestCase):
    def test_warns_when_nothing_fetched(self):
        out = io.StringIO()
        with mock.patch.object(check_jobs, "fetch_ashby_jobs", return_value=[]), redirect_stdout(out):
            check_jobs.fetch_jobs(ASHBY, [])
        self.assertIn("::warning::Wealthsimple: 0 postings fetched", out.getvalue())

    def test_no_warning_when_jobs_fetched(self):
        out = io.StringIO()
        with mock.patch.object(check_jobs, "fetch_ashby_jobs", return_value=[ashby_job()]), redirect_stdout(out):
            check_jobs.fetch_jobs(ASHBY, [])
        self.assertNotIn("::warning::", out.getvalue())


class ConfigTests(unittest.TestCase):
    def test_config_changes(self):
        config = json.loads((Path(__file__).resolve().parent.parent / "config.json").read_text())
        by_name = {c["name"]: c for c in config["companies"]}
        self.assertEqual(by_name["Wealthsimple"]["ats"], "ashby")
        self.assertEqual(by_name["Accenture"]["country"], "Canada")
        self.assertNotIn("Kyndryl Early Careers", by_name)
        self.assertIn("Kyndryl", by_name)


if __name__ == "__main__":
    unittest.main()
