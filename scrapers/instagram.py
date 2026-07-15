"""
Instagram scraper for Dragged Out — extracts music-related posts from venue profiles.

Instagram does not provide a public, anonymous API. This module supports multiple
extraction strategies ranked by reliability:

  Strategy 1 (BEST):  Browser-based via Hermes browser_console. Use `extract_from_browser()`
                      in a Hermes session. Works for profiles that aren't age-restricted.
  Strategy 2 (GOOD):  Instaloader with a saved login session. Requires valid Instagram
                      credentials and periodic session refresh. See `scrape_with_instaloader()`.
  Strategy 3 (OK):    Playwright headless browser. Requires `playwright install chromium`.
                      See `scrape_with_playwright()`.
  Strategy 4 (NONE):  requests + BeautifulSoup. Instagram 100% client-rendered;
                      no server-side HTML available. Will NOT work.

Usage (from generator.py dispatch):
    from scrapers.instagram import scrape_instagram
    events = scrape_instagram("thefoxandcastle", "The Fox & Castle", "fox-castle")

Usage (CLI test):
    python3 -m scrapers.instagram thefoxandcastle --name "The Fox & Castle" --slug fox-castle

Usage (Hermes browser session):
    python3 -m scrapers.instagram thefoxandcastle --browser-data <json-file>
"""

from __future__ import annotations

import json
import os
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# ── Generator-compatible event fields ──
EVENT_FIELDS = [
    "date", "day_name", "start", "end",
    "artist", "venue", "venue_slug",
    "cost", "source", "url",
    "cancelled", "repeating",
]

# Keywords that indicate music content
MUSIC_KEYWORDS = [
    "live music", "open mic", "acoustic", "gig", "band", "performer",
    "performing", "singer", "songwriter", "on stage", "dj",
    "karaoke", "jam session", "music night", "live band",
    "rock", "blues", "jazz", "folk", "punk", "metal",
    "soul", "r&b", "hip hop", "reggae", "indie",
]

# Date patterns commonly found in Instagram captions
DATE_PATTERNS = [
    # "Thursday 18th September" or "Thursday 18 Sept"
    re.compile(
        r"(?:📅|Date|When|Takes place)\s*[:\s]*"
        r"(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s+"
        r"(\d{1,2})(?:st|nd|rd|th)?\s+"
        r"(January|February|March|April|May|June|July|"
        r"August|September|October|November|December|"
        r"Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*"
        r"(?:\s*,?\s*(\d{4}))?",
        re.IGNORECASE,
    ),
    # "Saturday 11 July" (bare weekday + day + month)
    re.compile(
        r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)(?:day)?\s+"
        r"(\d{1,2})(?:st|nd|rd|th)?\s+"
        r"(January|February|March|April|May|June|July|"
        r"August|September|October|November|December|"
        r"Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b",
        re.IGNORECASE,
    ),
    # "24th June" (bare date)
    re.compile(
        r"\b(\d{1,2})(?:st|nd|rd|th)?\s+"
        r"(January|February|March|April|May|June|July|"
        r"August|September|October|November|December|"
        r"Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b",
        re.IGNORECASE,
    ),
    # DD/MM/YYYY or DD.MM.YYYY
    re.compile(r"\b(\d{1,2})[./](\d{1,2})[./](\d{4})\b"),
    # ISO date YYYY-MM-DD
    re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"),
]

MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]
MONTH_ABBR = {m[:3].lower(): i + 1 for i, m in enumerate(MONTH_NAMES)}
MONTH_FULL = {m.lower(): i + 1 for i, m in enumerate(MONTH_NAMES)}
MONTH_MAP = {**MONTH_FULL, **MONTH_ABBR}

DAY_NAMES = [
    "Monday", "Tuesday", "Wednesday",
    "Thursday", "Friday", "Saturday", "Sunday",
]


def is_music_post(caption: str) -> bool:
    """Check if a caption mentions music-related keywords."""
    if not caption:
        return False
    lower = caption.lower()
    return any(kw in lower for kw in MUSIC_KEYWORDS)


