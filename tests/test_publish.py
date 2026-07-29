import pytest

from publish import (
    ROBOTS_BODY,
    build_site,
    feed_digest,
    feed_filename,
    subscription_urls,
    validate_token,
    write_local,
)

FEED = "BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n"
TOKEN = "7f3a9c21e8b4"


class TestLocalWrite:
    def test_preserves_crlf(self, tmp_path):
        target = tmp_path / "out.ics"
        write_local(FEED, str(target))
        # newline="" must stop Python translating CRLF into CRCRLF or LF.
        assert target.read_bytes() == FEED.encode("utf-8")

    def test_reports_byte_length(self, tmp_path):
        result = write_local(FEED, str(tmp_path / "out.ics"))
        assert result["bytes"] == len(FEED.encode("utf-8"))


class TestToken:
    @pytest.mark.parametrize("token", ["abcdef", TOKEN, "a" * 64, "a-b_c.d"])
    def test_accepts_valid_tokens(self, token):
        assert validate_token(token) == token

    def test_strips_surrounding_whitespace(self):
        # Secrets pasted into the GitHub UI often carry a trailing newline.
        assert validate_token(f"  {TOKEN}\n") == TOKEN

    @pytest.mark.parametrize("token", ["", "short", "a" * 65, "has space", "sl/ash", "../x"])
    def test_rejects_bad_tokens(self, token):
        # Rejected rather than sanitised: a silently altered token would change
        # the feed URL and break every existing subscriber.
        with pytest.raises(ValueError):
            validate_token(token)

    def test_filename_embeds_token(self):
        assert feed_filename("zeffy-events", TOKEN) == f"zeffy-events-{TOKEN}.ics"


class TestBuildSite:
    def test_writes_feed_at_unguessable_name(self, tmp_path):
        result = build_site(FEED, str(tmp_path), token=TOKEN)
        written = tmp_path / f"zeffy-events-{TOKEN}.ics"

        assert written.read_bytes() == FEED.encode("utf-8")
        assert result["filename"] == written.name
        assert result["bytes"] == len(FEED.encode("utf-8"))

    def test_creates_site_dir_if_absent(self, tmp_path):
        target = tmp_path / "nested" / "_site"
        build_site(FEED, str(target), token=TOKEN)
        assert (target / f"zeffy-events-{TOKEN}.ics").exists()

    def test_writes_nojekyll(self, tmp_path):
        build_site(FEED, str(tmp_path), token=TOKEN)
        assert (tmp_path / ".nojekyll").exists()

    def test_writes_robots_by_default(self, tmp_path):
        build_site(FEED, str(tmp_path), token=TOKEN)
        assert (tmp_path / "robots.txt").read_text() == ROBOTS_BODY

    def test_robots_never_names_the_feed(self, tmp_path):
        # robots.txt is at a well-known URL; leaking the token there would
        # publish the one thing keeping the feed private.
        build_site(FEED, str(tmp_path), token=TOKEN)
        assert TOKEN not in (tmp_path / "robots.txt").read_text()

    def test_robots_can_be_disabled(self, tmp_path):
        build_site(FEED, str(tmp_path), token=TOKEN, emit_robots=False)
        assert not (tmp_path / "robots.txt").exists()

    def test_writes_no_index_page(self, tmp_path):
        # The site root should 404, not advertise that a calendar lives here.
        build_site(FEED, str(tmp_path), token=TOKEN)
        assert not (tmp_path / "index.html").exists()

    def test_overwrites_previous_feed(self, tmp_path):
        build_site(FEED, str(tmp_path), token=TOKEN)
        build_site(FEED + "X", str(tmp_path), token=TOKEN)
        # read_bytes, not read_text: text mode would translate the CRLFs away.
        written = (tmp_path / f"zeffy-events-{TOKEN}.ics").read_bytes()
        assert written == (FEED + "X").encode("utf-8")

    def test_dry_run_writes_nothing(self, tmp_path):
        target = tmp_path / "_site"
        result = build_site(FEED, str(target), token=TOKEN, dry_run=True)

        assert result["dry_run"] is True
        assert result["bytes"] == len(FEED.encode("utf-8"))
        assert not target.exists()

    def test_dry_run_still_validates_token(self, tmp_path):
        with pytest.raises(ValueError):
            build_site(FEED, str(tmp_path), token="", dry_run=True)


class TestDigest:
    def test_stable_and_content_sensitive(self):
        assert feed_digest(FEED) == feed_digest(FEED)
        assert feed_digest(FEED) != feed_digest(FEED + "X")


class TestSubscriptionUrls:
    def test_https_and_webcal_point_at_same_file(self):
        urls = subscription_urls("https://ian-hdsp.github.io/ammfs", "f.ics")
        assert urls["https"] == "https://ian-hdsp.github.io/ammfs/f.ics"
        assert urls["webcal"] == "webcal://ian-hdsp.github.io/ammfs/f.ics"

    def test_tolerates_trailing_slash_on_base(self):
        urls = subscription_urls("https://ian-hdsp.github.io/ammfs/", "f.ics")
        assert urls["https"] == "https://ian-hdsp.github.io/ammfs/f.ics"
