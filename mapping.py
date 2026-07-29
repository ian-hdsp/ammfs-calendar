"""Normalise Zeffy campaign records into calendar occurrences.

Zeffy's published API reference documents the resources (payments, contacts,
campaigns) but not a frozen field-by-field schema for a campaign, and a Zeffy
event can carry multiple dates and times. So rather than hard-coding one
guess, each logical field resolves against a list of candidate names, and
`ZEFFY_FIELD_MAP` can prepend the real names once they are confirmed against
live output (see discover.py).
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

CANDIDATES: dict[str, list[str]] = {
    "id": ["id", "campaignId", "campaign_id", "_id", "uuid"],
    "title": ["title", "name", "campaignName", "eventName", "formName"],
    "type": ["type", "campaignType", "formType", "kind", "category"],
    "status": ["status", "state", "publicationStatus"],
    "start": [
        "startDate", "startsAt", "start_at", "startDateTime", "eventDate",
        "eventStartDate", "start", "dateTime", "date",
    ],
    "end": [
        "endDate", "endsAt", "end_at", "endDateTime", "eventEndDate", "end",
    ],
    "timezone": ["timezone", "timeZone", "tz", "eventTimezone"],
    "location": [
        "location", "address", "venue", "eventAddress", "fullAddress",
        "formattedAddress", "place",
    ],
    "description": ["description", "summary", "details", "shortDescription"],
    "url": ["url", "publicUrl", "link", "formUrl", "shareUrl", "pageUrl"],
    "occurrences": [
        "occurrences", "dates", "eventDates", "sessions", "timeSlots",
        "rates", "schedules",
    ],
}

# Campaign types that represent something happening at a time and place.
EVENT_TYPE_HINTS = ("event", "ticket", "gala", "conference", "auction")

_ADDRESS_PARTS = ("street", "line1", "address1", "city", "state", "province",
                  "postalCode", "zip", "country")


@dataclass(frozen=True)
class Occurrence:
    """One thing that becomes exactly one Google Calendar entry."""

    sync_key: str
    campaign_id: str
    title: str
    start: datetime | date
    end: datetime | date
    all_day: bool
    timezone: str
    location: str = ""
    description: str = ""
    url: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def content_hash(self) -> str:
        payload = {
            "title": self.title,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "all_day": self.all_day,
            "timezone": self.timezone,
            "location": self.location,
            "description": self.description,
            "url": self.url,
        }
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


class FieldResolver:
    def __init__(self, overrides: dict[str, list[str]] | None = None) -> None:
        self.overrides = overrides or {}

    def names(self, logical: str) -> list[str]:
        return list(self.overrides.get(logical, [])) + CANDIDATES.get(logical, [])

    def get(self, record: dict, logical: str, default: Any = None) -> Any:
        for name in self.names(logical):
            if name in record and record[name] not in (None, "", [], {}):
                return record[name]
        return default


def parse_temporal(value: Any) -> tuple[datetime | date, bool] | None:
    """Parse a Zeffy date/datetime. Returns (value, is_all_day) or None."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value, False
    if isinstance(value, date):
        return value, True
    if isinstance(value, (int, float)):
        # Epoch seconds or milliseconds.
        seconds = value / 1000.0 if value > 1e11 else float(value)
        return datetime.fromtimestamp(seconds, tz=ZoneInfo("UTC")), False
    if not isinstance(value, str):
        return None

    raw = value.strip()
    if not raw:
        return None

    # Date only -> all-day.
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return date.fromisoformat(raw), True

    normalised = raw.replace("Z", "+00:00")
    # Trim fractional seconds longer than microseconds, which fromisoformat
    # rejects on Python < 3.11 and still rejects beyond 6 digits.
    normalised = re.sub(r"(\.\d{6})\d+", r"\1", normalised)
    try:
        parsed = datetime.fromisoformat(normalised)
    except ValueError:
        log.debug("Unparseable temporal value: %r", raw)
        return None
    return parsed, False


