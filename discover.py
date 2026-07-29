"""One-shot schema probe. Run this locally BEFORE the first real sync.

Zeffy publishes which resources exist but not a frozen field-by-field schema
for a campaign, so mapping.py resolves each logical field against a list of
candidate names. This script prints what the API actually returns for your
organisation so you can confirm the mapping -- and set ZEFFY_FIELD_MAP if any
field resolves to nothing.

    export ZEFFY_API_KEY=...        # never commit this
    python discover.py --pages 1
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from mapping import CANDIDATES, FieldResolver, campaign_to_occurrences, is_event_campaign
from zeffy import ZeffyClient, ZeffyError

LOGICAL_FIELDS = list(CANDIDATES)


def summarise(records: list[dict], timezone: str) -> None:
    resolver = FieldResolver()

    all_keys: set[str] = set()
    for record in records:
        all_keys.update(record)

    print(f"\n=== {len(records)} campaign record(s) sampled ===")
    print("\nTop-level keys seen across all records:")
    for key in sorted(all_keys):
        print(f"  - {key}")

    print("\nField resolution (logical -> matched key -> sample value):")
    unresolved: list[str] = []
    for logical in LOGICAL_FIELDS:
        matched_name = None
        sample = None
        for record in records:
            for name in resolver.names(logical):
                if name in record and record[name] not in (None, "", [], {}):
                    matched_name, sample = name, record[name]
                    break
            if matched_name:
                break
        if matched_name:
            rendered = json.dumps(sample, default=str)
            if len(rendered) > 120:
                rendered = rendered[:117] + "..."
            print(f"  {logical:12s} -> {matched_name:20s} = {rendered}")
        else:
            unresolved.append(logical)
            print(f"  {logical:12s} -> (NOT FOUND)")

    if unresolved:
        print(
            "\n!! Unresolved fields: "
            + ", ".join(unresolved)
            + "\n   Find the real key names in the raw record below and set ZEFFY_FIELD_MAP, e.g."
            '\n   ZEFFY_FIELD_MAP=\'{"start":["theRealName"]}\''
        )

    events = [r for r in records if is_event_campaign(r, resolver)]
    print(f"\nClassified as event campaigns: {len(events)}/{len(records)}")

    total = 0
    for record in events:
        occurrences = campaign_to_occurrences(record, resolver, timezone)
        total += len(occurrences)
        for occ in occurrences:
            print(f"  * {occ.start} -> {occ.end}  {occ.title!r}  key={occ.sync_key}")
    print(f"Total calendar entries that would be produced: {total}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe the Zeffy campaign schema")
    parser.add_argument("--pages", type=int, default=1, help="pages to sample")
    parser.add_argument("--page-size", type=int, default=25)
    parser.add_argument("--timezone", default=os.environ.get("DEFAULT_TIMEZONE", "America/New_York"))
    parser.add_argument("--raw", action="store_true", help="dump the full JSON of the first record")
    args = parser.parse_args(argv)

    api_key = os.environ.get("ZEFFY_API_KEY", "").strip()
    if not api_key:
        print("Set ZEFFY_API_KEY first (do not paste it into a file).", file=sys.stderr)
        return 2

    client = ZeffyClient(
        api_key=api_key,
        base_url=os.environ.get("ZEFFY_BASE_URL", "https://api.zeffy.com/api/v1").rstrip("/"),
    )
    records: list[dict] = []
    try:
        for record in client.iter_campaigns(page_size=args.page_size, max_pages=args.pages):
            records.append(record)
    except ZeffyError as exc:
        print(f"Zeffy request failed: {exc}", file=sys.stderr)
        return 1

    if not records:
        print("No campaigns returned. Check the key's organisation and permissions.")
        return 1

    if args.raw:
        print("=== raw first record ===")
        print(json.dumps(records[0], indent=2, default=str))

    summarise(records, args.timezone)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
