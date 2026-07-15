"""
Two Flints Brewery event scraper.
Primary source: Berkshire Artistree iCal feed (structured, no auth, no anti-bot).
Fallback: HTML parsing via Python stdlib html.parser.

Outputs a list of dicts matching the Dragged Out generator.py schema.
"""

import json
import re
import sys
import time
import urllib.error
import urllib.request
from html.parser import HTMLParser
from datetime import datetime, timezone

# ── Config ──

VENUE_NAME = "Two Flints Brewery"
VENUE_SLUG = "two-flints"
SOURCE = "web:two-flints"
ICAL_URL = "https://berkshireartistree.co.uk/venue/two-flints-brewery-windsor/?ical=1"
HTML_URL = "https://berkshireartistree.co.uk/venue/two-flints-brewery-windsor/"
USER_AGENT = "TwoFlintsScraper/1.0 (draggedout project; contact@cybr.fi)"
REQUEST_DELAY = 1.0  # seconds between requests (rate limiting)

# ── Helpers ──


def fetch(url):
    """Fetch a URL with rate limiting and basic error handling."""
    time.sleep(REQUEST_DELAY)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} fetching {url}: {e.reason}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"URL error fetching {url}: {e.reason}") from e


def _ical_unfold(text):
    """Unfold iCal continuation lines (RFC 5545 §3.1)."""
    return re.sub(r"\r?\n[\t ]", "", text)


def _ical_decode(text):
    """Decode iCal escaped text (\\n, \\N, \\,, \\;, \\\\)."""
    text = text.replace("\\n", "\n").replace("\\N", "\n")
    text = text.replace("\\,", ",").replace("\\;", ";")
    text = text.replace("\\\\", "\\")
    return text


def _ical_dt_to_local(dt_str):
    """
    Parse iCal DTSTART/DTEND.
    Returns (date_str, time_str) where time_str is "17:00-19:00" or "".
    """
    if ":" in dt_str:
        value = dt_str.split(":", 1)[1]
    else:
        value = dt_str
    value = value.strip()

    if "T" in value and len(value) >= 15:
        date_part = value[:8]
        time_part = value[9:15]
        d = f"{date_part[:4]}-{date_part[4:6]}-{date_part[6:8]}"
        t = f"{time_part[:2]}:{time_part[2:4]}"
        return d, t
    else:
        date_part = value.strip()
        if len(date_part) == 8:
            d = f"{date_part[:4]}-{date_part[4:6]}-{date_part[6:8]}"
            return d, ""
    return "", ""


def _ical_summary_to_name(summary):
    """Clean up iCal SUMMARY field to event name."""
    decoded = _ical_decode(summary.strip())
    # Remove venue prefix like "Live Music in Windsor, Two Flints Brewery | "
    decoded = re.sub(r"^.*?\|\s*", "", decoded)
    return decoded


# ── iCal Parser ──


def parse_ical_events(text):
    """
    Parse iCal VCALENDAR data into event dicts using stdlib.
    Returns list of event dicts with raw fields.
    """
    unfolded = _ical_unfold(text)
    events = []

    vevent_blocks = re.findall(
        r"BEGIN:VEVENT\r?\n(.*?)\r?\nEND:VEVENT",
        unfolded,
        re.DOTALL,
    )

    for block in vevent_blocks:
        event = {}

        def get_field(name):
            m = re.search(rf"^{name}(?:;[^:]*)?:(.*)$", block, re.MULTILINE)
            return m.group(1).strip() if m else ""

        def get_field_lines(name):
            matches = re.findall(rf"^{name}(?:;[^:]*)?:(.*)$", block, re.MULTILINE)
            return [m.strip() for m in matches]

        dtstart = get_field("DTSTART")
        dtend = get_field("DTEND")
        summary_raw = get_field("SUMMARY")
        desc_raw = get_field("DESCRIPTION")
        url = get_field("URL")
        location = get_field("LOCATION")
        uid = get_field("UID")
        categories = get_field_lines("CATEGORIES")
        attach_raw = get_field("ATTACH")

        date_str, time_start = _ical_dt_to_local(dtstart)
        _, time_end = _ical_dt_to_local(dtend)
        time_str = f"{time_start}-{time_end}" if time_start and time_end else time_start or time_end or ""

        name = _ical_summary_to_name(summary_raw)

        description = _ical_decode(desc_raw)
        description = re.sub(r"\n{3,}", "\n\n", description).strip()

        venue = _ical_decode(location.strip()) if location else VENUE_NAME

        image = ""
        if attach_raw:
            if attach_raw.startswith("http://") or attach_raw.startswith("https://"):
                image = attach_raw.strip()
            elif attach_raw.startswith("//"):
                image = "https:" + attach_raw.strip()

        event["name"] = name
        event["date"] = date_str
        event["time"] = time_str
        event["description"] = description
        event["url"] = url
        event["venue"] = venue
        event["categories"] = categories
        event["image"] = image
        event["uid"] = uid
        event["_source"] = "ical"

        events.append(event)

    return events


# ── HTML Fallback Parser ──


