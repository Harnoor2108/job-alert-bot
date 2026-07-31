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
import sys
import time
from pathlib import Path

import requests

CONFIG_PATH = Path(__file__).parent / "config.json"
STATE_PATH = Path(__file__).parent / "state.json"

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

# How many of the most recent postings to pull per company per run.
# Workday's default sort is newest-first, so this is plenty to catch
# anything new since the last run (every 15-30 min).
# NOTE: Workday's CXS API commonly rejects limit values above 20 with a 400.
FETCH_LIMIT = 20

REQUEST_TIMEOUT = 20

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


def fetch_workday_jobs(company):
    """Call a company's Workday CXS jobs endpoint and return raw postings."""
    tenant = company["tenant"]
    wd_number = company.get("wd_number", "1")
    site = company["site"]

    url = f"https://{tenant}.wd{wd_number}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
    body = {"appliedFacets": {}, "limit": FETCH_LIMIT, "offset": 0, "searchText": ""}

    try:
        resp = requests.post(url, json=body, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        postings = data.get("jobPostings", [])
        print(f"[INFO] {company['name']}: fetched {len(postings)} posting(s) (total available: {data.get('total', '?')})")
        return postings
    except requests.RequestException as e:
        print(f"[WARN] Failed to fetch jobs for {company['name']}: {e}", file=sys.stderr)
        if e.response is not None:
            print(f"[WARN] Response body: {e.response.text[:500]}", file=sys.stderr)
        return []


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


def fetch_jobs(company):
    ats = company.get("ats", "workday")
    if ats == "lever":
        return fetch_lever_jobs(company)
    return fetch_workday_jobs(company)


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


def matches_keywords(title, keywords):
    title_lower = title.lower()
    return any(kw.lower() in title_lower for kw in keywords)


def matches_exclusions(title, exclude_keywords):
    title_lower = title.lower()
    return any(kw.lower() in title_lower for kw in exclude_keywords)


def matches_location(location, location_keywords):
    if not location_keywords:
        return True  # no filter configured, allow everything
    location_lower = location.lower()
    return any(kw.lower() in location_lower for kw in location_keywords)


def job_url(company, posting):
    if company.get("ats") == "lever":
        return posting.get("hostedUrl", "")
    tenant = company["tenant"]
    wd_number = company.get("wd_number", "1")
    site = company["site"]
    external_path = posting.get("externalPath", "")
    return f"https://{tenant}.wd{wd_number}.myworkdayjobs.com/en-US/{site}{external_path}"


def post_to_discord(company_name, title, location, posted, url):
    if not DISCORD_WEBHOOK_URL:
        print("[WARN] No DISCORD_WEBHOOK_URL set, skipping Discord post.", file=sys.stderr)
        return

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

    try:
        resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[WARN] Failed to post to Discord: {e}", file=sys.stderr)


def main():
    config = load_json(CONFIG_PATH, {"keywords": [], "companies": []})
    state = load_json(STATE_PATH, {})

    keywords = config.get("keywords", [])
    exclude_keywords = config.get("exclude_keywords", [])
    location_keywords = config.get("location_keywords", [])
    companies = config.get("companies", [])

    new_count = 0

    for company in companies:
        name = company["name"]
        key = f"{company['tenant']}:{company.get('site', company.get('ats', 'default'))}"
        seen_ids = set(state.get(key, []))

        postings = fetch_jobs(company)
        matched = [
            p for p in postings
            if matches_keywords(get_title(company, p), keywords)
            and not matches_exclusions(get_title(company, p), exclude_keywords)
            and matches_location(get_location(company, p), location_keywords)
        ]
        print(f"[INFO] {name}: {len(matched)} posting(s) matched keywords+location out of {len(postings)} fetched")

        current_ids = []
        for posting in matched:
            job_id = get_job_id(company, posting)
            current_ids.append(job_id)

            if job_id not in seen_ids:
                url = job_url(company, posting)
                title = get_title(company, posting)
                print(f"[NEW] {name}: {title} -> {url}")
                post_to_discord(name, title, get_location(company, posting), get_posted(company, posting), url)
                new_count += 1
                time.sleep(1)  # be gentle on the Discord rate limit

        # Keep the union of old + current so we don't lose history if a
        # posting temporarily drops off the first page of results.
        state[key] = list(seen_ids.union(current_ids))[-300:]

    save_json(STATE_PATH, state)
    print(f"Done. {new_count} new matching posting(s) found.")


if __name__ == "__main__":
    main()
