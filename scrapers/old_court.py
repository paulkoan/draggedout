"""
Data model for The Old Court events.

This module defines the OidCourtEvent dataclass and helpers to parse events
from The Old Court's website (https://oldcourt.org.uk/events/music/).

The model matches the dict-based event schema used by the Dragged Out project
(generator.py) and provides a to_dict() method for seamless integration
with the existing pipeline.

Usage:
    from scrapers.old_court import OidCourtEvent

    # Create from raw data
    event = OidCourtEvent(
        title="Blues Brothers Night",
        date="2026-07-10",
        day_name="Friday",
        start="21:00",
        end="23:00",
        venue="The Old Court",
        venue_slug="old-court",
        url="https://oldcourt.org.uk/event/10839",
        image_url="https://...",
        ticket_url="https://tickets.oldcourt.org/...",
    )

    # Convert to generator-compatible dict
    event_dict = event.to_dict()
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional


MONTH_ABBR = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]

DAY_NAMES = [
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
]

DAY_ABBR = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


@dataclass
class OidCourtEvent:
    """A single event from The Old Court.

    All fields are validated on construction via __post_init__.
    Matches the dict schema used by the Dragged Out generator pipeline
    while preserving Old-Court-specific fields.

    Fields marked ``(enrichment)`` are populated by the genre/YouTube
    enrichment step in generator.py, not by the scraper.
    """

    # ── Core event fields (match generator.py event dict) ──
    title: str
    """Event title — maps to ``artist`` in the generator's dict schema."""

    date: str
    """Event date in ``YYYY-MM-DD`` format (ISO 8601)."""

    day_name: str
    """Full day-of-week name, e.g. ``\"Thursday\"``."""

    start: str
    """Start time in ``HH:MM`` 24-hour format, or empty string if unknown."""

    end: str
    """End time in ``HH:MM`` 24-hour format, or empty string if unknown."""

    venue: str = "The Old Court"
    """Venue display name."""

    venue_slug: str = "old-court"
    """Venue identifier used in URLs and slugs."""

    url: str = ""
    """Absolute URL to the event's detail page on oldcourt.org.uk."""

    image_url: str = ""
    """URL of the event's promotional image from the tickets system."""

    ticket_url: str = ""
    """URL to purchase tickets or view more info (tickets.oldcourt.org)."""

    ticket_type: str = ""
    """Link type: ``\"tickets\"``, ``\"info\"``, or ``\"\"`` if unknown."""

    source: str = "web:old-court"
    """Data source identifier — discriminates from Lemonrock/Instagram etc."""

    cost: str = "?"
    """Price or cost info. Default ``\"?\"`` means unknown; scraped from detail page."""

    # ── Recurrence ──
    repeating: bool = False
    """``True`` if the event spans multiple dates (e.g. weekly open mic)."""

    recurrence_dates: list[str] = field(default_factory=list)
    """All known dates for a recurring event, sorted as ``YYYY-MM-DD`` strings."""

    cancelled: bool = False
    """``True`` if the event has been cancelled."""

    # ── Enrichment fields (populated by generator.py later) ──
    genre: Optional[str] = None
    """Music genre (enrichment)."""

    youtube: Optional[str] = None
    """YouTube search URL for the artist/event (enrichment)."""

    description: str = ""
    """Event description or subtitle, if available."""

    event_id: Optional[int] = None
    """The Old Court's internal event ID (from the /event/{id} URL)."""

    def __post_init__(self) -> None:
        """Validate fields after construction."""
        errors: list[str] = []

        # Validate title
        if not self.title or not self.title.strip():
            errors.append("title is required")

        # Validate date format YYYY-MM-DD
        if self.date:
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", self.date):
                errors.append(f"date must be YYYY-MM-DD, got: {self.date!r}")
            else:
                try:
                    datetime.strptime(self.date, "%Y-%m-%d")
                except ValueError as e:
                    errors.append(f"invalid date: {e}")

        # Validate day_name
        if self.day_name and self.day_name not in DAY_NAMES:
            errors.append(
                f"day_name must be a full weekday name "
                f"(one of {DAY_NAMES}), got: {self.day_name!r}"
            )

        # Validate start/end time format HH:MM
        for field_name, value in [("start", self.start), ("end", self.end)]:
            if value and not re.match(r"^\d{2}:\d{2}$", value):
                errors.append(
                    f"{field_name} must be HH:MM 24-hour format, got: {value!r}"
                )

        # Validate venue
        if not self.venue or not self.venue.strip():
            errors.append("venue is required")

        # Validate venue_slug
        if not self.venue_slug or not self.venue_slug.strip():
            errors.append("venue_slug is required")

        if errors:
            raise ValueError(
                f"OidCourtEvent validation failed:\n  " + "\n  ".join(errors)
            )

    def to_dict(self) -> dict:
        """Convert to the existing dict-based event schema used by generator.py.

        Returns a dict with all the fields the generator pipeline expects,
        including enrichment slots that the band-info step fills in later.
        """
        return {
            "date": self.date,
            "day_name": self.day_name,
            "start": self.start,
            "end": self.end,
            "artist": self.title,
            "venue": self.venue,
            "venue_slug": self.venue_slug,
            "cost": self.cost,
            "source": self.source,
            "url": self.url,
            "cancelled": self.cancelled,
            "repeating": self.repeating,
            # Enrichment slots (set by generator.py → enrich_events)
            "genre": self.genre,
            "youtube": self.youtube,
            "image": self.image_url,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "OidCourtEvent":
        """Construct an OidCourtEvent from an existing generator event dict.

        This allows round-tripping: ``to_dict()`` → modify → ``from_dict()``.
        """
        return cls(
            title=d.get("artist", ""),
            date=d.get("date", ""),
            day_name=d.get("day_name", ""),
            start=d.get("start", ""),
            end=d.get("end", ""),
            venue=d.get("venue", "The Old Court"),
            venue_slug=d.get("venue_slug", "old-court"),
            url=d.get("url", ""),
            image_url=d.get("image", ""),
            cost=d.get("cost", "?"),
            source=d.get("source", "web:old-court"),
            cancelled=bool(d.get("cancelled", False)),
            repeating=bool(d.get("repeating", False)),
            genre=d.get("genre"),
            youtube=d.get("youtube"),
            description=d.get("description", ""),
        )


# ── Parsing helpers ──


def parse_time_range(text: str) -> tuple[str, str]:
    """Extract start and end times from a time-range string.

    Handles formats like ``18:00-20:30`` or ``21:00-23:00``.

    Returns ``(start_24h, end_24h)``, each as ``HH:MM`` or empty string.
    """
    m = re.search(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})", text)
    if m:
        return (f"{int(m.group(1)):02d}:{m.group(2)}", f"{int(m.group(3)):02d}:{m.group(4)}")
    return ("", "")


