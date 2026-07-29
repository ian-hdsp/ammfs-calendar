"""Publish the rendered feed as a GitHub Pages site directory.

GitHub Pages serves whatever static tree the workflow uploads, so "publishing"
here means building a small directory that `actions/upload-pages-artifact`
consumes. There is no bucket, no IAM, and no cloud SDK.

Two details are load-bearing:

* The feed lives at an **unguessable** path. A Pages site built from a private
  repo is still served publicly -- Pro grants the ability to publish from a
  private repo, not access control -- and Google Calendar can only fetch a URL
  it reaches anonymously. So secrecy of the URL is the control, and the
  filename carries a random token supplied via `FEED_TOKEN`.
* Nothing else written to the site root reveals that token. In particular
  `robots.txt` sits at a well-known URL, so it never names the feed.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re

log = logging.getLogger(__name__)

CONTENT_TYPE = "text/calendar; charset=utf-8"

# A mangled token would change the feed URL and quietly break every existing
# subscriber, so reject bad input rather than sanitising it.
_TOKEN_RE = re.compile(r"[A-Za-z0-9._-]{6,64}")

ROBOTS_BODY = "User-agent: *\nDisallow: /\n"


def feed_digest(feed: str) -> str:
    return hashlib.sha256(feed.encode("utf-8")).hexdigest()


def validate_token(token: str) -> str:
    token = (token or "").strip()
    if not _TOKEN_RE.fullmatch(token):
        raise ValueError(
            "FEED_TOKEN must be 6-64 characters of [A-Za-z0-9._-]. Generate one with:\n"
            '  python -c "import secrets; print(secrets.token_hex(8))"'
        )
    return token


def feed_filename(basename: str, token: str) -> str:
    """The unguessable object name, e.g. zeffy-events-7f3a9c21e8b4.ics"""
    return f"{basename}-{validate_token(token)}.ics"


def write_local(feed: str, path: str) -> dict:
    # newline="" so the CRLF line endings required by RFC 5545 survive.
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(feed)
    return {"target": path, "bytes": len(feed.encode("utf-8")), "changed": True}


def build_site(
    feed: str,
    site_dir: str,
    basename: str = "zeffy-events",
    token: str = "",
    emit_robots: bool = True,
    dry_run: bool = False,
) -> dict:
    """Materialise the Pages site directory. Returns a summary dict."""
    filename = feed_filename(basename, token)
    size = len(feed.encode("utf-8"))
    result = {
        "target": f"{site_dir}/{filename}",
        "filename": filename,
        "bytes": size,
        "digest": feed_digest(feed)[:12],
        "dry_run": dry_run,
    }

    if dry_run:
        log.info("[dry-run] would write %d bytes to %s/%s", size, site_dir, filename)
        return result

    os.makedirs(site_dir, exist_ok=True)
    write_local(feed, os.path.join(site_dir, filename))

    # Pages runs Jekyll by default, which drops files it considers special.
    # .nojekyll makes that independent of whatever token is in play.
    with open(os.path.join(site_dir, ".nojekyll"), "w", encoding="utf-8") as handle:
        handle.write("")

    if emit_robots:
        with open(os.path.join(site_dir, "robots.txt"), "w", encoding="utf-8") as handle:
            handle.write(ROBOTS_BODY)

    # No index.html is written on purpose: the site root should 404 rather than
    # advertise that a calendar lives here.
    log.info("Built site in %s (%d bytes of calendar)", site_dir, size)
    return result


def subscription_urls(base_url: str, filename: str) -> dict[str, str]:
    """The same file, in the forms different clients expect."""
    https_url = f"{base_url.rstrip('/')}/{filename}"
    return {
        "https": https_url,
        # webcal:// makes desktop clients offer "subscribe" instead of "download".
        "webcal": "webcal://" + https_url.split("://", 1)[1],
    }
