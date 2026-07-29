"""Render occurrences as an RFC 5545 subscribable calendar feed.

This produces a *subscription* feed, not a set of invitations, so METHOD is
deliberately omitted -- clients that see METHOD:PUBLISH may treat the payload
as an import or an invite rather than a live feed.

Timed events are emitted in UTC so no VTIMEZONE blocks are needed, which keeps
the feed small and avoids client-specific timezone-database disagreements.
All-day events use VALUE=DATE with an exclusive DTEND, per the spec.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from mapping import Occurrence, occurrence_sort_key

UTC = ZoneInfo("UTC")

# RFC 5545 requires lines to be folded at 75 octets.
_FOLD_LIMIT = 75


def escape_text(value: str) -> str:
    """Escape per RFC 5545 section 3.3.11."""
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
        .replace("\r", "\\n")
    )


def fold_line(line: str) -> str:
    """Fold a content line to 75 octets, splitting on UTF-8 boundaries."""
    encoded = line.encode("utf-8")
    if len(encoded) <= _FOLD_LIMIT:
        return line

    chunks: list[bytes] = []
    start = 0
    limit = _FOLD_LIMIT
    while start < len(encoded):
        end = min(start + limit, len(encoded))
        # Do not split a multi-byte character: back off to a boundary.
        while end > start and end < len(encoded) and (encoded[end] & 0xC0) == 0x80:
            end -= 1
        chunks.append(encoded[start:end])
        start = end
        limit = _FOLD_LIMIT - 1  # continuation lines carry a leading space
    return "\r\n ".join(chunk.decode("utf-8") for chunk in chunks)


def _fmt_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _fmt_date(value: date) -> str:
    return value.strftime("%Y%m%d")


def _uid(occ: Occurrence, domain: str) -> str:
    safe = occ.sync_key.replace("@", "-").replace(" ", "-")
    return f"zeffy-{safe}@{domain}"


def render_event(occ: Occurrence, dtstamp: datetime, domain: str) -> list[str]:
    lines = [
        "BEGIN:VEVENT",
        f"UID:{_uid(occ, domain)}",
        f"DTSTAMP:{_fmt_utc(dtstamp)}",
        "SEQUENCE:0",
        f"SUMMARY:{escape_text(occ.title)}",
    ]

    if occ.all_day:
        start = occ.start if isinstance(occ.start, date) else occ.start.date()
        end = occ.end if isinstance(occ.end, date) else occ.end.date()
        if end <= start:
            end = start + timedelta(days=1)
        lines.append(f"DTSTART;VALUE=DATE:{_fmt_date(start)}")
        lines.append(f"DTEND;VALUE=DATE:{_fmt_date(end)}")
    else:
        lines.append(f"DTSTART:{_fmt_utc(occ.start)}")
        lines.append(f"DTEND:{_fmt_utc(occ.end)}")

    if occ.location:
        lines.append(f"LOCATION:{escape_text(occ.location)}")

    description_parts = [p for p in (occ.description, occ.url) if p]
    if description_parts:
        body = "\n\n".join(description_parts)
        lines.append(f"DESCRIPTION:{escape_text(body)}")
    if occ.url:
        lines.append(f"URL:{escape_text(occ.url)}")

    lines.append("STATUS:CONFIRMED")
    lines.append("TRANSP:OPAQUE")
    lines.append("END:VEVENT")
    return lines


def render_calendar(
    occurrences: list[Occurrence],
    calendar_name: str = "Zeffy Events",
    timezone: str = "America/New_York",
    domain: str = "americanmademiniatures.org",
    refresh: str = "PT1H",
    now: datetime | None = None,
) -> str:
    """Return a complete .ics feed as a CRLF-delimited string."""
    dtstamp = now or datetime.now(tz=UTC)

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//American Made Miniatures//Zeffy Calendar Sync//EN",
        "CALSCALE:GREGORIAN",
        f"NAME:{escape_text(calendar_name)}",
        f"X-WR-CALNAME:{escape_text(calendar_name)}",
        f"X-WR-TIMEZONE:{escape_text(timezone)}",
        f"REFRESH-INTERVAL;VALUE=DURATION:{refresh}",
        f"X-PUBLISHED-TTL:{refresh}",
    ]

    for occ in sorted(occurrences, key=occurrence_sort_key):
        lines.extend(render_event(occ, dtstamp, domain))

    lines.append("END:VCALENDAR")
    return "\r\n".join(fold_line(line) for line in lines) + "\r\n"
