# Job Alert Bot

Monitors Workday-hosted careers pages and pings a Discord channel the moment
a new job matching your keywords is posted.

## Setup (one-time)

1. **Create a GitHub repo** (private is fine) and push these files to it:
   - `config.json`
   - `check_jobs.py`
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

5. That's it. The workflow runs every 15 minutes automatically. You can also
   trigger it manually from the repo's **Actions** tab → "Job Alerts" →
   "Run workflow" to test it right away.

## Adding more companies

`config.json` currently includes TD, Intact, Manulife, and Sun Life —
confirmed Workday tenants. To add another company, you need three things:
`tenant`, `wd_number`, and `site`. Here's the fastest way to find them,
takes about a minute per company:

1. Go to the company's careers page and search/browse to their job listings
   until the URL looks like:
   `https://COMPANY.wdN.myworkdayjobs.com/en-US/SITE_NAME/job/...`
2. Read off the three pieces:
   - `tenant` = the part before `.wd` (e.g. `td`, `manulife`)
   - `wd_number` = the digit right after `wd` (usually `1`, `3`, or `5`)
   - `site` = the segment right after the tenant (e.g. `TD_Bank_Careers`,
     `MFCJH_Jobs`)
3. Add a new entry to the `companies` array in `config.json`:
   ```json
   {
     "name": "Some Company",
     "tenant": "somecompany",
     "wd_number": "1",
     "site": "SomeCompanySite"
   }
   ```

**If the URL doesn't say `myworkdayjobs.com`, that company isn't on Workday**
and this script won't work for it — it'd need a different scraper for
whatever ATS they use (Taleo, iCIMS, SuccessFactors, Greenhouse, Lever,
etc.). From your list, likely non-Workday: CGI, Accenture, IBM, Cognizant,
Infosys, TCS, DXC, Ontario Public Service, City of Toronto, Metrolinx, TTC,
Government of Canada. Worth checking each — happy to help build additional
scrapers for whichever of those turn out to matter most once you've applied
to the Workday-based ones.

## Tuning keyword matching

`config.json`'s `keywords` list does a simple case-insensitive substring
match against each job title. Loosen or tighten it as you see how much
noise you get — e.g. `"it "` (with a trailing space) is there to catch "IT
Analyst" without also matching every word containing "it".

## Notes

- GitHub Actions free tier gives you plenty of minutes for a 15-minute cron
  on a script this lightweight.
- The state file caps at the last 300 seen job IDs per company to keep the
  repo small — plenty of headroom for any single company's posting volume.
- If a run fails to fetch a company's page (rate limiting, temporary
  outage), it just skips that company for the run and logs a warning to
  the Actions log — it won't crash the whole workflow.