def parse_date_from_text(date_text: str) -> str:
    """Parse a date string like ``\"Thu 9th Jul\"`` into ``YYYY-MM-DD``.

    Uses the current year, advancing to the next year for months that
    have already passed (so December events still show in January).
    """
    from datetime import date as date_type

    m = re.match(
        r"(?i)(mon|tue|wed|thu|fri|sat|sun)\s+(\d{1,2})(?:st|nd|rd|th)?\s+"
        r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)",
        date_text.strip(),
    )
    if not m:
        return ""

    day_num = int(m.group(2))
    month_abbr = m.group(3).lower()
    month_num = MONTH_ABBR.get(month_abbr)

    if month_num is None:
        return ""

    today = date_type.today()
    year = today.year

    # If this month has already passed (and it's not already this month),
    # assume next year. This handles December events shown in January.
    iso_date = date_type(year, month_num, 1)
    if iso_date < date_type(today.year, today.month, 1):
        year += 1

    return f"{year}-{month_num:02d}-{day_num:02d}"


def parse_event_id_from_url(url: str) -> Optional[int]:
    """Extract the numeric event ID from an Old Court event URL.

    ``/event/10839`` → ``10839``. Returns ``None`` if no ID found.
    """
    m = re.search(r"/event/(\d+)", url)
    return int(m.group(1)) if m else None


def parse_day_name(text: str) -> str:
    """Extract the full day name from a date string like ``\"Thu 9th Jul\"``.

    Returns ``\"Thursday\"`` or empty string if unrecognised.
    """
    m = re.match(r"(?i)(mon|tue|wed|thu|fri|sat|sun)", text.strip())
    if m:
        idx = DAY_ABBR.get(m.group(1).lower())
        if idx is not None:
            return DAY_NAMES[idx]
    return ""


# ── Scraper ──


import urllib.request
import sys
from bs4 import BeautifulSoup


OLD_COURT_BASE = "https://oldcourt.org.uk"

# Keywords that identify music/live-music events (as opposed to classes, films, quizzes)
MUSIC_KEYWORDS = [
    "live music", "live in the bar", "open mic", "blues", "jazz",
    "tribute", "soul", "band", "dj ", "bar beats", "oasiz",
    "variety night", "sing", "acoustic", "rock", "folk",
]


def _is_music_event(title: str) -> bool:
    """Rough heuristic — is this event likely music/live-performance?

    The caller (scrape_all) can override this decision; we err on the
    side of inclusion so enrichment can filter downstream.
    """
    lower = title.lower()
    if any(kw in lower for kw in MUSIC_KEYWORDS):
        return True
    return False