def extract_date_from_caption(caption: str) -> Optional[str]:
    """Try to extract a date (YYYY-MM-DD) from an Instagram caption.

    Returns None if no date can be parsed.
    """
    if not caption:
        return None

    now = datetime.now()
    current_year = now.year

    for pattern in DATE_PATTERNS:
        m = pattern.search(caption)
        if not m:
            continue

        groups = m.groups()

        # Pattern 1: "Thursday 18th September" or with year
        if len(groups) == 4:
            day_num = int(groups[1])
            month_name = groups[2]
            year_str = groups[3]

            month_name_lower = month_name.strip().lower()[:3]
            month_num = MONTH_MAP.get(month_name_lower)
            if not month_num:
                continue

            year = int(year_str) if year_str else current_year
            return f"{year}-{month_num:02d}-{day_num:02d}"

        # Pattern 2: weekday + day + month (no year)
        # Actually this is tricky because weekday is captured but day/month aren't grouped
        # Let me handle this differently

    # Fallback: try a simpler approach with regex splitting
    # "Thursday 18th September" -> day=18, month=September
    dm = re.search(
        r"(\d{1,2})(?:st|nd|rd|th)?\s+(Jan(?:uary)?|Feb(?:ruary)?|"
        r"Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|"
        r"Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)",
        caption, re.IGNORECASE,
    )
    if dm:
        day_num = int(dm.group(1))
        month_name = dm.group(2)[:3].lower()
        month_num = MONTH_MAP.get(month_name)
        if month_num:
            # Determine year: current year unless month has passed
            year = current_year
            if month_num < now.month:
                year += 1
            return f"{year}-{month_num:02d}-{day_num:02d}"

    return None


def extract_time_from_caption(caption: str) -> str:
    """Extract time like '8pm', '8:00pm', '20:00' from caption."""
    if not caption:
        return ""

    # Match time patterns: "at 8pm", "starts 8pm", "8:30pm", "from 8pm", etc.
    # or bare "8pm" / "8:30pm" preceded by space or at start
    m = re.search(
        r"(?:"
        r"(?:from|at|starts|🕗|⏰|⌚)\s*"
        r"(\d{1,2})(?::(\d{2}))?\s*(pm|am)?"
        r"|"
        r"(?:^|\s)(\d{1,2}):(\d{2})\s*(pm|am)"
        r"|"
        r"(?:^|\s)(\d{1,2})\s*(pm|am)(?:\s|$)"
        r")",
        caption, re.IGNORECASE,
    )
    if m:
        groups = m.groups()
        if groups[0] is not None:
            h, mi, a = int(groups[0]), groups[1] or "00", (groups[2] or "").lower()
        elif groups[3] is not None:
            h, mi, a = int(groups[3]), groups[4], (groups[5] or "").lower()
        elif groups[6] is not None:
            h, mi, a = int(groups[6]), "00", (groups[7] or "").lower()
        else:
            return ""
        if a == "pm" and h < 12:
            h += 12
        elif a == "am" and h == 12:
            h = 0
        return f"{h:02d}:{mi}"
    return ""


def extract_artist_from_caption(caption: str) -> str:
    """Try to extract the headliner/performer name from caption."""
    if not caption:
        return caption or ""

    # Look for artist name patterns
    patterns = [
        r"featuring\s+(.+?)(?:\.|!|$)",
        r"starring\s+(.+?)(?:\.|!|$)",
        r"performed by\s+(.+?)(?:\.|!|$)",
        r"with\s+(.+?)(?:\.|!|$)(?:\s+on)",
    ]
    for pat in patterns:
        m = re.search(pat, caption, re.IGNORECASE)
        if m:
            return m.group(1).strip().rstrip(".")

    # Return first line as "title"
    first_line = caption.split("\n")[0].strip()
    return first_line[:100] if first_line else caption[:100]


def normalize_post_to_event(
    post: dict[str, Any],
    vname: str,
    vslug: str,
) -> Optional[dict[str, Any]]:
    """Convert an Instagram post dict to a generator.py-compatible event dict.

    Expected post fields: href, caption, timestamp, img_url
    """
    caption = post.get("caption", "") or ""
    href = post.get("href", "") or ""

    # Only include music-related posts
    if not is_music_post(caption):
        return None

    date_str = extract_date_from_caption(caption)
    if not date_str:
        return None

    start = extract_time_from_caption(caption)
    artist = extract_artist_from_caption(caption)

    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        day_name = dt.strftime("%A")
    except ValueError:
        day_name = ""

    return {
        "date": date_str,
        "day_name": day_name,
        "start": start,
        "end": "",
        "artist": artist,
        "venue": vname,
        "venue_slug": vslug,
        "cost": "",
        "source": f"instagram:{vslug}",
        "url": href,
        "cancelled": False,
        "repeating": False,
    }


