from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from mapping import (
    FieldResolver,
    campaign_to_occurrences,
    is_event_campaign,
    is_archived,
    parse_temporal,
    stringify_location,
    strip_html,
)

EASTERN = "America/New_York"


def occ_of(record, resolver=None, tz=EASTERN, duration=120):
    return campaign_to_occurrences(record, resolver or FieldResolver(), tz, duration)


class TestParseTemporal:
    def test_date_only_is_all_day(self):
        value, all_day = parse_temporal("2026-09-12")
        assert value == date(2026, 9, 12)
        assert all_day is True

    def test_utc_z_suffix(self):
        value, all_day = parse_temporal("2026-09-12T18:30:00Z")
        assert all_day is False
        assert value == datetime(2026, 9, 12, 18, 30, tzinfo=ZoneInfo("UTC"))

    def test_offset_preserved(self):
        value, _ = parse_temporal("2026-09-12T18:30:00-04:00")
        assert value.utcoffset().total_seconds() == -4 * 3600

    def test_overlong_fractional_seconds(self):
        value, _ = parse_temporal("2026-09-12T18:30:00.1234567Z")
        assert value.year == 2026

    def test_epoch_millis(self):
        value, _ = parse_temporal(1789000000000)
        assert value.tzinfo is not None

    @pytest.mark.parametrize("bad", [None, "", "not a date", {}, []])
    def test_rejects_junk(self, bad):
        assert parse_temporal(bad) is None


class TestHelpers:
    def test_strip_html(self):
        assert strip_html("<p>Gala   night</p><br/>Join us") == "Gala night Join us"

    def test_decodes_html_entities(self):
        # Zeffy descriptions are rich text; raw &amp; would reach subscribers.
        assert strip_html("<p>Wine &amp; cheese</p>") == "Wine & cheese"

    def test_decodes_numeric_and_named_entities(self):
        assert strip_html("Tom&#39;s&nbsp;night") == "Tom's night"

    def test_entities_are_decoded_after_tag_removal(self):
        # Decoding first would turn &lt;b&gt; into a tag and then delete "bold".
        assert strip_html("&lt;b&gt;bold&lt;/b&gt;") == "<b>bold</b>"

    def test_location_from_dict_parts(self):
        value = stringify_location(
            {"address1": "12 Main St", "city": "Buffalo", "state": "NY", "zip": "14201"}
        )
        assert value == "12 Main St, Buffalo, NY, 14201"

    def test_location_prefers_formatted(self):
        assert stringify_location({"formattedAddress": "12 Main St, Buffalo NY",
                                   "city": "Buffalo"}) == "12 Main St, Buffalo NY"

    def test_location_plain_string(self):
        assert stringify_location("  The Barn  ") == "The Barn"


class TestClassification:
    def test_event_type_detected(self):
        assert is_event_campaign({"type": "TICKETING_EVENT"}, FieldResolver())

    def test_donation_form_rejected(self):
        assert not is_event_campaign({"type": "donation_form"}, FieldResolver())

    def test_untyped_with_start_treated_as_event(self):
        assert is_event_campaign({"startDate": "2026-09-12"}, FieldResolver())

    def test_untyped_without_start_rejected(self):
        assert not is_event_campaign({"name": "General fund"}, FieldResolver())


