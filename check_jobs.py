#!/usr/bin/env python3
"""
Workday job alert bot.

Polls each configured company's Workday CXS API for recent job postings,
filters by keyword, diffs against a saved state file, and posts any
brand-new matches to a Discord channel via webhook.

Run on a schedule (see .github/workflows/job-alerts.yml).
"""

import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

CONFIG_PATH = Path(__file__).parent / "config.json"
STATE_PATH = Path(__file__).parent / "state.json"

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

# Jobs requested per keyword search term. Kept at 20 since some tenants
# reject larger single-page requests.
PAGE_SIZE = 20

REQUEST_TIMEOUT = 20

# A job is forgotten once it hasn't appeared in our matched results for this
# many days. Long enough that a posting briefly falling out of search results
# isn't re-alerted; short enough to keep state.json small.
STATE_RETENTION_DAYS = 60

# Workday blocks/empties requests that don't look like a real browser.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Content-Type": "application/json",
    "Accept": "application/json",
}


def load_json(path, default):
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def today_str():
    return datetime.now(timezone.utc).date().isoformat()


def normalize_company_state(raw, today):
    """Return one company's state as {job_id: {"first_seen", "last_seen"}}.

    Older versions of this script stored a plain list of job IDs. Those are
    migrated by treating every ID as seen today, so nothing already alerted
    on gets posted again.
    """
    if not raw:
        return {}
    if isinstance(raw, list):
        return {job_id: {"first_seen": today, "last_seen": today} for job_id in raw}
    return {
        job_id: {
            "first_seen": entry.get("first_seen", today),
            "last_seen": entry.get("last_seen", today),
        }
        for job_id, entry in raw.items()
    }


def prune_company_state(company_state, today):
    """Drop entries whose last_seen is more than STATE_RETENTION_DAYS ago."""
    cutoff = (date.fromisoformat(today) - timedelta(days=STATE_RETENTION_DAYS)).isoformat()
    # ISO dates compare correctly as strings.
    return {
        job_id: entry
        for job_id, entry in sorted(company_state.items())
        if entry["last_seen"] >= cutoff
    }