# ═══════════════════════════════════════════════════
#  STRATEGY 1: Browser data (JSON file from browser_console)
# ═══════════════════════════════════════════════════

def scrape_from_browser_data(
    browser_data_path: str,
    vname: str,
    vslug: str,
) -> list[dict[str, Any]]:
    """Parse Instagram post data from a JSON file extracted via browser_console.

    How to use in a Hermes session:
        1. Navigate to https://www.instagram.com/<username>/
        2. Accept cookies
        3. Run in browser_console:
           JSON.stringify(
             Array.from(document.querySelectorAll('a[href*=\"/p/\"]'))
               .map(a => ({
                 href: a.href,
                 caption: a.querySelector('img')?.alt || ''
               }))
           )
        4. Save output to a file and pass path here.
    """
    with open(browser_data_path) as f:
        posts = json.load(f)

    events = []
    for post in posts:
        ev = normalize_post_to_event(post, vname, vslug)
        if ev:
            events.append(ev)

    events.sort(key=lambda e: e["date"])
    return events


# ═══════════════════════════════════════════════════
#  STRATEGY 2: Instaloader (requires login session)
# ═══════════════════════════════════════════════════

def scrape_with_instaloader(
    username: str,
    vname: str,
    vslug: str,
    session_file: Optional[str] = None,
    max_posts: int = 50,
) -> list[dict[str, Any]]:
    """Scrape Instagram profile using Instaloader with a saved session.

    Requires a valid Instagram session file. To create one:
        instaloader --login=your_username

    Args:
        username: Instagram handle (e.g. 'thefoxandcastle')
        vname: Venue display name
        vslug: Venue slug
        session_file: Path to saved session file. If None, tries anonymous.
        max_posts: Maximum posts to fetch.
    """
    try:
        import instaloader
    except ImportError:
        print("  [WARN] instaloader not installed. Run: uv pip install instaloader", file=sys.stderr)
        return []

    L = instaloader.Instaloader(
        user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    )

    if session_file and os.path.exists(session_file):
        try:
            L.load_session_from_file(username=None, sessionfile=session_file)
            print(f"  [ig] loaded session from {session_file}", file=sys.stderr)
        except Exception as e:
            print(f"  [WARN] failed to load session: {e}", file=sys.stderr)

    try:
        profile = instaloader.Profile.from_username(L.context, username)
    except instaloader.exceptions.ProfileNotExistsException:
        print(f"  [WARN] ig/{username}: profile not found (or 403)", file=sys.stderr)
        return []
    except instaloader.exceptions.ConnectionException as e:
        print(f"  [WARN] ig/{username}: connection error: {e}", file=sys.stderr)
        return []
    except Exception as e:
        print(f"  [WARN] ig/{username}: {e}", file=sys.stderr)
        return []

    events = []
    for i, post in enumerate(profile.get_posts()):
        if i >= max_posts:
            break

        caption = post.caption or ""
        ev = normalize_post_to_event(
            {"href": f"https://instagram.com/p/{post.shortcode}/", "caption": caption},
            vname, vslug,
        )
        if ev:
            events.append(ev)

        # Respect rate limits
        if i > 0 and i % 10 == 0:
            time.sleep(1)

    events.sort(key=lambda e: e["date"])
    print(f"  [ig] {vname}: {len(events)} music events from {username}", file=sys.stderr)
    return events


# ═══════════════════════════════════════════════════
#  STRATEGY 3: Playwright headless browser
# ═══════════════════════════════════════════════════

