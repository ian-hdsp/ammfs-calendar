"""Entry point: build a subscribable .ics feed from Zeffy event campaigns.

Runs as a scheduled GitHub Actions job that publishes the feed to GitHub
Pages. The same CLI runs locally for development and for the one-time schema
check (see discover.py).

The feed is regenerated whole on every run, so there is no reconciliation and
no state: an event removed in Zeffy simply stops appearing, and subscribers
drop it on their next refresh.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from config import Config
from ics import render_calendar
from mapping import (
    FieldResolver,
    Occurrence,
    campaign_to_occurrences,
    is_archived,
    is_event_campaign,
    occurrence_sort_key,
)
from publish import build_site, subscription_urls, write_local
from zeffy import ZeffyClient, ZeffyError

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("zeffy-ics")

UTC = ZoneInfo("UTC")


def collect_occurrences(cfg: Config, client: ZeffyClient) -> list[Occurrence]:
    resolver = FieldResolver(cfg.zeffy_field_map)
    occurrences: list[Occurrence] = []
    campaigns = 0
    non_events = 0
    archived = 0
    excluded = 0

    exclude = (
        re.compile(cfg.exclude_title_pattern, re.IGNORECASE)
        if cfg.exclude_title_pattern
        else None
    )

    for record in client.iter_campaigns(page_size=cfg.zeffy_page_size):
        campaigns += 1
        if exclude is not None:
            title = str(resolver.get(record, "title", "") or "")
            if exclude.search(title):
                excluded += 1
                continue
        # Archived campaigns are dropped even under sync_all_campaigns: that
        # flag widens which *kinds* of campaign count, not whether retired
        # ones come back.
        if is_archived(record):
            archived += 1
            continue
        if not cfg.sync_all_campaigns and not is_event_campaign(record, resolver):
            non_events += 1
            continue
        occurrences.extend(
            campaign_to_occurrences(
                record,
                resolver,
                default_timezone=cfg.default_timezone,
                default_duration_minutes=cfg.default_duration_minutes,
            )
        )

    log.info(
        "Read %d campaigns (%d non-events, %d archived, %d excluded) -> %d occurrences",
        campaigns, non_events, archived, excluded, len(occurrences),
    )
    return occurrences


def within_window(
    occurrences: list[Occurrence], time_min: datetime, time_max: datetime
) -> list[Occurrence]:
    kept = [o for o in occurrences if time_min <= occurrence_sort_key(o) <= time_max]
    dropped = len(occurrences) - len(kept)
    if dropped:
        log.info("Dropped %d occurrences outside the feed window", dropped)
    return kept


def build_feed(cfg: Config) -> tuple[str, int]:
    """Fetch from Zeffy and render the feed. Returns (feed, event_count)."""
    now = datetime.now(tz=UTC)
    time_min = now - timedelta(days=cfg.past_window_days)
    time_max = now + timedelta(days=cfg.future_window_days)

    client = ZeffyClient(
        api_key=cfg.zeffy_api_key,
        base_url=cfg.zeffy_base_url,
        max_requests_per_minute=cfg.zeffy_max_requests_per_minute,
    )

    occurrences = within_window(collect_occurrences(cfg, client), time_min, time_max)
    occurrences.sort(key=occurrence_sort_key)

    feed = render_calendar(
        occurrences,
        calendar_name=cfg.calendar_name,
        timezone=cfg.default_timezone,
        domain=cfg.uid_domain,
        refresh=cfg.refresh_interval,
        now=now,
    )
    return feed, len(occurrences)


def run(cfg: Config) -> dict:
    """Build the feed and materialise the Pages site directory."""
    cfg.validate(require_publish=True)
    feed, count = build_feed(cfg)
    published = build_site(
        feed,
        site_dir=cfg.site_dir,
        basename=cfg.feed_basename,
        token=cfg.feed_token,
        emit_robots=cfg.emit_robots,
        dry_run=cfg.dry_run,
    )
    result = {"ok": True, "events": count, **published}
    if cfg.pages_base_url:
        result["subscribe"] = subscription_urls(
            cfg.pages_base_url, published["filename"]
        )
    # The filename embeds FEED_TOKEN, which Actions masks in logs. Log the
    # shape of the run, not the secret.
    log.info(
        "Run finished: %d events, %d bytes, digest %s",
        count, published["bytes"], published.get("digest", "-"),
    )
    return result


# --- CLI --------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a subscribable .ics feed from Zeffy event campaigns"
    )
    parser.add_argument("--out", metavar="PATH",
                        help="write the feed to a single local file and stop")
    parser.add_argument("--stdout", action="store_true",
                        help="print the feed instead of publishing it")
    parser.add_argument("--site-dir", metavar="DIR",
                        help="override SITE_DIR, the Pages artifact directory")
    parser.add_argument("--dry-run", action="store_true",
                        help="build and report, but write nothing")
    args = parser.parse_args(argv)

    cfg = Config.from_env()
    if args.dry_run:
        cfg.dry_run = True
    if args.site_dir:
        cfg.site_dir = args.site_dir

    if args.out or args.stdout:
        try:
            cfg.validate(require_publish=False)
            feed, count = build_feed(cfg)
        except (ZeffyError, RuntimeError, ValueError) as exc:
            print(f"Failed: {exc}", file=sys.stderr)
            return 1

        if args.stdout:
            sys.stdout.write(feed)
            return 0

        result = write_local(feed, args.out)
        print(f"Wrote {count} events ({result['bytes']} bytes) to {args.out}")
        return 0

    try:
        result = run(cfg)
    except (ZeffyError, RuntimeError, ValueError) as exc:
        print(f"Failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
