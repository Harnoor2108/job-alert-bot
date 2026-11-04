# Job Alert Bot

Monitors Workday-hosted careers pages and pings a Discord channel the moment
a new job matching your keywords is posted.

## Setup (one-time)

1. **Create a GitHub repo** (private is fine) and push these files to it:
   - `config.json`
   - `check_jobs.py`
   - `requirements.txt`
   - `.github/workflows/job-alerts.yml`
   - `README.md` (this file)

2. **Create a Discord webhook:**
   - In your Discord server, go to the channel you want alerts in
   - Channel Settings → Integrations → Webhooks → New Webhook
   - Copy the webhook URL

3. **Add the webhook as a GitHub secret** (don't put it directly in the code):
   - In your repo: Settings → Secrets and variables → Actions → New repository secret
   - Name: `DISCORD_WEBHOOK_URL`
   - Value: paste the webhook URL

4. **Commit `state.json`** — an empty file is fine to start:
   ```
   echo "{}" > state.json
   git add state.json && git commit -m "init state"
   ```
   Without this, the first run will treat every existing job as "new" and
   flood your Discord channel.

5. That's it. The workflow runs every 30 minutes automatically. You can also
   trigger it manually from the repo's **Actions** tab → "Job Alerts" →
   "Run workflow" to test it right away.

## Adding more companies

`config.json` covers Workday-based companies (TD, CIBC, BMO, Intact,
Manulife, Sun Life, Desjardins, Interac, Moneris, Kyndryl, Equifax,
TransUnion, Accenture), one Lever-based company (PointClickCare) and one
Ashby-based company (Wealthsimple). Each entry has an `"ats"` field telling
the script which API shape to expect: `"workday"` (the default if omitted),
`"lever"` or `"ashby"`.

**Adding a Workday company** needs three things: `tenant`, `wd_number`, and
`site`. Fastest way to find them, about a minute per company:

1. Go to the company's careers page and browse to job listings until the
   URL looks like:
   `https://COMPANY.wdN.myworkdayjobs.com/en-US/SITE_NAME/job/...`
2. Read off the three pieces:
   - `tenant` = the part before `.wd` (e.g. `td`, `manulife`)
   - `wd_number` = the digit right after `wd` (usually `1`, `3`, `5`, or `10`)
   - `site` = the segment right after the locale (e.g. `TD_Bank_Careers`,
     `MFCJH_Jobs`)
3. Add an entry:
   ```json
   {
     "name": "Some Company",
     "ats": "workday",
     "tenant": "somecompany",
     "wd_number": "1",
     "site": "SomeCompanySite"
   }
   ```

**Adding a Lever company** only needs the tenant slug — check by going to
`jobs.lever.co/COMPANY` (it'll redirect there from most Lever-powered career
pages). Add:
```json
{
  "name": "Some Company",
  "ats": "lever",
  "tenant": "somecompany"
}
```

**Adding an Ashby company** also only needs the slug from the job board URL,
`jobs.ashbyhq.com/COMPANY` (Wealthsimple is `wealthsimple`):
```json
{
  "name": "Some Company",
  "ats": "ashby",
  "tenant": "somecompany"
}
```

**Global Workday companies:** add `"country": "Canada"` to a Workday entry
to only search that company's Canadian jobs, using Workday's own country
filter. Without it, a huge employer like Accenture fills every result page
with jobs from other countries before any Canadian ones show up. If the
filter can't be found, the bot logs a warning and searches all countries.

If a company ever shows **0 postings fetched**, the Actions run shows a
warning. That usually means it moved to a different job board, so check its
careers page and update its entry.

**If a company's URL doesn't match any of these patterns**, it's on a different ATS
(Taleo, iCIMS, SuccessFactors, Greenhouse, etc.) and would need a new fetcher
function added to `check_jobs.py` — happy to help build one if a specific
company turns out to matter a lot to you. Confirmed NOT on Workday or Lever
from your original list: Canada Life, Bell, Veeva Systems, CGI, Ontario
Public Service, City of Toronto, Metrolinx (worth double-checking the last
four yourself since they weren't fully confirmed either way).

## Separate channels per job type

Alerts are sorted into channels by job title, using `categories` in
`config.json`. The first category whose keywords match the title wins, so
the order matters:

| Category | Example titles | Secret |
|---|---|---|
| `data-ai-jobs` | Data Engineer, ML Engineer, Data Scientist, BI Analyst | `DISCORD_WEBHOOK_DATA` |
| `dev-jobs` | Software Engineer, Developer, QA/Quality Engineer, SDET | `DISCORD_WEBHOOK_DEV` |
| `it-jobs` | IT Operations/Support Analyst, Security Analyst, Technical Support | `DISCORD_WEBHOOK_IT` |
| `analyst-jobs` | Business Analyst, Business Systems Analyst, Functional Analyst | `DISCORD_WEBHOOK_ANALYST` |

**To set up a channel:**

1. In Discord, create the channel (e.g. `#dev-jobs`), then go to Channel
   Settings → Integrations → Webhooks → New Webhook, and copy its URL.
2. In GitHub: Settings → Secrets and variables → Actions → New repository
   secret. Use the secret name from the table above, with the webhook URL
   as the value.

Until a category's secret exists, its jobs keep going to the default channel
(`DISCORD_WEBHOOK_URL`), so you can set channels up one at a time. Jobs that
match no category also go to the default channel.

**Role pings (optional):** to ping a role (e.g. `@Dev`) for a channel's
alerts, enable Developer Mode in Discord (User Settings → Advanced),
right-click the role in Server Settings → Roles → Copy Role ID, and put it
in that category's `ping_role_id`. Only that role is ever pinged, never
`@everyone`.

## Location filtering

`location_keywords` is matched against each job's location. When a Workday
job is posted in several places, its search result only says "2 Locations"
(or 3, 5...). For those, the bot opens the job's detail page to get the real
city names before filtering. If the detail page can't be loaded, the job is
let through and shown as "Multiple locations (details unavailable)" rather
than silently hidden.

## Tuning keyword matching

All keyword lists match **whole words or phrases** in the job title, so
`"lead"` matches "Team Lead" but not "Leadership", and `"it"` can never
match inside "Credit". Keywords are case-insensitive, except ones written in
ALL CAPS (`"IT"`, `"QA"`, `"AI"`), which must appear in capitals. That's how
`"IT"` matches "IT Analyst" without matching the word "it".

A title is alerted on when:

1. it matches something in `keywords` (also used as the search terms sent to
   Workday), **and**
2. it matches nothing in `exclude_keywords` (seniority: senior, lead,
   manager...), **and**
3. if it matches `exclude_non_tech_keywords` (credit, banking, sales,
   fraud...), it must also match `tech_keywords`. This keeps "Cloud Developer,
   Banking Payments" but drops "Credit Analyst" and "Personal Banking
   Associate".

If you see a non-tech role slip through, add a word from its title to
`exclude_non_tech_keywords`. If a real tech role gets dropped, add a word
from it to `tech_keywords`. Run `python -m unittest discover tests` after
changing the lists, and add the title to `tests/test_keywords.py` so it
stays fixed.

## Notes

- GitHub Actions free tier gives you plenty of minutes for a 30-minute cron
  on a script this lightweight.
- `state.json` records, per company, each alerted job's `first_seen` and
  `last_seen` dates. A job is only forgotten after it hasn't appeared in the
  matched results for 60 days (`STATE_RETENTION_DAYS` in `check_jobs.py`),
  so still-open postings are never re-alerted. Older list-format state files
  are migrated automatically on the next run without re-posting anything.
- A job is only saved to `state.json` after Discord confirms its alert. If
  Discord is rate limiting (429) or briefly down, each post is retried up to
  3 times. Anything still unsent is retried on the next run, and the Actions
  run shows a warning with the count. After 3 failed posts in a row the bot
  stops posting for that run instead of hammering Discord.
- Run the tests with `python -m unittest discover tests`.
- If a run fails to fetch a company's page (rate limiting, temporary
  outage), it just skips that company for the run and logs a warning to
  the Actions log — it won't crash the whole workflow.
- To change how often the bot runs, edit the `cron` line in
  `.github/workflows/job-alerts.yml`. Runs never overlap, and the state
  commit is made by `github-actions[bot]` and rebased onto `main` if `main`
  changed during the run.
- The **Tests** workflow runs the test suite and validates `config.json` on
  every pull request, so a typo in the config is caught before it breaks the bot.