def scrape_with_playwright(
    username: str,
    vname: str,
    vslug: str,
) -> list[dict[str, Any]]:
    """Scrape Instagram profile using Playwright headless browser.

    Requires: playwright installed + `playwright install chromium`
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  [WARN] playwright not installed.", file=sys.stderr)
        return []

    events = []

    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
            except Exception as e:
                if "Executable doesn't exist" in str(e):
                    print(f"  [WARN] ig/{username}: chromium not installed (run: playwright install chromium)", file=sys.stderr)
                else:
                    print(f"  [WARN] ig/{username}: playwright launch failed: {e}", file=sys.stderr)
                return []

            try:
                context = browser.new_context(
                    user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    viewport={"width": 1280, "height": 800},
                )
                page = context.new_page()

                page.goto(f"https://www.instagram.com/{username}/", timeout=30000)
                page.wait_for_timeout(3000)

                # Accept cookies if dialog appears
                try:
                    cookie_btn = page.get_by_role("button", name="Allow all cookies")
                    if cookie_btn.is_visible(timeout=3000):
                        cookie_btn.click()
                        page.wait_for_timeout(2000)
                except Exception:
                    pass

                # Wait for posts to load
                try:
                    page.wait_for_selector("article a[href*='/p/']", timeout=15000)
                except Exception:
                    print(f"  [WARN] ig/{username}: no posts loaded (may be restricted)", file=sys.stderr)
                    browser.close()
                    return []

                # Extract posts
                posts = page.evaluate("""
                    Array.from(document.querySelectorAll('a[href*="/p/"]')).map(a => ({
                        href: a.href,
                        caption: a.querySelector('img')?.alt || ''
                    }))
                """)

                for post in posts:
                    ev = normalize_post_to_event(post, vname, vslug)
                    if ev:
                        events.append(ev)

            except Exception as e:
                print(f"  [WARN] ig/{username}: playwright error: {e}", file=sys.stderr)
            finally:
                browser.close()
    except Exception as e:
        print(f"  [WARN] ig/{username}: playwright setup failed: {e}", file=sys.stderr)
        return []

    events.sort(key=lambda e: e["date"])
    print(f"  [ig] {vname}: {len(events)} music events from {username} (playwright)", file=sys.stderr)
    return events


# ═══════════════════════════════════════════════════
#  STRATEGY 4: curl_cffi (Instagram internal REST API — BEST for automation)
# ═══════════════════════════════════════════════════

# Instagram internal API constants
INSTAGRAM_APP_ID = "936619743392459"
IG_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
IG_HEADERS = {
    "x-ig-app-id": INSTAGRAM_APP_ID,
    "User-Agent": IG_USER_AGENT,
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.instagram.com/",
}

# Event keywords for filtering
EVENT_KEYWORDS = [
    "open mic", "open-mic", "gig", "show", "live music",
    "quiz night", "quiz", "bbq", "charity",
    "kick-off", "match", "performance",
    "stage is yours", "the stage",
]


def _ig_api_get(url: str) -> dict:
    """Make a GET request to Instagram's internal API using curl_cffi."""
    from curl_cffi import requests as curl_requests

    max_retries = 5
    for attempt in range(max_retries):
        try:
            resp = curl_requests.get(
                url,
                headers=IG_HEADERS,
                impersonate="chrome120",
                timeout=30,
            )
            if resp.status_code == 429:
                wait = 2 ** (attempt + 1) + random.uniform(0, 1)
                print(f"  [ig] Rate limited (429), waiting {wait:.1f}s (attempt {attempt + 1}/{max_retries})...", file=sys.stderr)
                time.sleep(wait)
                continue
            if resp.status_code != 200:
                print(f"  [ig] HTTP {resp.status_code} from {url}: {resp.text[:200]}", file=sys.stderr)
                return {}
            return resp.json()
        except Exception as e:
            print(f"  [ig] Request error: {e}", file=sys.stderr)
            if attempt < max_retries - 1:
                wait = 2 ** (attempt + 1)
                print(f"  [ig] Retrying in {wait}s...", file=sys.stderr)
                time.sleep(wait)
                continue
            raise
    print(f"  [ig] Error: Exhausted retries for {url}", file=sys.stderr)
    return {}


def _ig_get_profile_info(username: str) -> dict | None:
    """Fetch Instagram profile metadata and return the user object."""
    url = f"https://i.instagram.com/api/v1/users/web_profile_info/?username={username}"
    data = _ig_api_get(url)
    user = data.get("data", {}).get("user")
    if not user:
        print(f"  [ig] Could not find profile @{username}", file=sys.stderr)
        return None
    return user


