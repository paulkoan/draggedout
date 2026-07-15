"""Unit tests for The Old Court scraper (scrapers/old_court.py).

Tests cover:
- OidCourtEvent dataclass validation, to_dict(), from_dict()
- All parsing helpers (parse_time_range, parse_date_from_text, etc.)
- scrape_old_court() with mocked HTTP responses
- scrape_old_court_music() music filtering
- Edge cases: empty page, malformed HTML, network errors
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import date
from unittest.mock import patch, MagicMock

import pytest
from bs4 import BeautifulSoup

# ── Module under test ──────────────────────────────────────────────────────

from scrapers.old_court import (
    OidCourtEvent,
    DAY_NAMES,
    MONTH_ABBR,
    OLD_COURT_BASE,
    _is_music_event,
    parse_time_range,
    parse_date_from_text,
    parse_event_id_from_url,
    parse_day_name,
    scrape_old_court,
    scrape_old_court_music,
)


# ═══════════════════════════════════════════════════════════════════════════
#  Fixtures
# ═══════════════════════════════════════════════════════════════════════════

# A minimal HTML page with one event card — used as the base for most tests.
SINGLE_EVENT_HTML = """\
<!DOCTYPE html>
<html>
<body>
<div>
<div>
<div>
<a href="/event/10839"><img src="https://tickets.example.org/img.jpg"></a>
<div>
<div>Blues Brothers Night</div>
<hr>
<span>Fri 10th Jul 21:00-23:00 <a href="https://tickets.example.org/bb">(tickets)</a></span>
</div>
</div>
</div>
</div>
</body>
</html>"""


# A full realistic page with multiple events, recurring events, no-link events
MULTI_EVENT_HTML = """\
<!DOCTYPE html>
<html>
<body>
<div>
<div>
<div>
<a href="/event/10839"><img src="https://tickets.example.org/bb.jpg"></a>
<div>
<div>Blues Brothers Night</div>
<hr>
<span>Fri 10th Jul 21:00-23:00 <a href="https://tickets.example.org/bb">(tickets)</a></span>
</div>
</div>
</div>
<div>
<a href="/event/11209"><img src="https://tickets.example.org/om.jpg"></a>
<div>
<div>Open Mic with Omari</div>
<hr>
<span>Wed 15th Jul 20:00-23:00</span><span>Wed 22nd Jul 20:00-23:00</span><span>Wed 29th Jul 20:00-23:00</span>
</div>
</div>
<div>
<a href="/event/11157"><img src="https://tickets.example.org/bolly.jpg"></a>
<div>
<div>Bollywood Day Party</div>
<hr>
<span>Sat 18th Jul 14:00-20:00</span>
</div>
</div>
<div>
<a href="/event/6278"><img src="https://tickets.example.org/soul.jpg"></a>
<div>
<div>Soul Stew</div>
<hr>
<span>Sun 12th Jul 14:00-19:00 <a href="https://tickets.example.org/soul">(info)</a></span>
</div>
</div>
<div>
<a href="/event/10618"><img src="https://tickets.example.org/yr3.jpg"></a>
<div>
<div>Yr 3 and 4 show</div>
<hr>
<span>Thu 9th Jul 18:00-20:30 <a href="https://tickets.example.org/yr3">(tickets)</a></span>
</div>
</div>
</div>
</div>
</body>
</html>"""


EMPTY_HTML = """\
<!DOCTYPE html>
<html>
<body>
<div>
<div>
<p>No events at this time.</p>
</div>
</div>
</body>
</html>"""


MALFORMED_HTML = """\
<!DOCTYPE html>
<html>
<body>
<div>
<div>
<p>Some broken content with no event cards at all</p>
</div>
</div>
</body>
</html>"""


@pytest.fixture
def mock_urlopen():
    """Patch urllib.request.urlopen to return controlled HTML."""
    with patch("urllib.request.urlopen") as mock:
        yield mock


def _mock_response(html: str, status: int = 200) -> MagicMock:
    """Build a urllib.response-compatible mock."""
    resp = MagicMock()
    resp.read.return_value = html.encode("utf-8")
    resp.__enter__.return_value = resp
    resp.status = status
    return resp


# ═══════════════════════════════════════════════════════════════════════════
#  OidCourtEvent — data model tests
# ═══════════════════════════════════════════════════════════════════════════


class TestOidCourtEvent:
    """Tests for the OidCourtEvent dataclass."""

    def test_valid_event(self):
        """Can create a valid event with all required fields."""
        ev = OidCourtEvent(
            title="Blues Brothers Night",
            date="2026-07-10",
            day_name="Friday",
            start="21:00",
            end="23:00",
        )
        assert ev.title == "Blues Brothers Night"
        assert ev.date == "2026-07-10"
        assert ev.day_name == "Friday"
        assert ev.start == "21:00"
        assert ev.end == "23:00"
        assert ev.venue == "The Old Court"  # default
        assert ev.venue_slug == "old-court"  # default
        assert ev.source == "web:old-court"  # default
        assert ev.cost == "?"  # default
        assert ev.repeating is False
        assert ev.cancelled is False
        assert ev.genre is None
        assert ev.youtube is None
        assert ev.event_id is None

    def test_empty_title_raises(self):
        """Title is required — empty string should raise."""
        with pytest.raises(ValueError, match="title is required"):
            OidCourtEvent(title="", date="2026-07-10", day_name="Friday", start="21:00", end="23:00")

    def test_whitespace_title_raises(self):
        """Title with only whitespace should raise."""
        with pytest.raises(ValueError, match="title is required"):
            OidCourtEvent(title="   ", date="2026-07-10", day_name="Friday", start="21:00", end="23:00")

    def test_invalid_date_format_raises(self):
        """Date must be YYYY-MM-DD format."""
        with pytest.raises(ValueError, match="date must be YYYY-MM-DD"):
            OidCourtEvent(title="Test", date="10-07-2026", day_name="Friday", start="21:00", end="23:00")

    def test_invalid_date_value_raises(self):
        """Date must be a real calendar date."""
        with pytest.raises(ValueError, match="invalid date"):
            OidCourtEvent(title="Test", date="2026-02-30", day_name="Monday", start="21:00", end="23:00")

    def test_invalid_day_name_raises(self):
        """day_name must be a full weekday name."""
        with pytest.raises(ValueError, match="day_name must be a full weekday name"):
            OidCourtEvent(title="Test", date="2026-07-10", day_name="Fri", start="21:00", end="23:00")

    def test_invalid_time_format_raises(self):
        """Start time must be HH:MM."""
        with pytest.raises(ValueError, match="start must be HH:MM"):
            OidCourtEvent(title="Test", date="2026-07-10", day_name="Friday", start="9:00", end="23:00")

    def test_empty_venue_raises(self):
        """Venue is required."""
        with pytest.raises(ValueError, match="venue is required"):
            OidCourtEvent(title="Test", date="2026-07-10", day_name="Friday", start="21:00", end="23:00", venue="")

    def test_empty_venue_slug_raises(self):
        """Venue slug is required."""
        with pytest.raises(ValueError, match="venue_slug is required"):
            OidCourtEvent(title="Test", date="2026-07-10", day_name="Friday", start="21:00", end="23:00", venue_slug="")

    def test_allows_empty_start_and_end(self):
        """Start and end times are optional (empty string allowed)."""
        ev = OidCourtEvent(title="Test", date="2026-07-10", day_name="Friday", start="", end="")
        assert ev.start == ""
        assert ev.end == ""

    def test_allows_empty_day_name(self):
        """Day name is optional."""
        ev = OidCourtEvent(title="Test", date="2026-07-10", day_name="", start="21:00", end="23:00")
        assert ev.day_name == ""

    def test_to_dict_shape(self):
        """to_dict() returns the expected generator-compatible dict."""
        ev = OidCourtEvent(
            title="Blues Brothers Night",
            date="2026-07-10",
            day_name="Friday",
            start="21:00",
            end="23:00",
            url="https://oldcourt.org.uk/event/10839",
            image_url="https://example.org/img.jpg",
            ticket_url="https://tickets.example.org/bb",
            ticket_type="tickets",
            event_id=10839,
        )
        d = ev.to_dict()
        assert d["artist"] == "Blues Brothers Night"
        assert d["date"] == "2026-07-10"
        assert d["day_name"] == "Friday"
        assert d["start"] == "21:00"
        assert d["end"] == "23:00"
        assert d["venue"] == "The Old Court"
        assert d["venue_slug"] == "old-court"
        assert d["cost"] == "?"
        assert d["source"] == "web:old-court"
        assert d["url"] == "https://oldcourt.org.uk/event/10839"
        assert d["image"] == "https://example.org/img.jpg"
        assert d["cancelled"] is False
        assert d["repeating"] is False
        assert d["genre"] is None
        assert d["youtube"] is None

    def test_from_dict_round_trip(self):
        """from_dict(to_dict()) preserves core fields.

        The generator dict shape (to_dict) only includes the fields
        that the generator pipeline uses — ticket_url, ticket_type,
        event_id, description, and recurrence_dates are not in the
        dict schema, so we only check fields that survive the round-trip.
        """
        ev = OidCourtEvent(
            title="Blues Brothers Night",
            date="2026-07-10",
            day_name="Friday",
            start="21:00",
            end="23:00",
            url="https://oldcourt.org.uk/event/10839",
            image_url="https://example.org/img.jpg",
            cost="£15",
            genre="Blues",
            youtube="https://youtube.com/blues",
        )
        d = ev.to_dict()
        ev2 = OidCourtEvent.from_dict(d)
        assert ev2.title == ev.title
        assert ev2.date == ev.date
        assert ev2.day_name == ev.day_name
        assert ev2.start == ev.start
        assert ev2.end == ev.end
        assert ev2.url == ev.url
        assert ev2.image_url == ev.image_url
        assert ev2.cost == ev.cost
        assert ev2.genre == ev.genre
        assert ev2.youtube == ev.youtube
        # ticket_url, ticket_type, event_id, description are not
        # in the generator dict shape — they stay at defaults
        assert ev2.ticket_url == ""
        assert ev2.ticket_type == ""
        assert ev2.event_id is None
        assert ev2.description == ""

    def test_from_dict_with_missing_fields(self):
        """from_dict() handles missing optional fields gracefully."""
        ev = OidCourtEvent.from_dict({"artist": "Test", "date": "2026-07-10", "day_name": "Friday", "start": "21:00", "end": "23:00"})
        assert ev.title == "Test"
        assert ev.venue == "The Old Court"  # default
        assert ev.source == "web:old-court"  # default
        assert ev.cost == "?"  # default
        assert ev.genre is None

    def test_recurring_event_round_trip(self):
        """Repeating event with recurrence_dates."""
        ev = OidCourtEvent(
            title="Open Mic",
            date="2026-07-15",
            day_name="Wednesday",
            start="20:00",
            end="23:00",
            repeating=True,
            recurrence_dates=["2026-07-15", "2026-07-22", "2026-07-29"],
        )
        d = ev.to_dict()
        assert d["repeating"] is True
        ev2 = OidCourtEvent.from_dict(d)
        assert ev2.repeating is True


# ═══════════════════════════════════════════════════════════════════════════
#  Parsing helpers
# ═══════════════════════════════════════════════════════════════════════════


class TestParseTimeRange:
    def test_standard_range(self):
        assert parse_time_range("18:00-20:30") == ("18:00", "20:30")

    def test_range_with_spaces(self):
        assert parse_time_range("21:00 - 23:00") == ("21:00", "23:00")

    def test_single_digit_hours(self):
        assert parse_time_range("9:00-17:30") == ("09:00", "17:30")

    def test_no_match(self):
        assert parse_time_range("All day") == ("", "")

    def test_empty_string(self):
        assert parse_time_range("") == ("", "")


class TestParseDateFromText:
    def test_standard_date(self):
        """Parses 'Fri 10th Jul' into YYYY-MM-DD."""
        result = parse_date_from_text("Fri 10th Jul")
        assert result.startswith("2026-")  # year depends on today
        assert result.endswith("-07-10")

    def test_no_ordinal_suffix(self):
        """'Thu 9 Jul' without ordinal suffix."""
        result = parse_date_from_text("Thu 9 Jul")
        assert result.endswith("-07-09")

    def test_st_nd_rd_th_suffixes(self):
        """Various ordinal suffixes."""
        assert parse_date_from_text("Mon 1st Jan").endswith("-01-01")
        assert parse_date_from_text("Tue 2nd Jan").endswith("-01-02")
        assert parse_date_from_text("Wed 3rd Jan").endswith("-01-03")
        assert parse_date_from_text("Thu 4th Jan").endswith("-01-04")

    def test_december_events_advance_year(self):
        """December events in current year advance to next year."""
        result = parse_date_from_text("Mon 25th Dec")
        # If today is before Dec, year stays; if after, year advances
        today = date.today()
        if today.month < 12:
            assert result.startswith(f"{today.year}-")
        else:
            # December or later — advance
            assert result.startswith(f"{today.year + 1}-")
        assert result.endswith("-12-25")

    def test_invalid_date_text(self):
        """Unparseable text returns empty string."""
        assert parse_date_from_text("Not a date") == ""

    def test_empty_string(self):
        assert parse_date_from_text("") == ""

    def test_case_insensitive_day(self):
        """Day abbreviation is case-insensitive."""
        result = parse_date_from_text("FRI 10th Jul")
        assert result.endswith("-07-10")


class TestParseEventIdFromUrl:
    def test_standard_event_url(self):
        assert parse_event_id_from_url("/event/10839") == 10839

    def test_full_url(self):
        assert parse_event_id_from_url("https://oldcourt.org.uk/event/10839") == 10839

    def test_no_match(self):
        assert parse_event_id_from_url("/events") is None

    def test_empty_string(self):
        assert parse_event_id_from_url("") is None


class TestParseDayName:
    def test_standard_abbreviation(self):
        assert parse_day_name("Thu 9th Jul") == "Thursday"

    def test_abbreviation_only(self):
        assert parse_day_name("Fri") == "Friday"

    def test_case_insensitive(self):
        assert parse_day_name("fri 10th jul") == "Friday"

    def test_no_match(self):
        assert parse_day_name("Not a day") == ""

    def test_empty_string(self):
        assert parse_day_name("") == ""


class TestIsMusicEvent:
    def test_live_music_keyword(self):
        assert _is_music_event("Live Music in the Bar") is True

    def test_dj_keyword(self):
        assert _is_music_event("Bar Beats: DJ Little Kate") is True

    def test_open_mic(self):
        assert _is_music_event("Open Mic with Omari") is True

    def test_band_keyword(self):
        assert _is_music_event("Blues Brothers Night") is True  # "blues" matches

    def test_tribute_keyword(self):
        assert _is_music_event("MoonAge - Bowie Tribute") is True

    def test_non_music_event(self):
        assert _is_music_event("Yr 3 and 4 show") is False

    def test_quiz_night(self):
        assert _is_music_event("Quiz Night with Dickie Jones") is False

    def test_dance_class(self):
        assert _is_music_event("Party Dance Class") is False

    def test_empty_title(self):
        assert _is_music_event("") is False


# ═══════════════════════════════════════════════════════════════════════════
#  scrape_old_court — HTTP mocking
# ═══════════════════════════════════════════════════════════════════════════


class TestScrapeOldCourt:
    """Tests for scrape_old_court() with mocked HTTP responses."""

    def test_single_event_card(self, mock_urlopen):
        """A single event card is parsed correctly."""
        mock_urlopen.return_value = _mock_response(SINGLE_EVENT_HTML)
        events = scrape_old_court()
        assert len(events) == 1
        ev = events[0]
        assert ev["artist"] == "Blues Brothers Night"
        assert ev["start"] == "21:00"
        assert ev["end"] == "23:00"
        assert ev["venue"] == "The Old Court"
        assert ev["venue_slug"] == "old-court"
        assert ev["source"] == "web:old-court"
        assert ev["repeating"] is False
        # Ticket URL should be present
        assert ev["ticket_url"] == "https://tickets.example.org/bb"
        assert ev["ticket_type"] == "tickets"
        # Image URL should be absolute
        assert ev["image"].startswith("http")

    def test_multiple_events(self, mock_urlopen):
        """Multiple events with different configurations."""
        mock_urlopen.return_value = _mock_response(MULTI_EVENT_HTML)
        events = scrape_old_court()
        # Blues Brothers (1 date) + Open Mic with Omari (3 dates)
        # + Bollywood Day Party (1) + Soul Stew (1) + Yr 3 and 4 show (1) = 7 events
        assert len(events) == 7

        # Check a sample event
        blues = [e for e in events if e["artist"] == "Blues Brothers Night"]
        assert len(blues) == 1
        assert blues[0]["start"] == "21:00"
        assert blues[0]["end"] == "23:00"
        assert blues[0]["repeating"] is False

        # Check recurring event — Open Mic
        open_mic = [e for e in events if e["artist"] == "Open Mic with Omari"]
        assert len(open_mic) == 3
        for om in open_mic:
            assert om["repeating"] is True
            assert "recurrence_dates" in om
        # All three should have the same recurrence_dates
        assert open_mic[0]["recurrence_dates"] == ["2026-07-15", "2026-07-22", "2026-07-29"]

        # Check event with info link (not tickets)
        soul = [e for e in events if e["artist"] == "Soul Stew"]
        assert len(soul) == 1
        assert soul[0]["ticket_type"] == "info"

        # Check event without any booking link
        bolly = [e for e in events if e["artist"] == "Bollywood Day Party"]
        assert len(bolly) == 1
        assert bolly[0]["ticket_url"] == ""
        assert bolly[0]["ticket_type"] == ""

    def test_events_sorted_by_date(self, mock_urlopen):
        """Events are returned sorted by date."""
        mock_urlopen.return_value = _mock_response(MULTI_EVENT_HTML)
        events = scrape_old_court()
        dates = [e["date"] for e in events]
        assert dates == sorted(dates)

    def test_empty_page(self, mock_urlopen):
        """Empty page with no event cards returns empty list."""
        mock_urlopen.return_value = _mock_response(EMPTY_HTML)
        events = scrape_old_court()
        assert events == []

    def test_malformed_html(self, mock_urlopen):
        """Malformed HTML with no event cards returns empty list."""
        mock_urlopen.return_value = _mock_response(MALFORMED_HTML)
        events = scrape_old_court()
        assert events == []

    def test_network_error_returns_empty(self, mock_urlopen):
        """Network error is caught and returns empty list."""
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")
        events = scrape_old_court()
        assert events == []

    def test_timeout_returns_empty(self, mock_urlopen):
        """Timeout raises socket.timeout converted to Exception."""
        mock_urlopen.side_effect = Exception("timed out")
        events = scrape_old_court()
        assert events == []

    def test_event_id_parsed_from_url(self, mock_urlopen):
        """Event ID is extracted from the /event/{id} URL."""
        mock_urlopen.return_value = _mock_response(SINGLE_EVENT_HTML)
        events = scrape_old_court()
        assert events[0]["event_id"] == 10839

    def test_relative_image_url_resolved(self, mock_urlopen):
        """Relative image src is resolved to absolute URL."""
        html = SINGLE_EVENT_HTML.replace(
            'src="https://tickets.example.org/img.jpg"',
            'src="/static/img.jpg"',
        )
        mock_urlopen.return_value = _mock_response(html)
        events = scrape_old_court()
        assert events[0]["image"].startswith("https://oldcourt.org.uk")

    def test_card_without_image(self, mock_urlopen):
        """Event card without an <img> tag should not crash."""
        html = SINGLE_EVENT_HTML.replace('<img src="https://tickets.example.org/img.jpg">', "")
        mock_urlopen.return_value = _mock_response(html)
        events = scrape_old_court()
        assert len(events) == 1
        assert events[0]["image"] == ""

    def test_card_without_title_div(self, mock_urlopen):
        """Event card missing the title div should be skipped."""
        # Remove the title div inside the card
        html = SINGLE_EVENT_HTML.replace("<div>Blues Brothers Night</div>", "")
        mock_urlopen.return_value = _mock_response(html)
        events = scrape_old_court()
        assert events == []

    def test_card_with_date_not_matching_pattern(self, mock_urlopen):
        """Event card with a date that doesn't match the expected pattern is skipped."""
        html = SINGLE_EVENT_HTML.replace(
            "Fri 10th Jul 21:00-23:00",
            "All day event",
        )
        mock_urlopen.return_value = _mock_response(html)
        events = scrape_old_court()
        assert events == []

    def test_uses_correct_url(self, mock_urlopen):
        """scrape_old_court always fetches from /events regardless of web_url param."""
        mock_urlopen.return_value = _mock_response(SINGLE_EVENT_HTML)
        scrape_old_court(web_url="https://oldcourt.org.uk/events/music/")
        # Verify the URL used
        call_args = mock_urlopen.call_args
        assert call_args is not None
        req = call_args[0][0]
        assert isinstance(req, urllib.request.Request)
        assert req.full_url == "https://oldcourt.org.uk/events"


