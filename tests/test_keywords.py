"""Tests for job title matching (issue #1).

Run with:  python -m unittest discover tests
"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import check_jobs  # noqa: E402

CONFIG = json.loads((ROOT / "config.json").read_text())

# Titles taken from postings the bot has actually seen.
SHOULD_MATCH = [
    "Software Engineer I",
    "Software Engineer II (SDET)",
    "Associate Software Engineer",
    "IT Operations Analyst II",
    "IT Support Analyst IV - TD Securities",
    "Data Engineer I",
    "Java Developer - TD Securities",
    "Summer Intern 2027 - Software Engineering",
    "Business Systems Analyst",
    "QA Analyst co-op",
    "Associate, Data Scientist",
    "Technical Support Coordinator",
    # Non-tech words, but clearly dev roles: kept by tech_keywords.
    "Cloud Application Developer - AWS (Banking Payment Systems)",
    "Intermediate AWS Developer, Wealth Management Technologies",
    "Software Engineer (Java) - Front Office Pricing & Risk",
    "Mobile Software Engineer II, Mobile Delivery CDN banking",
    "Analyst, IT Security",
    "Leadership Development Program - Software Engineer",  # "lead" must not match "Leadership"
]

SHOULD_NOT_MATCH = [
    # Bank-branch / client roles (the bulk of the old noise)
    "Personal Banking Associate",
    "Customer Experience Associate",
    "Client Service Associate - Private Wealth",
    "Private Banking Associate",
    "Mortgage Specialist Associate",
    # "it " used to match inside these
    "Credit Analyst I",
    "Credit Analyst II",
    "Commercial Banking Credit Analyst Internship - Mississauga - Summer 2027",
    # Finance-type analyst roles
    "Actuarial Analyst II",
    "Financial Analyst - General Accounting",
    "Investment Banking Analyst - Technology",
    "Analyst, Fraud Controls",
    "Treasury Analyst",
    # Seniority
    "Senior Mainframe Engineer - TD Securities",
    "Sr. Systems Engineer",
    "Sr Developer, Performance Management",
    "Team Lead, Software Development",
    "Engineering Manager",
    "Staff Software Engineer",
]


class TitleMatchingTests(unittest.TestCase):
    def test_should_match(self):
        for title in SHOULD_MATCH:
            with self.subTest(title=title):
                self.assertTrue(check_jobs.is_wanted_title(title, CONFIG))

    def test_should_not_match(self):
        for title in SHOULD_NOT_MATCH:
            with self.subTest(title=title):
                self.assertFalse(check_jobs.is_wanted_title(title, CONFIG))


class KeywordPatternTests(unittest.TestCase):
    def test_whole_words_only(self):
        self.assertFalse(check_jobs.matches_any("Credit Analyst", ["it"]))
        self.assertFalse(check_jobs.matches_any("Leadership Program", ["lead"]))
        self.assertFalse(check_jobs.matches_any("Staffing Analyst", ["staff"]))
        self.assertTrue(check_jobs.matches_any("Team Lead", ["lead"]))

    def test_all_caps_keyword_is_case_sensitive(self):
        self.assertTrue(check_jobs.matches_any("IT Analyst", ["IT"]))
        self.assertFalse(check_jobs.matches_any("Make it happen", ["IT"]))

    def test_lowercase_keyword_is_case_insensitive(self):
        self.assertTrue(check_jobs.matches_any("SOFTWARE ENGINEER", ["software engineer"]))

    def test_punctuation_around_words(self):
        self.assertTrue(check_jobs.matches_any("Sr. Developer", ["sr"]))
        self.assertTrue(check_jobs.matches_any("Engineer, Cloud (Toronto)", ["engineer"]))

    def test_blank_keywords_ignored(self):
        self.assertFalse(check_jobs.matches_any("Anything", ["", "  "]))


if __name__ == "__main__":
    unittest.main()