def _ig_get_user_feed(user_id: str, max_posts: int = 30) -> list[dict]:
    """Fetch recent posts from a user's Instagram feed with pagination."""
    items = []
    next_max_id = None

    while len(items) < max_posts:
        if next_max_id:
            url = f"https://i.instagram.com/api/v1/feed/user/{user_id}/?max_id={next_max_id}"
        else:
            url = f"https://i.instagram.com/api/v1/feed/user/{user_id}/"

        data = _ig_api_get(url)
        if not data:
            break
        batch = data.get("items", [])
        items.extend(batch)

        print(f"  [ig] Fetched {len(batch)} posts (total: {len(items)})", file=sys.stderr)

        if not data.get("more_available"):
            break
        next_max_id = data.get("next_max_id")
        if not next_max_id:
            break
        time.sleep(1.0)

    return items[:max_posts]


def _ig_parse_date_time(text: str) -> tuple[str | None, str | None]:
    """Extract date and time from Instagram caption text."""
    if not text:
        return None, None

    date = None
    time_val = None

    # Date patterns — ordered by specificity
    date_patterns = [
        # With 📅 emoji: "📅 Saturday 11 July"
        r"📅\s*\w+\s+\d{1,2}(?:st|nd|rd|th)?\s+\w+",
        # With other emoji or standalone date
        r"(?:on\s+)?(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\w*\s+(?:this\s+)?\d{1,2}(?:st|nd|rd|th)?\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*",
        # "Thursday 31st July" with "this" prefix
        r"(?:this\s+)?(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\w*\s+\d{1,2}(?:st|nd|rd|th)?\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*",
        # DD Month YYYY: "17th April 2025"
        r"\d{1,2}(?:st|nd|rd|th)?\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+\d{4}",
    ]

    for pattern in date_patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            date = m.group(0).strip()
            date = re.sub(r'^on\s+', '', date, flags=re.IGNORECASE).strip()
            break

    # Time patterns
    time_patterns = [
        r"🕙\s*\w+[-–]\w+:\s*\d{1,2}(?::\d{2})?\s*(?:am|pm)?",
        r"🕙\s*\w+\s+from\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?",
        r"(?:🕙|🕗)\s*[\w\s]*?\d{1,2}(?::\d{2})?(?:\s*(?:am|pm))?",
        r"Kick-off:\s*\d{1,2}(?::\d{2})?(?:\s*(?:am|pm))?",
        r"⏰\s*(\w+\s+)?\d{1,2}(?::\d{2})?\s*(?:am|pm)?",
        r"Performances?\s+from\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?",
        r"(?:from|starts?)\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?",
        r"\d{1,2}:\d{2}\s*(?:am|pm)",
        r"\d{1,2}\s*(?:am|pm)\b",
    ]
    for pattern in time_patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            time_val = m.group(0).strip()
            break

    return date, time_val


def _ig_parse_date_text_to_iso(date_text: str) -> str | None:
    """Convert Instagram date text like 'Saturday 11 July' to YYYY-MM-DD."""
    if not date_text:
        return None

    now = datetime.now()
    current_year = now.year

    # Strip emoji and prefixes
    cleaned = date_text
    cleaned = re.sub(r'^\s*📅\s*', '', cleaned)
    cleaned = cleaned.strip()

    # Match "Saturday 11 July" or "11th July" or "11 July"
    dm = re.match(
        r'(?:(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\w*\s+)?'
        r'(\d{1,2})(?:st|nd|rd|th)?\s+'
        r'(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|'
        r'Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)'
        r'(?:\s+(\d{4}))?',
        cleaned, re.IGNORECASE
    )
    if not dm:
        return None

    day_num = int(dm.group(1))
    month_str = dm.group(2)[:3].lower()
    month_num = MONTH_MAP.get(month_str)
    if not month_num:
        return None

    year = int(dm.group(3)) if dm.group(3) else current_year

    # If the month has already passed this year, assume next year
    if not dm.group(3) and month_num < now.month:
        year += 1

    return f"{year}-{month_num:02d}-{day_num:02d}"


