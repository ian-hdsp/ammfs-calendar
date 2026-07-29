"""Configuration, read from the environment.

Nothing here has a secret as a default. In production the Zeffy key and the
feed token come from GitHub Actions repository secrets; locally they come from
a .env that is never committed.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


@dataclass
class Config:
    # --- Zeffy ---
    zeffy_api_key: str = ""
    zeffy_base_url: str = "https://api.zeffy.com/api/v1"
    # Zeffy documents 100 requests/minute. Stay under it.
    zeffy_max_requests_per_minute: int = 90
    zeffy_page_size: int = 100
    # Overrides for the field-name resolution in mapping.py, as JSON:
    #   {"start": ["myStartField"], "title": ["headline"]}
    # Names listed here are tried before the built-in candidates.
    zeffy_field_map: dict[str, list[str]] = field(default_factory=dict)
    # By default only campaigns that look like events (ticketing) are included.
    sync_all_campaigns: bool = False

    # --- Feed ---
    calendar_name: str = "American Made Miniatures Events"
    default_timezone: str = "America/New_York"
    default_duration_minutes: int = 120
    # Advertised refresh hint, matching the workflow cron. Clients treat it as
    # a suggestion at best, and Google Calendar ignores it outright.
    refresh_interval: str = "PT30M"
    uid_domain: str = "americanmademiniatures.org"
    # Bound the feed so it does not grow without limit.
    past_window_days: int = 90
    future_window_days: int = 730

    # --- Publishing (GitHub Pages) ---
    # Directory the workflow uploads via actions/upload-pages-artifact.
    site_dir: str = "_site"
    feed_basename: str = "zeffy-events"
    # Random token embedded in the filename. A Pages site from a private repo
    # is still served publicly, so an unguessable URL is the access control.
    feed_token: str = ""
    # e.g. https://ian-hdsp.github.io/ammfs -- only used to print the URL.
    pages_base_url: str = ""
    emit_robots: bool = True
    dry_run: bool = False

    @classmethod
    def from_env(cls) -> "Config":
        raw_map = os.environ.get("ZEFFY_FIELD_MAP", "").strip()
        parsed_map: dict[str, list[str]] = {}
        if raw_map:
            parsed_map = {k: list(v) for k, v in json.loads(raw_map).items()}

        return cls(
            zeffy_api_key=os.environ.get("ZEFFY_API_KEY", "").strip(),
            zeffy_base_url=os.environ.get(
                "ZEFFY_BASE_URL", "https://api.zeffy.com/api/v1"
            ).rstrip("/"),
            zeffy_max_requests_per_minute=_int("ZEFFY_MAX_RPM", 90),
            zeffy_page_size=_int("ZEFFY_PAGE_SIZE", 100),
            zeffy_field_map=parsed_map,
            sync_all_campaigns=_bool("SYNC_ALL_CAMPAIGNS", False),
            calendar_name=os.environ.get(
                "CALENDAR_NAME", "American Made Miniatures Events"
            ),
            default_timezone=os.environ.get("DEFAULT_TIMEZONE", "America/New_York"),
            default_duration_minutes=_int("DEFAULT_DURATION_MINUTES", 120),
            refresh_interval=os.environ.get("REFRESH_INTERVAL", "PT30M"),
            uid_domain=os.environ.get("UID_DOMAIN", "americanmademiniatures.org"),
            past_window_days=_int("PAST_WINDOW_DAYS", 90),
            future_window_days=_int("FUTURE_WINDOW_DAYS", 730),
            site_dir=os.environ.get("SITE_DIR", "_site").strip() or "_site",
            feed_basename=os.environ.get("FEED_BASENAME", "zeffy-events").strip()
            or "zeffy-events",
            feed_token=os.environ.get("FEED_TOKEN", "").strip(),
            pages_base_url=os.environ.get("PAGES_BASE_URL", "").strip(),
            emit_robots=_bool("EMIT_ROBOTS", True),
            dry_run=_bool("DRY_RUN", False),
        )

    def validate(self, require_publish: bool = True) -> None:
        missing = []
        if not self.zeffy_api_key:
            missing.append("ZEFFY_API_KEY")
        if require_publish and not self.feed_token:
            missing.append("FEED_TOKEN")
        if missing:
            raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")