# ═══════════════════════════════════════════════════════════════════════════
#  scrape_old_court_music
# ═══════════════════════════════════════════════════════════════════════════


class TestScrapeOldCourtMusic:
    """Tests for scrape_old_court_music() — music event filtering."""

    def test_music_events_filtered(self, mock_urlopen):
        """Only music events are returned."""
        mock_urlopen.return_value = _mock_response(MULTI_EVENT_HTML)
        music = scrape_old_court_music()
        # Music events from MULTI_EVENT_HTML (by keyword):
        # Blues Brothers Night → "blues" matches
        # Open Mic with Omari → "open mic" matches
        # Soul Stew → "soul" matches
        # Bollywood Day Party → NO
        # Yr 3 and 4 show → NO
        assert len(music) == 5  # Blues (1) + Open Mic (3 dates) + Soul Stew (1)
        titles = {e["artist"] for e in music}
        assert "Blues Brothers Night" in titles
        assert "Open Mic with Omari" in titles
        assert "Soul Stew" in titles
        assert "Bollywood Day Party" not in titles
        assert "Yr 3 and 4 show" not in titles

    def test_empty_page_returns_empty(self, mock_urlopen):
        """Empty page returns empty list."""
        mock_urlopen.return_value = _mock_response(EMPTY_HTML)
        assert scrape_old_court_music() == []

    def test_network_error_returns_empty(self, mock_urlopen):
        mock_urlopen.side_effect = Exception("Connection error")
        assert scrape_old_court_music() == []


# ═══════════════════════════════════════════════════════════════════════════
#  Integration-style: OidCourtEvent round-trip through scrape_old_court
# ═══════════════════════════════════════════════════════════════════════════


class TestScraperToEventRoundTrip:
    """Event dicts from scrape_old_court can be round-tripped via OidCourtEvent."""

    def test_event_dict_to_oid_court_event(self, mock_urlopen):
        """Each event dict from scrape_old_court can construct an OidCourtEvent."""
        mock_urlopen.return_value = _mock_response(MULTI_EVENT_HTML)
        events = scrape_old_court()
        for ev_dict in events:
            # Should not raise ValidationError
            oid_ev = OidCourtEvent.from_dict(ev_dict)
            # Round-trip back to dict
            rt_dict = oid_ev.to_dict()
            # Core fields match
            assert rt_dict["artist"] == ev_dict["artist"]
            assert rt_dict["date"] == ev_dict["date"]
            assert rt_dict["start"] == ev_dict["start"]
            assert rt_dict["end"] == ev_dict["end"]
            assert rt_dict["venue"] == ev_dict["venue"]
            assert rt_dict["venue_slug"] == ev_dict["venue_slug"]