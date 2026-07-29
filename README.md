# Zeffy → subscribable calendar feed

A scheduled **GitHub Actions** job that publishes your Zeffy **event campaigns**
as a single `.ics` file on **GitHub Pages**, rebuilt every 30 minutes. Anyone
holding the URL can subscribe in Google Calendar, Apple Calendar, Outlook, or
Thunderbird — no accounts, no per-person setup, unlimited subscribers.

There is no cloud provider, no CLI, and no server. The entire deployment is
this repo.

---

## Rotate the API key first

An API key was pasted into a chat transcript during earlier work on this.
Treat it as compromised: in Zeffy go to **Settings → Organization →
Integrations** and regenerate it. The key is read-only, so the exposure is
disclosure of donor and payment data — not modification — but that is still
donor PII.

Nothing in this repo contains a key. It is read from `ZEFFY_API_KEY`, which in
production is a GitHub Actions secret.

---

## Setup

Three steps, all in the GitHub web UI.

**1. Generate a feed token.** This random string becomes part of the published
filename and is the only thing keeping the URL private (see *Who can read the
feed* below). Generate it once:

```bash
python -c "import secrets; print(secrets.token_hex(8))"
```

**2. Add two repository secrets** under **Settings → Secrets and variables →
Actions → Secrets**:

| Secret          | Value                  |
| --------------- | ---------------------- |
| `ZEFFY_API_KEY` | The rotated Zeffy key  |
| `FEED_TOKEN`    | The string from step 1 |

Optionally add repository **variables** (same page, *Variables* tab) to
override `CALENDAR_NAME`, `DEFAULT_TIMEZONE`, `SYNC_ALL_CAMPAIGNS`, or
`ZEFFY_FIELD_MAP`. All have working defaults.

**3. Run it.** Actions → *Publish Zeffy calendar feed* → **Run workflow**. The
workflow enables Pages itself on first run (`configure-pages` with
`enablement: true`), so there is nothing to switch on beforehand.

Your feed URL is then:

```
https://ian-hdsp.github.io/ammfs-calendar/zeffy-events-<FEED_TOKEN>.ics
```

**Actions will not print that URL for you.** `FEED_TOKEN` is a secret, so
GitHub masks it in logs — you will see `zeffy-events-***.ics`. That masking is
working as intended. Assemble the URL yourself from the token you generated.

---

## Who can read the feed

**The feed is public to anyone with the URL. It is not access-controlled.**

This is a property of the goal, not a shortcut. Google Calendar fetches
external feeds anonymously from Google's servers, so any URL it can subscribe
to must be readable without credentials. There is no plan or configuration
that changes this: GitHub's access-controlled Pages (Enterprise Cloud only)
requires each visitor to log in to GitHub, which Google Calendar cannot do.

So the protection here is **URL secrecy**, applied deliberately:

- the filename carries a random token, so the path is not guessable
- `robots.txt` disallows all crawlers, and never names the feed file — putting
  the token in a file at a well-known URL would publish the secret
- no `index.html` is written, so the site root 404s instead of advertising
  that a calendar lives here
- the token lives only in the `FEED_TOKEN` **secret**. It is never committed,
  and Actions masks it everywhere in logs — including in the `tar` listing
  printed by the Pages upload step

### This repo is public — what that does and does not expose

Public: the sync code and the workflow run logs. Anyone can read both.

Not public: the Zeffy API key, the feed token, and therefore the feed URL.
Both are repository secrets, redacted as `***` wherever they would otherwise
appear in a log.

The one rule that follows: **never run `discover.py` in Actions.** It prints
raw campaign JSON, which is not secret-masked and would land in a public log.
Run it locally (see below).

What the feed contains: event names, times, locations, descriptions, and
campaign links. **No donor, payment, or contact data** — the job reads
`/campaigns` and nothing else. Judge the URL-secrecy tradeoff against that.

If the token ever leaks, rotate `FEED_TOKEN`. The old URL 404s on the next run
and every existing subscriber must re-subscribe.

---

## Read this before promising anyone "30 minute" updates

The schedule controls **how fresh the published file is**, not how often
subscribers pull it. Refresh cadence is entirely the client's decision:

| Client              | External feed refresh                   |
| ------------------- | --------------------------------------- |
| **Google Calendar** | **8–24 hours, not adjustable**          |
| Apple Calendar      | Honors the hint; user-settable to 5 min |
| Outlook (web/365)   | ~3 hours                                |
| Thunderbird         | User-settable, minutes                  |

The feed advertises `REFRESH-INTERVAL:PT30M`, but that is a hint. **Google
Calendar ignores it**, and no ICS, CalDAV, or WebDAV design changes this —
Google controls the polling. A 30-minute cron therefore benefits Apple
Calendar and Thunderbird users only.