def _ig_parse_time_text_to_24h(time_text: str) -> str:
    """Convert Instagram time text like '8pm', 'starts 8pm' to '20:00'."""
    if not time_text:
        return ""

    # Strip emoji/prefixes
    cleaned = re.sub(r'^[⏰🕙🕗]+\s*', '', time_text)
    cleaned = re.sub(r'^(Kick-off|Performances?\s+from|from|starts?)\s*:\s*', '', cleaned, flags=re.IGNORECASE).strip()

    m = re.match(r'(\d{1,2})(?::(\d{2}))?\s*(pm|am)?', cleaned, re.IGNORECASE)
    if m:
        h = int(m.group(1))
        mi = m.group(2) or "00"
        a = (m.group(3) or "").lower()
        if a == "pm" and h < 12:
            h += 12
        elif a == "am" and h == 12:
            h = 0
        return f"{h:02d}:{mi}"

    return ""


def _ig_is_event_post(caption: str) -> bool:
    """Check if a caption indicates an event worth listing."""
    if not caption:
        return False
    caption_lower = caption.lower()
    return any(kw in caption_lower for kw in EVENT_KEYWORDS)


def scrape_with_curl_cffi(
    username: str,
    vname: str,
    vslug: str,
    max_posts: int = 30,
) -> list[dict[str, Any]]:
    """Scrape Instagram profile via internal REST API using curl_cffi TLS impersonation.

    This is the most reliable programmatic approach — no login required for
    public profiles, bypasses Instagram's bot detection via Chrome 120 TLS
    fingerprinting.

    Returns generator-compatible event dicts.
    """
    print(f"  [ig/{username}] curl_cffi strategy...", file=sys.stderr)

    try:
        from curl_cffi import requests as curl_requests  # noqa: F401
    except ImportError:
        print("  [WARN] curl_cffi not installed. Run: pip install curl_cffi", file=sys.stderr)
        return []

    user = _ig_get_profile_info(username)
    if not user:
        return []

    user_id = user["id"]
    full_name = user.get("full_name", "")
    follower_count = user.get("edge_followed_by", {}).get("count", 0)
    print(f"  [ig/{username}] {full_name} — {follower_count} followers", file=sys.stderr)

    posts = _ig_get_user_feed(user_id, max_posts)
    print(f"  [ig/{username}] Fetched {len(posts)} posts total", file=sys.stderr)

    events = []
    for post in posts:
        caption_data = post.get("caption")
        caption = ""
        if isinstance(caption_data, dict):
            caption = caption_data.get("text", "") or ""
        elif isinstance(caption_data, str):
            caption = caption_data

        code = post.get("code", "")
        if not caption or not code:
            continue

        # Skip non-event posts
        if not _ig_is_event_post(caption):
            continue

        # Extract date/time
        date_text, time_text = _ig_parse_date_time(caption)
        date_iso = _ig_parse_date_text_to_iso(date_text) if date_text else None
        start = _ig_parse_time_text_to_24h(time_text) if time_text else ""

        if not date_iso:
            # Still include if it has strong event keywords but no parsed date
            # (the date might be in a non-standard format)
            if any(kw in caption.lower() for kw in ["open mic", "quiz night", "quiz"]):
                # Use timestamp from the post itself
                ts = post.get("taken_at")
                if ts:
                    dt = datetime.fromtimestamp(ts)
                    date_iso = dt.strftime("%Y-%m-%d")
                    start = ""

        if not date_iso:
            continue

        # Extract day name
        try:
            dt = datetime.strptime(date_iso, "%Y-%m-%d")
            day_name = dt.strftime("%A")
        except ValueError:
            day_name = ""

        # Determine artist/title — first non-empty line of caption, cleaned
        lines = caption.split("\n")
        first_line = ""
        for line in lines:
            stripped = line.strip()
            if stripped:
                first_line = stripped
                break
        # Strip leading emoji characters
        artist = re.sub(r'^[\U0001F300-\U0001FFFD\u2600-\u27FF\uFE00-\uFE0F🏴🧠🎤✨🍻🍀🐣🦢🍀⚽🎶]+[\s]{0,2}', '', first_line).strip()
        if not artist:
            artist = first_line[:100]
        else:
            artist = artist[:100]

        events.append({
            "date": date_iso,
            "day_name": day_name,
            "start": start,
            "end": "",
            "artist": artist.strip().rstrip(","),
            "venue": vname,
            "venue_slug": vslug,
            "cost": "",
            "source": f"instagram:{vslug}",
            "url": f"https://www.instagram.com/p/{code}/",
            "cancelled": False,
            "repeating": "quiz" in caption.lower() or "open mic" in caption.lower(),
        })

    events.sort(key=lambda e: e["date"])
    print(f"  [ig/{username}] {vname}: {len(events)} events from curl_cffi", file=sys.stderr)
    return events


