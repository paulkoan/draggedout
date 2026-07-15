"""
Scraper for Other Space Arts Wix Events page.

Extracts events from:
  - Main page:  https://www.otherspacearts.com/up-coming-what-s-on
  - Sub-pages:  /food-and-pop-up-events, /dj-events

The site is Wix Thunderbolt (SSR). Each event is a <div> child of
<ul class="lT_atc">. Event detail pages have JSON-LD but we extract
everything from the listing HTML (no JS rendering needed).

Returns a list of dicts matching the Dragged Out generator.py schema.
"""

import re
import sys
from datetime import datetime, date as date_type

import urllib.request, urllib.error

# ── Constants ──

BASE_URL = "https://www.otherspacearts.com"
MAIN_PAGE = f"{BASE_URL}/up-coming-what-s-on"
SUB_PAGES = [
    "/food-and-pop-up-events",
    "/dj-events",
]

VENUE_NAME = "Other Space Arts"
VENUE_SLUG = "other-space-arts"
SOURCE = "web:other-space-arts"

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

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)


# ── Date/time parsing ──

def parse_date_from_card(date_text: str) -> tuple[str, str, str, str]:
    """Parse a Wix event card date string like '09 Jul 2026, 19:30 – 23:00'.

    Returns (date_yyyy_mm_dd, day_name, start_hhmm, end_hhmm).
    Any component that can't be parsed is returned as empty string.
    """
    date_text = date_text.strip()

    # Extract date portion: "09 Jul 2026"
    dm = re.match(
        r"(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{4})",
        date_text,
        re.I,
    )
    if not dm:
        return ("", "", "", "")

    day_num = int(dm.group(1))
    month_abbr = dm.group(2).lower()
    year = int(dm.group(3))
    month_num = MONTH_ABBR.get(month_abbr)

    if month_num is None:
        return ("", "", "", "")

    date_str = f"{year}-{month_num:02d}-{day_num:02d}"

    # Day name from date
    try:
        day_name = datetime(year, month_num, day_num).strftime("%A")
    except ValueError:
        day_name = ""

    # Extract time portion: "19:30 – 23:00" or "19:30-23:00"
    time_match = re.search(r"(\d{1,2}):(\d{2})\s*[–-]\s*(\d{1,2}):(\d{2})", date_text)
    if time_match:
        start = f"{int(time_match.group(1)):02d}:{time_match.group(2)}"
        end = f"{int(time_match.group(3)):02d}:{time_match.group(4)}"
    else:
        # Single time like "19:30" or just date with no time
        time_single = re.search(r"(\d{1,2}):(\d{2})", date_text)
        if time_single:
            start = f"{int(time_single.group(1)):02d}:{time_single.group(2)}"
        else:
            start = ""
        end = ""

    return (date_str, day_name, start, end)


# ── Main scraping logic ──

def scrape_page(url: str) -> list[dict]:
    """Scrape events from a single Wix events listing page.

    Returns a list of event dicts (generator.py schema).
    """
    # Fetch
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        resp = urllib.request.urlopen(req, timeout=15)
        html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  [WARN] other-space-arts: failed to fetch {url}: {e}", file=sys.stderr)
        return []

    # Parse with BeautifulSoup
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        print("  [WARN] other-space-arts: BeautifulSoup not available", file=sys.stderr)
        return []

    soup = BeautifulSoup(html, "html.parser")
    events = []

    # Find the event list container: <ul class="lT_atc">
    ul = soup.find("ul", class_="lT_atc")
    if not ul:
        print(f"  [WARN] other-space-arts: no <ul.lT_atc> found on {url}", file=sys.stderr)
        return []

    card_divs = ul.find_all("div", recursive=False)
    if not card_divs:
        # Fallback: just take direct children
        card_divs = ul.find_all(recursive=False)

    for card in card_divs:
        # Title link
        title_link = card.select_one("a.DjQEyU")
        if not title_link:
            # Fallback: any <a> with href containing event-details
            title_link = card.select_one('a[href*="event-details-registration"]')
        if not title_link:
            continue

        title = title_link.get_text(strip=True)
        if not title:
            continue

        # Event URL
        href = title_link.get("href", "")
        if href and href.startswith("/"):
            event_url = BASE_URL + href
        elif href and href.startswith("http"):
            event_url = href
        else:
            event_url = ""

        # Date text
        date_el = card.select_one('[data-hook="date"]')
        if not date_el:
            date_el = card.select_one('[data-hook="ev-full-date-location"]')
        date_text = date_el.get_text(strip=True) if date_el else ""

        date_str, day_name, start, end = parse_date_from_card(date_text)

        if not date_str:
            # If we can't parse the date, skip this event
            continue

        # Description
        desc_el = card.select_one("._srdnb")
        description = desc_el.get_text(strip=True) if desc_el else ""

        # Ribbon / badge — "Multiple Dates" badge means repeating
        ribbon_el = card.select_one('[data-hook="ribbon"]')
        is_recurring = bool(ribbon_el and "Multiple Dates" in ribbon_el.get_text())

        events.append({
            "date": date_str,
            "day_name": day_name,
            "start": start,
            "end": end,
            "artist": title,
            "venue": VENUE_NAME,
            "venue_slug": VENUE_SLUG,
            "cost": "",
            "source": SOURCE,
            "url": event_url,
            "cancelled": False,
            "repeating": is_recurring,
        })

    return events


def scrape(url: str = MAIN_PAGE) -> list[dict]:
    """Scrape Other Space Arts events from all listing pages.

    Args:
        url: Override the main page URL (used by generator.py dispatch).

    Returns:
        list of event dicts matching the generator.py schema.
    """
    all_events = []

    # Main page
    all_events.extend(scrape_page(MAIN_PAGE))

    # Sub-pages
    for sub in SUB_PAGES:
        sub_url = BASE_URL + sub
        all_events.extend(scrape_page(sub_url))

    # Deduplicate by URL (same event may appear on multiple pages)
    seen_urls: set[str] = set()
    deduped = []
    for ev in all_events:
        if ev["url"] not in seen_urls:
            seen_urls.add(ev["url"])
            deduped.append(ev)

    deduped.sort(key=lambda e: e["date"])
    print(f"  [web] {VENUE_NAME}: {len(deduped)} events ({len(all_events)} total, {len(all_events) - len(deduped)} dupes)", file=sys.stderr)
    return deduped


# ── CLI entry point ──

if __name__ == "__main__":
    import json
    events = scrape()
    print(json.dumps(events, indent=2))