If a same-day schedule change absolutely must reach Google Calendar
subscribers quickly, a feed cannot do it. Writing directly into a shared
calendar via the Google Calendar API is the only mechanism that can, at the
cost of service-account setup and per-calendar sharing.

### Why not CalDAV?

CalDAV would make this **more** complex, not less. It is a stateful protocol
(RFC 4791) requiring an actual server — Radicale, Baïkal, Nextcloud — rather
than one static file. And it would not help: Google Calendar cannot subscribe
to a third-party CalDAV collection at all; its CalDAV support exists for
accessing Google's own calendars.

---

## Actions minutes

**This repo is public, so Actions minutes are free and unlimited.** At a
30-minute cadence (~1,460 runs/month) the cost is $0. That is the main reason
the job lives here rather than in the private `ammfs` repo — where Pages also
requires a paid plan.

The workflow is nonetheless written to be cheap enough to move into a private
repo unchanged, because private repos meter minutes (2,000/month on Free,
3,000 on Pro), Linux runners bill at 1×, and **every job is rounded up to a
whole minute**. Hence a **single job** rather than the conventional
`build` + `deploy` pair:

| Cadence    | Runs/month | Billed (1 job) | Billed (2 jobs) |
| ---------- | ---------- | -------------- | --------------- |
| **30 min** | ~1,460     | **~1,460**     | ~2,920          |
| Hourly     | ~730       | ~730           | ~1,460          |

Tests are skipped on scheduled runs (`if: github.event_name != 'schedule'`)
for the same reason — they would otherwise re-test unchanged code 1,460 times
a month. To halve the run count, change the cron to `'7 * * * *'`.

---

## What it does

Every 30 minutes:

1. Lists all campaigns from `GET https://api.zeffy.com/api/v1/campaigns`,
   following cursor pagination.
2. Keeps the ones that look like events and expands each into occurrences — a
   Zeffy event can carry multiple dates, and each becomes its own `VEVENT`.
3. Renders the feed into `_site/` and deploys that directory to Pages.

Archived occurrences are skipped. Zeffy leaves them in the campaign's
`occurrences` list -- they were 28 of 197 dates on first contact with the real
account -- so filtering them is what keeps retired tour slots off the
calendar.

The feed is rebuilt **whole** every run, which is what makes this simple: no
state, no reconciliation, no delete logic. An event removed in Zeffy stops
appearing, and subscribers drop it on their next refresh.

### Feed correctness

- Emitted as a **subscription** feed, so `METHOD` is deliberately omitted — a
  feed carrying `METHOD:PUBLISH` is treated by some clients as a one-off
  import or an invitation rather than a live subscription.
- Timed events are emitted in UTC, so no `VTIMEZONE` blocks are needed and
  clients cannot disagree about timezone-database versions.
- All-day events use `VALUE=DATE` with an exclusive `DTEND`, per RFC 5545.
- Lines are folded at 75 octets on UTF-8 character boundaries; `,` `;` `\` and
  newlines are escaped.
- HTML is stripped from descriptions and entities are decoded, so `&amp;`
  does not reach subscribers raw.
- `UID`s are stable across runs (`zeffy-<campaign>:<occurrence>@domain`), so
  clients update events in place instead of duplicating them.
- `.nojekyll` is written so Pages serves the tree verbatim.

---

## Why polling, not webhooks

Zeffy supports webhooks, but they fire on **payment completed** — a donation
or ticket purchase. There is no webhook for "an event campaign was created or
its date moved," which is what this cares about. So it polls: ~48 requests/day
against a documented limit of 100 requests/minute.

---

## The one soft spot: the campaign schema

Zeffy documents *which* resources exist and the auth and pagination model, but
not a frozen field-by-field schema for a campaign. **The field names in
`mapping.py` are educated candidates, not confirmed against live output.**

Each logical field resolves against a list of candidate names, and
`discover.py` reports what your account actually returns. **Run it before
trusting the feed:**

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export ZEFFY_API_KEY=...          # the rotated key; do not commit it
python discover.py --pages 1 --raw
```

**Run this locally, not in Actions.** It prints raw campaign JSON, and Actions
logs are retained and visible to anyone with repo access.

It prints every top-level key, which candidate matched each logical field, and
exactly which events would land in the feed. If something reports `(NOT FOUND)`
or matches the wrong key, pin it as the `ZEFFY_FIELD_MAP` repository variable:

```json
{"start":["eventStartsAt"],"location":["venueAddress"]}
```

Names listed there are tried before the built-ins.

---

## How people subscribe

Give them the URL. **Do not send the file** — an emailed `.ics` is a one-time
import that never updates.