class TribeEventsParser(HTMLParser):
    """Parse The Events Calendar (Tribe) HTML for event data."""

    def __init__(self):
        super().__init__()
        self.events = []
        self.in_article = False
        self.current = {}
        self._tag_stack = []

    def _reset_current(self):
        self.current = {
            "name": "", "date": "", "time": "", "description": "",
            "url": "", "venue": "", "categories": [], "image": "",
            "uid": "", "_source": "html",
        }

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        classes = a.get("class", "")

        if tag == "article" and "type-tribe_events" in classes:
            self.in_article = True
            self._reset_current()
            self._tag_stack = []
            return

        if not self.in_article:
            return

        self._tag_stack.append(tag)

        if tag == "a" and classes and "tribe-event-url" in classes.split():
            self.current["url"] = a.get("href", "")

        if tag == "img" and not self.current["image"]:
            src = a.get("src", "")
            if src and "logo" not in src.lower():
                self.current["image"] = src

        if tag == "time":
            dt = a.get("datetime", "")
            if dt and len(dt) >= 10:
                self.current["date"] = dt[:10]

    def handle_endtag(self, tag):
        if tag == "article" and self.in_article:
            self.in_article = False
            if self.current.get("name"):
                self.events.append(self.current)
            self.current = {}
            self._tag_stack = []
            return
        if self._tag_stack and self._tag_stack[-1] == tag:
            self._tag_stack.pop()

    def handle_data(self, data):
        if not self._tag_stack:
            return
        stripped = data.strip()
        if not stripped:
            return
        if self._tag_stack and self._tag_stack[-1] in ("h4", "h3"):
            if stripped and not self.current.get("name"):
                name = stripped
                if "|" in name:
                    name = name.split("|")[-1].strip()
                self.current["name"] = name
        if self._tag_stack and self._tag_stack[-1] == "p":
            if "covers" in stripped.lower() or "acoustic" in stripped.lower():
                if self.current.get("description"):
                    self.current["description"] += "\n" + stripped
                else:
                    self.current["description"] = stripped


def parse_html_events(text):
    """Parse HTML page for event listings. Fallback method."""
    parser = TribeEventsParser()
    parser.feed(text)
    return parser.events


# ── Adapter: convert to Dragged Out schema ──


def _to_dragged_out_format(raw_events):
    """Convert raw scraper events to Dragged Out generator dicts."""
    results = []
    seen_uids = set()

    for ev in raw_events:
        # Dedup by uid
        uid = ev.get("uid", "")
        if uid and uid in seen_uids:
            continue
        if uid:
            seen_uids.add(uid)

        # Skip events without a name
        name = ev.get("name", "").strip()
        if not name:
            continue

        date_str = ev.get("date", "")
        if not date_str:
            continue

        # Parse day name from date
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            day_name = dt.strftime("%A")
        except ValueError:
            day_name = ""

        # Parse time range "HH:MM-HH:MM"
        time_str = ev.get("time", "")
        start = ""
        end = ""
        if time_str:
            tm = re.match(r"(\d{2}:\d{2})-(\d{2}:\d{2})", time_str)
            if tm:
                start = tm.group(1)
                end = tm.group(2)
            else:
                # Single time
                start = time_str

        # Build the event dict in generator.py schema
        results.append({
            "date": date_str,
            "day_name": day_name,
            "start": start,
            "end": end,
            "artist": name,
            "venue": VENUE_NAME,
            "venue_slug": VENUE_SLUG,
            "cost": "FREE",  # Two Flints gigs are free entry
            "source": SOURCE,
            "url": ev.get("url", ""),
            "cancelled": False,
            "repeating": False,
        })

    return results


# ── Scraper Orchestrator ──


def scrape(url=None, ical_only=False, html_only=False):
    """
    Main scrape entry point. Returns Dragged Out event dicts.

    Args:
        url: Ignored (for compatibility with generator.py dispatch signature).
        ical_only: Only use iCal method.
        html_only: Only use HTML method.

    Returns:
        list of event dicts matching generator.py schema.
    """
    raw_events = []

    try:
        if not html_only:
            print(f"  [web] Two Flints: fetching iCal feed", file=sys.stderr)
            ical_text = fetch(ICAL_URL)
            ical_events = parse_ical_events(ical_text)
            print(f"  [web] Two Flints: {len(ical_events)} events from iCal", file=sys.stderr)
            raw_events.extend(ical_events)
    except Exception as e:
        print(f"  [WARN] Two Flints iCal failed: {e}", file=sys.stderr)
        if ical_only:
            raise

    if not ical_only and len(raw_events) == 0:
        try:
            print(f"  [web] Two Flints: fetching HTML fallback", file=sys.stderr)
            html_text = fetch(HTML_URL)
            html_events = parse_html_events(html_text)
            print(f"  [web] Two Flints: {len(html_events)} events from HTML", file=sys.stderr)
            raw_events.extend(html_events)
        except Exception as e:
            print(f"  [WARN] Two Flints HTML fallback failed: {e}", file=sys.stderr)

    events = _to_dragged_out_format(raw_events)
    events.sort(key=lambda e: e["date"])
    print(f"  [web] Two Flints Brewery: {len(events)} events", file=sys.stderr)
    return events


# ── CLI entry point ──

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Scrape Two Flints Brewery events")
    parser.add_argument("--ical-only", action="store_true")
    parser.add_argument("--html-only", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    events = scrape(ical_only=args.ical_only, html_only=args.html_only)
    print(json.dumps(events, indent=2 if args.pretty else None, ensure_ascii=False))