def stringify_location(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("formattedAddress", "fullAddress", "displayName", "name"):
            if isinstance(value.get(key), str) and value[key].strip():
                return value[key].strip()
        parts = [str(value[k]).strip() for k in _ADDRESS_PARTS
                 if isinstance(value.get(k), (str, int)) and str(value[k]).strip()]
        return ", ".join(dict.fromkeys(parts))
    return str(value).strip()


def strip_html(text: str) -> str:
    without_tags = re.sub(r"<[^>]+>", " ", text)
    # Unescape after stripping tags, not before: doing it first would turn a
    # literal &lt;p&gt; into a tag and then delete the text it was quoting.
    # Zeffy descriptions are rich text, so entities are common.
    unescaped = html.unescape(without_tags)
    return re.sub(r"\s+", " ", unescaped).strip()


def is_event_campaign(record: dict, resolver: FieldResolver) -> bool:
    raw_type = resolver.get(record, "type", "")
    type_text = str(raw_type).lower() if raw_type else ""
    if any(hint in type_text for hint in EVENT_TYPE_HINTS):
        return True
    # No usable type field, but it carries a start date: treat as an event.
    if not type_text and resolver.get(record, "start") is not None:
        return True
    return False


def _occurrence_windows(
    record: dict, resolver: FieldResolver
) -> list[tuple[str, Any, Any]]:
    """Return (occurrence_id, raw_start, raw_end) tuples for a campaign."""
    nested = resolver.get(record, "occurrences")
    windows: list[tuple[str, Any, Any]] = []

    if isinstance(nested, list) and nested:
        for index, item in enumerate(nested):
            if not isinstance(item, dict):
                parsed = parse_temporal(item)
                if parsed:
                    windows.append((str(index), item, None))
                continue
            start = resolver.get(item, "start")
            if start is None:
                continue
            occ_id = item.get("id") or item.get("_id") or str(index)
            windows.append((str(occ_id), start, resolver.get(item, "end")))
        if windows:
            return windows

    start = resolver.get(record, "start")
    if start is None:
        return []
    return [("0", start, resolver.get(record, "end"))]


def campaign_to_occurrences(
    record: dict,
    resolver: FieldResolver,
    default_timezone: str,
    default_duration_minutes: int = 120,
) -> list[Occurrence]:
    campaign_id = resolver.get(record, "id")
    if campaign_id in (None, ""):
        log.warning("Skipping campaign with no resolvable id: keys=%s", list(record))
        return []
    campaign_id = str(campaign_id)

    title = str(resolver.get(record, "title", "") or "").strip() or "Zeffy event"
    tz_name = str(resolver.get(record, "timezone", "") or "").strip() or default_timezone
    try:
        tzinfo = ZoneInfo(tz_name)
    except Exception:
        log.warning("Unknown timezone %r on campaign %s, using %s",
                    tz_name, campaign_id, default_timezone)
        tz_name = default_timezone
        tzinfo = ZoneInfo(default_timezone)

    location = stringify_location(resolver.get(record, "location"))
    raw_description = str(resolver.get(record, "description", "") or "")
    description = strip_html(raw_description)
    url = str(resolver.get(record, "url", "") or "").strip()

    results: list[Occurrence] = []
    for occ_id, raw_start, raw_end in _occurrence_windows(record, resolver):
        parsed_start = parse_temporal(raw_start)
        if parsed_start is None:
            log.debug("Campaign %s occurrence %s has no parseable start", campaign_id, occ_id)
            continue
        start_value, all_day = parsed_start

        parsed_end = parse_temporal(raw_end) if raw_end is not None else None
        if parsed_end is not None and parsed_end[1] != all_day:
            # Mixed granularity (date start, datetime end or vice versa):
            # trust the start and derive the end from it.
            parsed_end = None

        if parsed_end is not None:
            end_value = parsed_end[0]
        elif all_day:
            end_value = start_value + timedelta(days=1)
        else:
            end_value = start_value + timedelta(minutes=default_duration_minutes)

        if not all_day:
            if start_value.tzinfo is None:
                start_value = start_value.replace(tzinfo=tzinfo)
            if end_value.tzinfo is None:
                end_value = end_value.replace(tzinfo=tzinfo)
            if end_value <= start_value:
                end_value = start_value + timedelta(minutes=default_duration_minutes)
        elif end_value <= start_value:
            end_value = start_value + timedelta(days=1)

        results.append(
            Occurrence(
                sync_key=f"{campaign_id}:{occ_id}",
                campaign_id=campaign_id,
                title=title,
                start=start_value,
                end=end_value,
                all_day=all_day,
                timezone=tz_name,
                location=location,
                description=description,
                url=url,
            )
        )
    return results


def occurrence_sort_key(occ: Occurrence) -> datetime:
    if isinstance(occ.start, datetime):
        return occ.start
    return datetime.combine(occ.start, time.min, tzinfo=ZoneInfo("UTC"))