# ═══════════════════════════════════════════════════
#  AUTO-DISPATCH: try strategies in order of reliability
# ═══════════════════════════════════════════════════

def scrape_instagram(
    username: str,
    vname: str,
    vslug: str,
    session_file: Optional[str] = None,
    browser_data_file: Optional[str] = None,
    prefer_playwright: bool = False,
) -> list[dict[str, Any]]:
    """Scrape Instagram for music events, trying available strategies.

    Strategy priority:
        0. curl_cffi (Instagram REST API — no login, most reliable)
        1. Browser data file (if provided — manual Hermes extraction)
        2. Instaloader with session
        3. Playwright headless (if chromium installed)
        4. Quiet failure

    Args:
        username: Instagram handle
        vname: Venue display name
        vslug: Venue slug
        session_file: Path to Instaloader session file
        browser_data_file: Path to JSON file from browser_console extraction
        prefer_playwright: Try Playwright before Instaloader

    Returns:
        List of event dicts compatible with generator.py
    """
    # Strategy 0: curl_cffi (Instagram REST API — best for automation)
    try:
        events = scrape_with_curl_cffi(username, vname, vslug)
        if events:
            return events
    except ImportError:
        print("  [ig] curl_cffi not available, trying other strategies...", file=sys.stderr)

    # Strategy 1: browser data (explicitly provided)
    if browser_data_file and os.path.exists(browser_data_file):
        events = scrape_from_browser_data(browser_data_file, vname, vslug)
        if events:
            return events

    # Strategy 2: installed with session
    if not prefer_playwright:
        events = scrape_with_instaloader(username, vname, vslug, session_file)
        if events:
            return events

    # Strategy 3: playwright
    events = scrape_with_playwright(username, vname, vslug)
    if events:
        return events

    # Strategy 2 retry (if playwright was preferred)
    if prefer_playwright:
        events = scrape_with_instaloader(username, vname, vslug, session_file)
        if events:
            return events

    # Strategy 4: static cache fallback (data/instagram-{vslug}.json)
    cache_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "data", f"instagram-{vslug}.json"
    )
    if os.path.exists(cache_path):
        try:
            with open(cache_path) as f:
                cached = json.load(f)
            if cached:
                print(f"  [ig/{username}] {vname}: {len(cached)} events from cache", file=sys.stderr)
                return cached
        except Exception as e:
            print(f"  [ig/{username}] cache error: {e}", file=sys.stderr)

    return []


# ═══════════════════════════════════════════════════
#  CLI entry point
# ═══════════════════════════════════════════════════

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Scrape Instagram for venue music events")
    parser.add_argument("username", help="Instagram handle (e.g. thefoxandcastle)")
    parser.add_argument("--name", default="", help="Venue display name")
    parser.add_argument("--slug", default="", help="Venue slug")
    parser.add_argument("--session", help="Path to Instaloader session file")
    parser.add_argument("--browser-data", help="Path to JSON file from browser_console")
    parser.add_argument("--playwright", action="store_true", help="Use Playwright")
    parser.add_argument("--max", type=int, default=50, help="Max posts to check")
    args = parser.parse_args()

    vname = args.name or args.username
    vslug = args.slug or args.username

    events = scrape_instagram(
        args.username,
        vname,
        vslug,
        session_file=args.session,
        browser_data_file=args.browser_data,
        prefer_playwright=args.playwright,
    )

    print(json.dumps(events, indent=2))
    print(f"\n--- {len(events)} music events found ---", file=sys.stderr)


if __name__ == "__main__":
    main()