def scrape_old_court(web_url: str = "") -> list[dict]:
    """Fetch all events from The Old Court /events page and return event dicts.

    ``web_url`` is accepted for compatibility with the generator dispatch;
    the scraper always fetches ``{OLD_COURT_BASE}/events`` which lists all
    upcoming events regardless of the venue-pinned URL.

    Each card on the page is a ``<div>`` containing:
      - ``<a href="/event/{id}"><img src="..."></a>``
      - ``<div>`` with title ``<div>``, ``<hr>``, and one or more
        ``<span>`` tags (date/time lines).

    Recurring events (e.g. Open Mic, Quiz Night) have multiple ``<span>``
    tags — each becomes a separate event dict with ``repeating=True`` and
    all recurrence dates recorded.

    Returns event dicts matching the generator.py schema (same dict shape
    as ``OidCourtEvent.to_dict()``).
    """
    url = f"{OLD_COURT_BASE}/events"
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"},
        )
        resp = urllib.request.urlopen(req, timeout=15)
        html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  [WARN] old-court: {e}", file=sys.stderr)
        return []

    soup = BeautifulSoup(html, "html.parser")
    events: list[dict] = []

    # Find all event-card wrappers: each has an <a> with href starting with /event/
    for a_tag in soup.find_all("a", href=re.compile(r"^/event/\d+")):
        card = a_tag.find_parent("div")
        if not card:
            continue

        # ── Image URL & event detail URL ──
        img_tag = a_tag.find("img")
        image_url = ""
        if img_tag and img_tag.get("src"):
            src = str(img_tag["src"])
            image_url = src if src.startswith("http") else f"{OLD_COURT_BASE}{src}"

        event_url = f"{OLD_COURT_BASE}{a_tag['href']}"
        event_id = parse_event_id_from_url(event_url)

        # ── Event title ──
        content_div = card.find("div")
        if not content_div:
            continue
        title_tag = content_div.find("div")
        if not title_tag:
            continue
        title = title_tag.get_text(strip=True)
        if not title:
            continue

        # ── Date/time spans ──
        # Each event has one or more <span> tags inside the content div.
        # Some spans have an <a> child (the booking link).
        span_tags = content_div.find_all("span", recursive=False)

        date_info_list: list[dict] = []
        for span in span_tags:
            text = span.get_text(" ", strip=True)
            if not text:
                continue

            # Extract booking link if present
            a_book = span.find("a")
            ticket_url = ""
            ticket_type = ""
            if a_book:
                href = str(a_book.get("href", ""))
                if href:
                    ticket_url = href if href.startswith("http") else f"{OLD_COURT_BASE}{href}"
                link_text = a_book.get_text(strip=True).lower()
                if "ticket" in link_text:
                    ticket_type = "tickets"
                elif "info" in link_text:
                    ticket_type = "info"

            # Parse the date/time string (before the booking link)
            date_time_str = text
            if a_book:
                book_text = a_book.get_text(strip=True)
                date_time_str = text.replace(book_text, "").strip()

            # Parse "Fri 10th Jul 21:00-23:30" or similar
            dm = re.match(
                r"(?i)(mon|tue|wed|thu|fri|sat|sun)\s+(\d+)(?:st|nd|rd|th)?\s+"
                r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+"
                r"(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})",
                date_time_str.strip(),
            )
            if not dm:
                continue

            day_name_full = parse_day_name(dm.group(1))
            date_iso = parse_date_from_text(f"{dm.group(1)} {dm.group(2)} {dm.group(3)}")
            if not date_iso:
                continue
            start = f"{int(dm.group(4)):02d}:{dm.group(5)}"
            end = f"{int(dm.group(6)):02d}:{dm.group(7)}"

            date_info_list.append({
                "date": date_iso,
                "day_name": day_name_full,
                "start": start,
                "end": end,
                "ticket_url": ticket_url or "",
                "ticket_type": ticket_type or "",
            })

        if not date_info_list:
            continue

        is_recurring = len(date_info_list) > 1
        all_dates = sorted(set(d["date"] for d in date_info_list))

        for di in date_info_list:
            ev = {
                "date": di["date"],
                "day_name": di["day_name"],
                "start": di["start"],
                "end": di["end"],
                "artist": title,
                "venue": "The Old Court",
                "venue_slug": "old-court",
                "cost": "?",
                "source": "web:old-court",
                "url": event_url,
                "image": image_url,
                "ticket_url": di["ticket_url"],
                "ticket_type": di["ticket_type"],
                "cancelled": False,
                "repeating": is_recurring,
                "description": "",
                "genre": None,
                "youtube": None,
                "event_id": event_id,
            }
            if is_recurring:
                ev["recurrence_dates"] = all_dates
            events.append(ev)

    print(f"  [web] The Old Court: {len(events)} events", file=sys.stderr)
    events.sort(key=lambda e: e["date"])
    return events


def scrape_old_court_music(web_url: str = "") -> list[dict]:
    """Convenience: return only music/live-performance events from The Old Court.

    Delegates to :func:`scrape_old_court` and filters by magic keywords.
    ``web_url`` is accepted for compatibility with the generator dispatch.
    """
    all_events = scrape_old_court()
    music = [e for e in all_events if _is_music_event(e["artist"])]
    print(
        f"  [web] The Old Court (music only): {len(music)}/{len(all_events)} events",
        file=sys.stderr,
    )
    return music