| Client          | Where                                                           |
| --------------- | --------------------------------------------------------------- |
| Google Calendar | Other calendars → **+** → *From URL* (paste the `https://` URL)  |
| Apple Calendar  | File → *New Calendar Subscription* (`webcal://` works directly)  |
| Outlook         | Add calendar → *Subscribe from web*                              |
| Thunderbird     | New Calendar → *On the Network* → iCalendar (ICS)                |

The `webcal://` form makes desktop clients offer "subscribe" rather than
"download"; it is the same file.

Anyone you give the URL to can read the feed and pass it on — treat it like a
shared secret, not a permission.

---

## Configuration

Secrets are marked ✱; everything else can be a repository variable.

| Variable                   | Default                           | Meaning                                      |
| -------------------------- | --------------------------------- | -------------------------------------------- |
| `ZEFFY_API_KEY` ✱          | *(required)*                      | Zeffy read-only API key                      |
| `FEED_TOKEN` ✱             | *(required)*                      | Random token in the published filename       |
| `SITE_DIR`                 | `_site`                           | Pages artifact directory                     |
| `FEED_BASENAME`            | `zeffy-events`                    | Filename stem before the token               |
| `PAGES_BASE_URL`           | *(set by workflow)*               | Only used to print subscribe URLs            |
| `EMIT_ROBOTS`              | `true`                            | Write a disallow-all `robots.txt`            |
| `CALENDAR_NAME`            | `AMFS Zeffy Events`               | Name subscribers see                         |
| `PRODID`                   | `-//AMFS//Zeffy Events//EN`       | Generator tag; not displayed by clients      |
| `DEFAULT_TIMEZONE`         | `America/Los_Angeles`                | Used when a campaign carries no timezone     |
| `DEFAULT_DURATION_MINUTES` | `120`                             | Applied when an event has a start but no end |
| `REFRESH_INTERVAL`         | `PT30M`                           | Advertised refresh hint                      |
| `ZEFFY_FIELD_MAP`          | `{}`                              | JSON field-name overrides                    |
| `SYNC_ALL_CAMPAIGNS`       | `false`                           | `true` includes any dated campaign           |
| `EXCLUDE_TITLE_PATTERN`    | *(none)*                          | Case-insensitive regex; matching titles are dropped |
| `PAST_WINDOW_DAYS`         | `90`                              | How much history to keep in the feed         |
| `FUTURE_WINDOW_DAYS`       | `730`                             | How far ahead to include                     |
| `DRY_RUN`                  | `false`                           | Build and report, publish nothing            |

---

## Development

```bash
pip install -r requirements.txt pytest
python -m pytest

python main.py --stdout                    # print the feed
python main.py --out events.ics            # write a single file
python main.py --site-dir /tmp/site        # build the whole Pages tree
python main.py --dry-run                   # build, report, write nothing
```

100 tests, no network required. They cover date/timezone parsing, multi-date
expansion, RFC 5545 folding and escaping, HTML/entity handling, cursor
pagination including a stuck-cursor guard, and the site builder including
token validation and the guarantee that `robots.txt` never leaks the token.

### Files

| File          | Role                                                            |
| ------------- | --------------------------------------------------------------- |
| `main.py`     | CLI and Actions entry point                                      |
| `zeffy.py`    | Zeffy client: pagination, rate limiting, retry                   |
| `mapping.py`  | Campaign JSON → occurrences; all field-name guessing lives here  |
| `ics.py`      | RFC 5545 feed rendering                                          |
| `publish.py`  | Builds the Pages site directory                                  |
| `discover.py` | One-shot schema probe — run locally before going live            |

The workflow is `.github/workflows/publish.yml`.

### Known limitations

- Campaign field names are unverified against live Zeffy output (see above).
- Subscriber refresh latency is client-controlled and can be a full day on
  Google Calendar (see above).
- Occurrences are emitted as individual `VEVENT`s rather than `RRULE`. Simpler
  and more robust when Zeffy's occurrence list changes, but a weekly series for
  a year is 52 entries.
- The feed is readable by anyone with the URL (see *Who can read the feed*).
- **Zeffy's campaigns API exposes no location field.** There is no venue or
  address anywhere in the payload -- `metadata` is `{}` and `target` is null --
  so every event is published without a `LOCATION`. Nothing here can recover
  it; it would have to come from somewhere other than `/campaigns`.
- Zeffy has no "internal only" flag, so staging campaigns (templates,
  accidental duplicates) are ordinary campaigns with real dates and publish
  like any other. `EXCLUDE_TITLE_PATTERN` is the workaround.
- GitHub disables scheduled workflows in repos with no commit activity for 60
  days. If the feed silently stops updating, check whether the schedule was
  disabled and re-enable it in the Actions tab.