def fetch_workday_jobs(company, keywords):
    """Search a company's Workday CXS jobs endpoint once per keyword term
    (same as typing that term into the company's own search box) and
    return the combined, deduplicated raw postings.

    This is more reliable than paginating through the default/blank-search
    listing, since large companies (hundreds of open roles) may not sort
    that listing by post date, so relevant postings can sit outside
    whatever page range a blind pagination approach checks. Workday's own
    relevance ranking for a specific search term reliably surfaces
    matching titles near the top of the results for that term.
    """
    tenant = company["tenant"]
    wd_number = company.get("wd_number", "1")
    site = company["site"]
    url = f"https://{tenant}.wd{wd_number}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"

    seen_paths = set()
    all_postings = []

    for term in keywords:
        body = {"appliedFacets": {}, "limit": PAGE_SIZE, "offset": 0, "searchText": term}

        try:
            resp = requests.post(url, json=body, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            print(f"[WARN] Failed to fetch jobs for {company['name']} (term '{term}'): {e}", file=sys.stderr)
            if e.response is not None:
                print(f"[WARN] Response body: {e.response.text[:500]}", file=sys.stderr)
            continue

        for posting in data.get("jobPostings", []):
            path = posting.get("externalPath")
            if path and path not in seen_paths:
                seen_paths.add(path)
                all_postings.append(posting)

    print(f"[INFO] {company['name']}: fetched {len(all_postings)} unique posting(s) across {len(keywords)} search term(s)")
    return all_postings


def fetch_lever_jobs(company):
    """Call a company's Lever public postings API and return raw postings."""
    tenant = company["tenant"]
    url = f"https://api.lever.co/v0/postings/{tenant}?mode=json"

    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        postings = resp.json()
        print(f"[INFO] {company['name']}: fetched {len(postings)} posting(s) from Lever")
        return postings
    except requests.RequestException as e:
        print(f"[WARN] Failed to fetch jobs for {company['name']}: {e}", file=sys.stderr)
        if e.response is not None:
            print(f"[WARN] Response body: {e.response.text[:500]}", file=sys.stderr)
        return []


def fetch_jobs(company, keywords):
    ats = company.get("ats", "workday")
    if ats == "lever":
        return fetch_lever_jobs(company)
    return fetch_workday_jobs(company, keywords)


def get_title(company, posting):
    if company.get("ats") == "lever":
        return posting.get("text", "")
    return posting.get("title", "")


def get_job_id(company, posting):
    if company.get("ats") == "lever":
        return posting.get("id", "")
    return posting.get("externalPath") or posting.get("title", "")


def get_location(company, posting):
    if company.get("ats") == "lever":
        return posting.get("categories", {}).get("location", "Location not listed")
    return posting.get("locationsText", "Location not listed")


def get_posted(company, posting):
    if company.get("ats") == "lever":
        ts = posting.get("createdAt")
        if ts:
            # Lever gives epoch milliseconds
            return time.strftime("%Y-%m-%d", time.gmtime(ts / 1000))
        return ""
    return posting.get("postedOn", "")


def get_days_old(company, posting):
    """Return how many days ago a posting went live, or None if unknown.

    Workday's postedOn is relative text like "Posted Today",
    "Posted Yesterday", "Posted 3 Days Ago", or "Posted 30+ Days Ago" — not
    an exact date. Lever gives an exact epoch timestamp, so that one's a
    straightforward calculation.
    """
    if company.get("ats") == "lever":
        ts = posting.get("createdAt")
        if not ts:
            return None
        posted_dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
        return (datetime.now(timezone.utc) - posted_dt).days

    text = posting.get("postedOn", "")
    if not text:
        return None
    text_lower = text.lower()
    if "today" in text_lower:
        return 0
    if "yesterday" in text_lower:
        return 1
    match = re.search(r"(\d+)\+?\s*days?\s*ago", text_lower)
    if match:
        return int(match.group(1))
    return None  # unrecognized format — treat as unknown rather than guess


def keyword_pattern(keyword):
    """Compile a keyword into a whole-word regex.

    - Matches whole words/phrases only: "lead" matches "Team Lead" but not
      "Leadership", "it" can't match inside "Credit".
    - A keyword written in ALL CAPS (e.g. "IT", "QA") is matched
      case-sensitively, so "IT" matches "IT Analyst" but not the word "it".
      Everything else is case-insensitive.
    """
    keyword = keyword.strip()
    flags = 0 if keyword.isupper() else re.IGNORECASE
    return re.compile(r"(?<!\w)" + re.escape(keyword) + r"(?!\w)", flags)


def matches_any(text, keywords):
    return any(keyword_pattern(kw).search(text) for kw in keywords if kw.strip())


def matches_keywords(title, keywords):
    return matches_any(title, keywords)


def matches_exclusions(title, exclude_keywords):
    return matches_any(title, exclude_keywords)


def is_wanted_title(title, config):
    """Decide whether a job title should be alerted on.

    1. It must match one of `keywords`.
    2. It must not match `exclude_keywords` (seniority: senior, lead, ...).
    3. If it matches `exclude_non_tech_keywords` (credit, banking, sales, ...)
       it's dropped, unless it also matches `tech_keywords`. That keeps
       "Cloud Developer, Banking Payments" while dropping "Credit Analyst".
    """
    if not matches_keywords(title, config.get("keywords", [])):
        return False
    if matches_exclusions(title, config.get("exclude_keywords", [])):
        return False
    if matches_exclusions(title, config.get("exclude_non_tech_keywords", [])):
        return matches_any(title, config.get("tech_keywords", []))
    return True


def matches_location(location, location_keywords):
    if not location_keywords:
        return True  # no filter configured, allow everything
    location_lower = location.lower()
    return any(kw.lower() in location_lower for kw in location_keywords)


def matches_recency(days_old, max_days_old):
    if max_days_old is None:
        return True  # no filter configured, allow everything
    if days_old is None:
        return True  # unknown age — don't risk hiding a real match
    return days_old <= max_days_old


def job_url(company, posting):
    if company.get("ats") == "lever":
        return posting.get("hostedUrl", "")
    tenant = company["tenant"]
    wd_number = company.get("wd_number", "1")
    site = company["site"]
    external_path = posting.get("externalPath", "")
    return f"https://{tenant}.wd{wd_number}.myworkdayjobs.com/en-US/{site}{external_path}"


class DiscordPoster:
    """Posts job alerts to the Discord webhook, with retries.

    - On 429 (rate limited), waits the `retry_after` Discord asks for, then retries.
    - On 5xx or a network error, waits briefly and retries.
    - On other 4xx (bad payload, deleted webhook), gives up straight away.
    - After MAX_CONSECUTIVE_FAILURES failed posts in a row, Discord is treated
      as down and the rest of this run's posts are skipped. Those jobs aren't
      saved to state, so they're retried on the next run.
    """

    MAX_ATTEMPTS = 3
    MAX_CONSECUTIVE_FAILURES = 3
    MAX_RETRY_WAIT = 60  # seconds; never sleep longer than this for one retry
    ERROR_RETRY_WAIT = 2

    def __init__(self, webhook_url):
        self.webhook_url = webhook_url
        self.consecutive_failures = 0
        self.failed = 0
        self.skipped = 0

    @property
    def gave_up(self):
        return self.consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES

    def post(self, company_name, title, location, posted, url):
        """Return True only if Discord confirmed the post."""
        if not self.webhook_url:
            print("[WARN] No DISCORD_WEBHOOK_URL set, not posting (job will be retried next run).", file=sys.stderr)
            self.failed += 1
            return False
        if self.gave_up:
            self.skipped += 1
            return False

        payload = {
            "embeds": [
                {
                    "title": title or "Untitled role",
                    "url": url,
                    "description": f"**{company_name}**\n📍 {location}\n🗓️ {posted}",
                    "color": 3447003,
                }
            ]
        }

        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            wait = None
            try:
                resp = requests.post(self.webhook_url, json=payload, timeout=REQUEST_TIMEOUT)
            except requests.RequestException as e:
                print(f"[WARN] Discord post failed (attempt {attempt}): {e}", file=sys.stderr)
                wait = self.ERROR_RETRY_WAIT
            else:
                if resp.ok:
                    self.consecutive_failures = 0
                    return True
                if resp.status_code == 429:
                    wait = retry_after_seconds(resp)
                    print(f"[WARN] Discord rate limited (attempt {attempt}), retrying in {wait:.1f}s", file=sys.stderr)
                elif resp.status_code >= 500:
                    wait = self.ERROR_RETRY_WAIT
                    print(f"[WARN] Discord returned {resp.status_code} (attempt {attempt})", file=sys.stderr)
                else:
                    print(f"[WARN] Discord rejected the post ({resp.status_code}): {resp.text[:300]}", file=sys.stderr)
                    break  # retrying won't help

            if attempt < self.MAX_ATTEMPTS:
                time.sleep(min(wait, self.MAX_RETRY_WAIT))

        self.failed += 1
        self.consecutive_failures += 1
        if self.gave_up:
            print(f"[WARN] {self.consecutive_failures} Discord posts failed in a row; "
                  "skipping the rest this run.", file=sys.stderr)
        return False


def retry_after_seconds(resp):
    """Seconds to wait from a Discord 429 (JSON body first, then header)."""
    try:
        return float(resp.json().get("retry_after"))
    except (ValueError, TypeError, AttributeError):
        pass
    try:
        return float(resp.headers.get("Retry-After"))
    except (ValueError, TypeError):
        return 5.0


def main():
    config = load_json(CONFIG_PATH, {"keywords": [], "companies": []})
    state = load_json(STATE_PATH, {})

    keywords = config.get("keywords", [])
    location_keywords = config.get("location_keywords", [])
    max_days_old = config.get("max_days_old")
    companies = config.get("companies", [])

    new_count = 0
    today = today_str()
    discord = DiscordPoster(DISCORD_WEBHOOK_URL)

    for company in companies:
        name = company["name"]
        key = f"{company['tenant']}:{company.get('site', company.get('ats', 'default'))}"
        company_state = normalize_company_state(state.get(key), today)

        postings = fetch_jobs(company, keywords)
        matched = [
            p for p in postings
            if is_wanted_title(get_title(company, p), config)
            and matches_location(get_location(company, p), location_keywords)
            and matches_recency(get_days_old(company, p), max_days_old)
        ]
        print(f"[INFO] {name}: {len(matched)} posting(s) matched keywords+location+recency out of {len(postings)} fetched")

        for posting in matched:
            job_id = get_job_id(company, posting)

            if job_id in company_state:
                company_state[job_id]["last_seen"] = today
                continue

            url = job_url(company, posting)
            title = get_title(company, posting)
            print(f"[NEW] {name}: {title} -> {url}")
            # Only remember the job once Discord has confirmed the alert, so a
            # failed post is retried next run instead of being lost.
            if discord.post(name, title, get_location(company, posting), get_posted(company, posting), url):
                company_state[job_id] = {"first_seen": today, "last_seen": today}
                new_count += 1
                time.sleep(1)  # be gentle on the Discord rate limit

        # Entries are only dropped once a job hasn't shown up in our results
        # for STATE_RETENTION_DAYS, so a still-open posting is never forgotten
        # (and re-posted) just because the state grew large.
        state[key] = prune_company_state(company_state, today)

    save_json(STATE_PATH, state)
    print(f"Done. {new_count} new matching posting(s) posted.")
    unsent = discord.failed + discord.skipped
    if unsent:
        # "::warning::" shows up as an annotation on the GitHub Actions run.
        print(f"::warning::{unsent} job alert(s) could not be sent to Discord "
              f"({discord.failed} failed, {discord.skipped} skipped). They'll be retried next run.")


if __name__ == "__main__":
    main()
