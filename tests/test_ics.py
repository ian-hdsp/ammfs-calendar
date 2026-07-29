from datetime import date, datetime
from zoneinfo import ZoneInfo

from ics import escape_text, fold_line, render_calendar
from mapping import Occurrence

UTC = ZoneInfo("UTC")
STAMP = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)


def timed_occ(**kw):
    defaults = dict(
        sync_key="c1:0",
        campaign_id="c1",
        title="Spring Gala",
        start=datetime(2026, 9, 12, 22, 30, tzinfo=UTC),
        end=datetime(2026, 9, 13, 1, 0, tzinfo=UTC),
        all_day=False,
        timezone="America/New_York",
    )
    defaults.update(kw)
    return Occurrence(**defaults)


def lines_of(feed):
    return feed.split("\r\n")


class TestFolding:
    def test_short_line_untouched(self):
        assert fold_line("SUMMARY:hi") == "SUMMARY:hi"

    def test_long_line_folded_to_75_octets(self):
        line = "DESCRIPTION:" + ("x" * 300)
        folded = fold_line(line)
        parts = folded.split("\r\n")
        assert len(parts[0].encode()) <= 75
        for part in parts[1:]:
            assert part.startswith(" ")
            assert len(part.encode()) <= 75

    def test_multibyte_not_split(self):
        folded = fold_line("SUMMARY:" + ("é" * 100))
        # Every continuation must still decode cleanly.
        for part in folded.split("\r\n"):
            part.encode("utf-8").decode("utf-8")

    def test_unfolding_restores_original(self):
        line = "DESCRIPTION:" + ("abc" * 90)
        assert fold_line(line).replace("\r\n ", "") == line


class TestEscaping:
    def test_escapes_specials(self):
        assert escape_text("a,b;c\\d") == "a\\,b\\;c\\\\d"

    def test_newlines_become_literal_n(self):
        assert escape_text("one\ntwo\r\nthree") == "one\\ntwo\\nthree"


class TestRenderCalendar:
    def test_structure_and_crlf(self):
        feed = render_calendar([timed_occ()], now=STAMP)
        assert feed.startswith("BEGIN:VCALENDAR\r\n")
        assert feed.endswith("END:VCALENDAR\r\n")
        assert "\n" not in feed.replace("\r\n", "")

    def test_no_method_for_subscription_feed(self):
        feed = render_calendar([timed_occ()], now=STAMP)
        assert "METHOD:" not in feed

    def test_refresh_hints_present(self):
        feed = render_calendar([timed_occ()], now=STAMP)
        assert "REFRESH-INTERVAL;VALUE=DURATION:PT1H" in feed
        assert "X-PUBLISHED-TTL:PT1H" in feed

    def test_timed_event_emitted_in_utc(self):
        feed = render_calendar([timed_occ()], now=STAMP)
        assert "DTSTART:20260912T223000Z" in feed
        assert "DTEND:20260913T010000Z" in feed

    def test_local_time_converted_to_utc(self):
        eastern = ZoneInfo("America/New_York")
        occ = timed_occ(
            start=datetime(2026, 9, 12, 18, 30, tzinfo=eastern),
            end=datetime(2026, 9, 12, 21, 0, tzinfo=eastern),
        )
        feed = render_calendar([occ], now=STAMP)
        assert "DTSTART:20260912T223000Z" in feed

    def test_all_day_uses_date_value_and_exclusive_end(self):
        occ = timed_occ(start=date(2026, 9, 12), end=date(2026, 9, 13), all_day=True)
        feed = render_calendar([occ], now=STAMP)
        assert "DTSTART;VALUE=DATE:20260912" in feed
        assert "DTEND;VALUE=DATE:20260913" in feed

    def test_uid_is_stable_and_unique(self):
        a = render_calendar([timed_occ()], now=STAMP)
        b = render_calendar([timed_occ()], now=STAMP)
        assert a == b
        assert "UID:zeffy-c1:0@americanmademiniatures.org" in a

    def test_events_sorted_by_start(self):
        later = timed_occ(sync_key="c2:0", title="Later",
                          start=datetime(2026, 12, 1, 1, 0, tzinfo=UTC),
                          end=datetime(2026, 12, 1, 3, 0, tzinfo=UTC))
        feed = render_calendar([later, timed_occ()], now=STAMP)
        assert feed.index("Spring Gala") < feed.index("Later")

    def test_optional_fields_omitted_when_empty(self):
        feed = render_calendar([timed_occ()], now=STAMP)
        assert "LOCATION:" not in feed
        assert "URL:" not in feed

    def test_location_and_url_included(self):
        occ = timed_occ(location="12 Main St, Buffalo NY",
                        url="https://zeffy.com/e/gala",
                        description="Join us")
        feed = render_calendar([occ], now=STAMP)
        assert "LOCATION:12 Main St\\, Buffalo NY" in feed
        assert "URL:https://zeffy.com/e/gala" in feed
        assert "DESCRIPTION:Join us\\n\\nhttps://zeffy.com/e/gala" in feed

    def test_empty_calendar_is_still_valid(self):
        feed = render_calendar([], now=STAMP)
        assert "BEGIN:VEVENT" not in feed
        assert feed.startswith("BEGIN:VCALENDAR")

    def test_every_vevent_is_closed(self):
        feed = render_calendar([timed_occ(), timed_occ(sync_key="c2:0")], now=STAMP)
        assert feed.count("BEGIN:VEVENT") == feed.count("END:VEVENT") == 2


class TestCalendarIdentity:
    def test_default_name_and_prodid(self):
        feed = render_calendar([])
        assert "NAME:AMFS Zeffy Events" in feed
        assert "X-WR-CALNAME:AMFS Zeffy Events" in feed
        assert "PRODID:-//AMFS//Zeffy Events//EN" in feed

    def test_prodid_is_overridable(self):
        feed = render_calendar([], prodid="-//Other//Thing//EN")
        assert "PRODID:-//Other//Thing//EN" in feed

    def test_name_is_escaped(self):
        # A comma in the name would otherwise split the property value.
        feed = render_calendar([], calendar_name="AMFS, Events")
        assert "NAME:AMFS\\, Events" in feed