class TestCampaignToOccurrences:
    def test_single_timed_event_applies_default_duration(self):
        record = {
            "id": "camp_1",
            "name": "Spring Gala",
            "type": "event",
            "startDate": "2026-09-12T18:30:00-04:00",
        }
        (occ,) = occ_of(record)
        assert occ.title == "Spring Gala"
        assert occ.sync_key == "camp_1:0"
        assert occ.all_day is False
        assert (occ.end - occ.start).total_seconds() == 120 * 60

    def test_explicit_end_respected(self):
        record = {
            "id": "c",
            "name": "Auction",
            "type": "event",
            "startDate": "2026-09-12T18:00:00Z",
            "endDate": "2026-09-12T21:00:00Z",
        }
        (occ,) = occ_of(record)
        assert (occ.end - occ.start).total_seconds() == 3 * 3600

    def test_end_before_start_is_repaired(self):
        record = {
            "id": "c", "name": "x", "type": "event",
            "startDate": "2026-09-12T20:00:00Z",
            "endDate": "2026-09-12T19:00:00Z",
        }
        (occ,) = occ_of(record)
        assert occ.end > occ.start

    def test_all_day_end_is_exclusive_next_day(self):
        record = {"id": "c", "name": "Fair", "type": "event", "startDate": "2026-09-12"}
        (occ,) = occ_of(record)
        assert occ.all_day is True
        assert occ.start == date(2026, 9, 12)
        assert occ.end == date(2026, 9, 13)

    def test_naive_datetime_gets_campaign_timezone(self):
        record = {
            "id": "c", "name": "x", "type": "event",
            "startDate": "2026-09-12T18:30:00",
            "timezone": "America/Chicago",
        }
        (occ,) = occ_of(record)
        assert occ.timezone == "America/Chicago"
        assert occ.start.utcoffset().total_seconds() == -5 * 3600

    def test_unknown_timezone_falls_back(self):
        record = {
            "id": "c", "name": "x", "type": "event",
            "startDate": "2026-09-12T18:30:00", "timezone": "Mars/Olympus",
        }
        (occ,) = occ_of(record)
        assert occ.timezone == EASTERN

    def test_multi_date_event_yields_one_per_occurrence(self):
        record = {
            "id": "camp_9",
            "name": "Workshop Series",
            "type": "event",
            "occurrences": [
                {"id": "a", "startDate": "2026-09-12T18:00:00Z"},
                {"id": "b", "startDate": "2026-09-19T18:00:00Z"},
                {"id": "c", "startDate": "2026-09-26T18:00:00Z"},
            ],
        }
        results = occ_of(record)
        assert [o.sync_key for o in results] == ["camp_9:a", "camp_9:b", "camp_9:c"]
        assert all(o.title == "Workshop Series" for o in results)

    def test_occurrence_without_start_is_skipped(self):
        record = {
            "id": "c", "name": "x", "type": "event",
            "occurrences": [{"id": "a"}, {"id": "b", "startDate": "2026-09-19T18:00:00Z"}],
        }
        assert [o.sync_key for o in occ_of(record)] == ["c:b"]

    def test_campaign_without_id_skipped(self):
        assert occ_of({"name": "x", "type": "event", "startDate": "2026-09-12"}) == []

    def test_campaign_without_start_skipped(self):
        assert occ_of({"id": "c", "name": "x", "type": "event"}) == []

    def test_field_map_override_wins(self):
        resolver = FieldResolver({"start": ["whenever"], "title": ["headline"]})
        record = {"id": "c", "headline": "Custom", "type": "event",
                  "whenever": "2026-09-12T18:00:00Z"}
        (occ,) = occ_of(record, resolver)
        assert occ.title == "Custom"

    def test_content_hash_changes_with_content(self):
        base = {"id": "c", "name": "Gala", "type": "event",
                "startDate": "2026-09-12T18:00:00Z"}
        moved = dict(base, startDate="2026-09-13T18:00:00Z")
        renamed = dict(base, name="Gala 2026")
        h1 = occ_of(base)[0].content_hash()
        assert h1 == occ_of(dict(base))[0].content_hash()   # stable
        assert h1 != occ_of(moved)[0].content_hash()
        assert h1 != occ_of(renamed)[0].content_hash()


class TestArchivedFiltering:
    def test_archived_campaign_is_archived(self):
        assert is_archived({"id": "c1", "is_archived": True}) is True

    def test_deleted_campaign_is_archived(self):
        assert is_archived({"id": "c1", "deleted_at": "2026-01-01T00:00:00Z"}) is True

    @pytest.mark.parametrize("status", ["archived", "deleted", "draft", "inactive", "ARCHIVED"])
    def test_retired_statuses(self, status):
        assert is_archived({"id": "c1", "status": status}) is True

    def test_active_campaign_is_not_archived(self):
        assert is_archived({"id": "c1", "status": "active", "is_archived": False}) is False

    def test_missing_flags_default_to_live(self):
        assert is_archived({"id": "c1"}) is False

    def test_archived_occurrences_are_dropped(self):
        # Zeffy leaves archived occurrences in the list; they were 14% of the
        # real account's dates and must not reach the calendar.
        record = {
            "id": "c1",
            "type": "ticketing",
            "occurrences": [
                {"id": "live", "start_date": 1786035600, "end_date": 1786039200},
                {"id": "gone", "start_date": 1786122000, "end_date": 1786125600,
                 "is_archived": True},
            ],
        }
        occs = occ_of(record)
        assert [o.sync_key for o in occs] == ["c1:live"]

    def test_campaign_with_only_archived_occurrences_yields_nothing(self):
        record = {
            "id": "c1",
            "type": "ticketing",
            "occurrences": [
                {"id": "gone", "start_date": 1786122000, "is_archived": True},
            ],
        }
        assert occ_of(record) == []


class TestZeffySnakeCaseDates:
    def test_epoch_seconds_from_start_date(self):
        record = {"id": "c1", "type": "ticketing",
                  "occurrences": [{"id": "o1", "start_date": 1786035600,
                                   "end_date": 1786039200}]}
        occ = occ_of(record)[0]
        assert occ.start.timestamp() == 1786035600
        assert occ.end.timestamp() == 1786039200

    def test_campaign_level_snake_case_dates_resolve(self):
        # No occurrences list: must fall back to the campaign's own dates.
        record = {"id": "c1", "type": "ticketing",
                  "start_date": 1786035600, "end_date": 1786039200}
        occs = occ_of(record)
        assert len(occs) == 1
        assert occs[0].start.timestamp() == 1786035600
