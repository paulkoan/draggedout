#!/usr/bin/env python3
"""Instagram browser-data → Dragged Out event format.

Reads raw JSON extracted from Instagram (posts with href + caption fields),
parses dates/times/artists from captions, and outputs events in the
standard Dragged Out event schema as CSV, YAML, or JSON.

Usage:
    python3 instagram_to_events.py fox_castle_instagram.json            # → stdout CSV
    python3 instagram_to_events.py fox_castle_instagram.json -o events  # → events.csv + events.yaml
    python3 instagram_to_events.py fox_castle_instagram.json --json     # → stdout JSON
    python3 instagram_to_events.py fox_castle_instagram.json --validate # → sample calendar HTML
    python3 instagram_to_events.py *.json --name "The Fox & Castle" --slug fox-castle
"""

import csv
import io
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, date, timedelta
from pathlib import Path

# ── Standard Dragged Out event fields ──
EVENT_FIELDS = [
    "date", "day_name", "start", "end",
    "artist", "venue", "venue_slug",
    "cost", "source", "url",
    "cancelled", "repeating",
]

MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]
MONTH_ABBR = {m[:3].lower(): i + 1 for i, m in enumerate(MONTH_NAMES)}
MONTH_FULL = {m.lower(): i + 1 for i, m in enumerate(MONTH_NAMES)}
MONTH_MAP = {**MONTH_FULL, **MONTH_ABBR}

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# ── Broad event keywords (covers music, quiz, comedy, sports, charity) ──
EVENT_KEYWORDS = [
    "live music", "open mic", "open-mic", "acoustic", "gig", "band",
    "performer", "performing", "singer", "songwriter", "on stage", "dj",
    "karaoke", "jam session", "music night", "live band",
    "rock", "blues", "jazz", "folk", "punk", "metal",
    "soul", "r&b", "hip hop", "reggae", "indie",
    "quiz night", "quiz", "bbq", "charity",
    "kick-off", "match", "performance",
    "stage is yours", "the stage", "live",
    "open mic night",
]

# ── Date patterns ──
DATE_PATTERNS = [
    # "📅 Saturday 11 July" (emoji prefix)
    re.compile(
        r"📅\s*(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\w*\s+"
        r"(\d{1,2})(?:st|nd|rd|th)?\s+"
        r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
        r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
        r"(?:\s*,?\s*(\d{4}))?",
        re.IGNORECASE,
    ),
    # "Saturday 11 July" or "Thursday 18th September"
    re.compile(
        r"(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\w*\s+"
        r"(\d{1,2})(?:st|nd|rd|th)?\s+"
        r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
        r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
        r"(?:\s*,?\s*(\d{4}))?",
        re.IGNORECASE,
    ),
    # "on Saturday 11 July"
    re.compile(
        r"on\s+(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\w*\s+"
        r"(\d{1,2})(?:st|nd|rd|th)?\s+"
        r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
        r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
        r"(?:\s*,?\s*(\d{4}))?",
        re.IGNORECASE,
    ),
    # "11th July" (bare date)
    re.compile(
        r"\b(\d{1,2})(?:st|nd|rd|th)?\s+"
        r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
        r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
        r"(?:\s*,?\s*(\d{4}))?",
        re.IGNORECASE,
    ),
    # "17th April 2025" (DD Month YYYY)
    re.compile(
        r"\b(\d{1,2})(?:st|nd|rd|th)?\s+"
        r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
        r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
        r"\s+(\d{4})\b",
        re.IGNORECASE,
    ),
    # DD/MM/YYYY
    re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b"),
    # YYYY-MM-DD
    re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"),
]

# ── Time patterns ──
TIME_PATTERNS = [
    # "Kick-off: 22:00"
    re.compile(r"(?:Kick-off|Kickoff|Starts?)\s*:?\s*(\d{1,2}:\d{2})\s*(pm|am)?", re.IGNORECASE),
    # "🕙 Kick-off: 22:00" or "🕗 Performances from 8pm"
    re.compile(r"[🕙🕗⏰⌚]\s*(?:\w+\s+)*?(\d{1,2})(?::(\d{2}))?\s*(pm|am)?", re.IGNORECASE),
    # "Performances from 8pm – 10pm"
    re.compile(r"Performances?\s+from\s+(\d{1,2})(?::(\d{2}))?\s*(pm|am)?", re.IGNORECASE),
    # "from 8pm" / "at 8pm" / "starts 8pm"
    re.compile(r"(?:from|at|starts?)\s+(\d{1,2})(?::(\d{2}))?\s*(pm|am)?", re.IGNORECASE),
    # "8pm" or "8:30pm" (bare)
    re.compile(r"\b(\d{1,2}):(\d{2})\s*(pm|am)?\b", re.IGNORECASE),
    re.compile(r"\b(\d{1,2})\s*(pm|am)\b", re.IGNORECASE),
]


# ═══════════════════════════════════════════════════
#  PARSING
# ═══════════════════════════════════════════════════


def is_event_post(caption: str) -> bool:
    """Check if a caption mentions an event worth listing."""
    if not caption:
        return False
    lower = caption.lower()
    return any(kw in lower for kw in EVENT_KEYWORDS)


def extract_date(caption: str) -> str | None:
    """Extract a date (YYYY-MM-DD) from an Instagram caption.

    Handles formats like:
      - 'Saturday 11 July'
      - '📅 Tuesday 24th June'
      - 'Thursday 18th September 2026'
      - '11th July'
      - 'on Sunday 17th March'
      - 'this Sunday' (relative — picks next occurrence)
    """
    if not caption:
        return None

    now = datetime.now()
    current_year = now.year

    # ── Structured date patterns (checked FIRST — they're more specific) ──
    for pattern in DATE_PATTERNS:
        m = pattern.search(caption)
        if not m:
            continue

        groups = m.groups()

        # Pattern with month name in groups[2] (most patterns)
        if len(groups) >= 3:
            # groups[0] = day num or weekday, groups[1] = day num, groups[2] = month
            # Determine which field is day/num
            if groups[1] and groups[1].isdigit():
                day_num = int(groups[1])
                month_str = groups[2]
                year_str = groups[3] if len(groups) > 3 and groups[3] else None
            elif groups[0] and groups[0].isdigit():
                day_num = int(groups[0])
                month_str = groups[1]
                year_str = groups[2] if len(groups) > 2 and groups[2] else None
            else:
                continue

            month_lower = month_str.strip().lower()[:3]
            month_num = MONTH_MAP.get(month_lower)
            if not month_num:
                continue

            year = int(year_str) if year_str else current_year
            if not year_str and month_num < now.month:
                year += 1
            elif not year_str and month_num == now.month and now.day > day_num:
                year += 1

            return f"{year}-{month_num:02d}-{day_num:02d}"

        # DD/MM/YYYY or YYYY-MM-DD (groups: d, m, y or y, m, d)
        if len(groups) == 3:
            a, b, c = groups
            year_str = c if len(c) == 4 else a if len(a) == 4 else None
            if year_str is not None:
                y, m, d = int(year_str), int(b), int(a)
                return f"{y:04d}-{m:02d}-{d:02d}"

    # ── Fallback: relative dates (only if no structured date matched) ──
    # "this Sunday", "this Thursday", etc.
    relative_match = re.search(
        r"\bthis\s+(Sunday|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday)\b",
        caption, re.IGNORECASE,
    )
    if relative_match:
        target_day = relative_match.group(1).capitalize()
        today_weekday = now.weekday()
        target_weekday = DAY_NAMES.index(target_day)
        days_ahead = (target_weekday - today_weekday) % 7
        if days_ahead == 0:
            days_ahead = 7
        next_date = now + timedelta(days=days_ahead)
        return next_date.strftime("%Y-%m-%d")

    # "tomorrow" — only if no other date found
    if re.search(r"\btomorrow\b", caption, re.IGNORECASE):
        tomorrow = now + timedelta(days=1)
        return tomorrow.strftime("%Y-%m-%d")

    return None


def extract_time(caption: str) -> str:
    """Extract time like '8pm', '20:00', '8:30pm' from caption.

    Returns 24-hour format string 'HH:MM' or empty string.
    """
    if not caption:
        return ""

    for pattern in TIME_PATTERNS:
        m = pattern.search(caption)
        if not m:
            continue

        groups = m.groups()

        # Determine which group is hour, minutes, and am/pm
        # Most patterns: groups[0]=hour, groups[1]=minutes_or_none, groups[2]=ampm_or_none
        # Bare patterns may have groups[1]=ampm when minutes absent

        hour_str = groups[0]
        if not hour_str:
            continue

        # Handle HH:MM format (e.g. "22:00" captured as a single group)
        if ":" in hour_str:
            parts = hour_str.split(":")
            h = int(parts[0])
            mi = parts[1]
            return f"{h:02d}:{mi}"

        h = int(hour_str)

        # Check what's in groups[1] — minutes or am/pm
        mid = groups[1]  # Could be minutes string, am/pm string, or None
        ampms = groups[2] if len(groups) > 2 else None  # Could be am/pm or None

        mi = "00"
        a = ""

        if mid and mid.lower() in ("am", "pm"):
            # Bare pattern: "8pm" → groups = ('8', 'pm')
            a = mid.lower()
        elif mid and mid.isdigit():
            mi = mid
            a = (ampms or "").lower() if ampms else ""
        elif ampms:
            a = ampms.lower()

        if a == "pm" and h < 12:
            h += 12
        elif a == "am" and h == 12:
            h = 0
        return f"{h:02d}:{mi}"

    return ""


def extract_artist(caption: str) -> str:
    """Extract performer/headliner name from caption."""
    if not caption:
        return ""

    # Look for named performer patterns
    patterns = [
            r"featuring\s+(.+?)(?:[,\.!]|\n|$)",
            r"starring\s+(.+?)(?:[,\.!]|\n|$)",
            r"performed by\s+(.+?)(?:[,\.!]|\n|$)",
            r"music to be performed by\s+(.+?)(?:[,\.!]|\n|$)",
            r"hosted by\s+(.+?)(?:[,\.!]|\n|$)",
        ]
    for pat in patterns:
        match = re.search(pat, caption, re.IGNORECASE)
        if match:
            artist = match.group(1).strip().rstrip(".")
            # Clean emoji from artist name
            artist = re.sub(r'[\U0001F300-\U0001FFFD\u2600-\u27FF\uFE00-\uFE0F]+', '', artist).strip()
            if artist and len(artist) > 2:
                return artist[:100]

    # Fallback: first non-empty line, stripped of leading emoji
    lines = caption.split("\n")
    first_line = ""
    for line in lines:
        stripped = line.strip()
        if stripped:
            first_line = stripped
            break

    # Strip leading emoji
    artist = re.sub(r'^[\U0001F300-\U0001FFFD\u2600-\u27FF\uFE00-\uFE0F🏴🧠🎤✨🍻🍀🐣🦢⚽🎶🎅]+[\s]{0,2}', '', first_line).strip()
    artist = re.sub(r'^[🏴🧠🎤✨🍻🍀🐣🦢⚽🎶🎅🎁🍸]+[\s]{0,2}', '', artist).strip()
    if not artist:
        artist = first_line[:100]
    else:
        artist = artist[:100]

    return artist.strip().rstrip(",")


def is_recurring(caption: str) -> bool:
    """Check if caption suggests a recurring event."""
    lower = caption.lower()
    return any(kw in lower for kw in ["quiz", "open mic", "open-mic", "weekly", "monthly", "fortnightly"])


def is_music_focus(caption: str) -> bool:
    """Check if the event is primarily music-focused (vs quiz, sports, etc.)."""
    lower = caption.lower()
    music_kw = [
        "live music", "open mic", "open-mic", "acoustic", "gig", "band",
        "performer", "performing", "singer", "songwriter", "on stage", "dj",
        "karaoke", "jam session", "music night", "live band",
        "rock", "blues", "jazz", "folk", "punk", "metal",
        "soul", "r&b", "hip hop", "reggae", "indie",
    ]
    return any(kw in lower for kw in music_kw)


# ═══════════════════════════════════════════════════
#  TRANSFORM
# ═══════════════════════════════════════════════════


def transform_post(post: dict, vname: str = "", vslug: str = "") -> dict | None:
    """Convert an Instagram post dict to a Dragged Out event dict.

    Expected post fields: href (post URL), caption (alt text/caption), img_url.
    """
    caption = post.get("caption", "") or ""
    href = post.get("href", "") or ""
    img_url = post.get("img_url", "") or ""

    # Skip non-event posts
    if not is_event_post(caption):
        return None

    date_str = extract_date(caption)
    if not date_str:
        return None

    start = extract_time(caption)
    artist = extract_artist(caption)

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
        "source": f"instagram:{vslug}" if vslug else "instagram",
        "url": href,
        "cancelled": False,
        "repeating": is_recurring(caption),
        "_music_focus": is_music_focus(caption),
        "_img_url": img_url,
    }


# ═══════════════════════════════════════════════════
#  OUTPUT FORMATTERS
# ═══════════════════════════════════════════════════


def to_csv(events: list[dict]) -> str:
    """Output events as CSV string."""
    output = io.StringIO()
    # Non-prefixed fields only for CSV output
    fieldnames = [f for f in EVENT_FIELDS]
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for ev in events:
        row = {k: ev.get(k, "") for k in fieldnames}
        row["cancelled"] = "true" if ev.get("cancelled") else "false"
        row["repeating"] = "true" if ev.get("repeating") else "false"
        writer.writerow(row)
    return output.getvalue()


def to_yaml(events: list[dict]) -> str:
    """Output events as YAML string (manual, no dependency)."""
    lines = ["# Instagram events — Dragged Out format", f"# Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}", f"# Events: {len(events)}", ""]
    for ev in events:
        lines.append(f"- date: {ev.get('date', '')}")
        lines.append(f"  day_name: {ev.get('day_name', '')}")
        lines.append(f"  start: \"{ev.get('start', '')}\"")
        lines.append(f"  end: \"{ev.get('end', '')}\"")
        # Escape YAML-safe artist
        artist = ev.get("artist", "")
        if any(c in artist for c in [":", "#", "{", "}", "[", "]", ">", "|", "!", "&", "*", "?", "-"]):
            lines.append(f"  artist: \"{artist}\"")
        else:
            lines.append(f"  artist: {artist}")
        lines.append(f"  venue: \"{ev.get('venue', '')}\"")
        lines.append(f"  venue_slug: {ev.get('venue_slug', '')}")
        lines.append(f"  cost: \"{ev.get('cost', '')}\"")
        lines.append(f"  source: {ev.get('source', '')}")
        lines.append(f"  url: \"{ev.get('url', '')}\"")
        lines.append(f"  cancelled: {'true' if ev.get('cancelled') else 'false'}")
        lines.append(f"  repeating: {'true' if ev.get('repeating') else 'false'}")
        lines.append("")
    return "\n".join(lines)


def to_json(events: list[dict]) -> str:
    """Output events as pretty JSON."""
    clean = []
    for ev in events:
        clean_ev = {k: ev.get(k, "") for k in EVENT_FIELDS}
        # Add _prefixed extras
        for k in ("_music_focus", "_img_url"):
            if k in ev and ev[k]:
                clean_ev[k] = ev[k]
        clean.append(clean_ev)
    return json.dumps(clean, indent=2, ensure_ascii=False)


# ═══════════════════════════════════════════════════
#  VALIDATION: sample calendar page
# ═══════════════════════════════════════════════════


SAMPLE_CSS = """
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#0d1117;--fg:#e6edf3;--card:#161b22;--card-hover:#1c2333;--border:#30363d;--accent:#58a6ff;--green:#3fb950;--amber:#d29922;--red:#f85149;--muted:#8b949e;--font:'Segoe UI',Helvetica,Arial,sans-serif}
html{font-family:var(--font);font-size:16px;color:var(--fg);background:var(--bg)}
body{min-height:100vh;display:flex;flex-direction:column}
.wrapper{max-width:900px;margin:0 auto;padding:1.5rem;width:100%;flex:1}
a{color:var(--accent);text-decoration:none}
nav{display:flex;gap:1.5rem;margin-bottom:1.5rem;font-size:.95rem}
nav a{color:var(--muted)}
nav a:hover{color:var(--fg)}
h1{font-size:1.8rem;margin-bottom:.25rem}
.subtitle{color:var(--muted);margin-bottom:1.5rem;font-size:.9rem}
.day-section{margin-bottom:2rem}
.day-heading{font-size:1rem;font-weight:600;color:var(--accent);padding-bottom:.35rem;margin-bottom:.75rem;border-bottom:1px solid var(--border)}
.event-card{display:flex;align-items:center;gap:.65rem;padding:.6rem .85rem;background:var(--card);border:1px solid var(--border);border-radius:8px;margin-bottom:.4rem;transition:background .1s}
.event-card:hover{background:var(--card-hover);border-color:var(--accent)}
.event-time{font-size:.82rem;font-weight:600;min-width:3.5rem;text-align:center;padding:.2rem .4rem;background:rgba(255,255,255,.04);border-radius:4px;white-space:nowrap}
.event-body{flex:1;display:flex;flex-direction:column}
.event-artist{font-weight:500;font-size:.95rem}
.event-source{font-size:.75rem;color:var(--muted);margin-top:.1rem}
.event-tags{display:flex;gap:.35rem;align-items:center;flex-wrap:wrap}
.event-venue-tag{font-size:.78rem;color:var(--muted);padding:.12rem .4rem;background:rgba(88,166,255,.08);border-radius:4px;white-space:nowrap}
.event-label{font-size:.75rem;padding:.1rem .35rem;border-radius:4px;white-space:nowrap}
.label-music{background:rgba(63,185,80,.12);color:var(--green)}
.label-other{background:rgba(210,153,34,.12);color:var(--amber)}
.footer{margin-top:2rem;padding-top:1rem;border-top:1px solid var(--border);font-size:.8rem;color:var(--muted)}
.stats{display:flex;gap:2rem;margin-bottom:1rem;flex-wrap:wrap}
.stat-box{padding:.75rem 1rem;background:var(--card);border:1px solid var(--border);border-radius:8px;text-align:center;min-width:120px}
.stat-num{font-size:1.5rem;font-weight:700}
.stat-label{font-size:.75rem;color:var(--muted)}
"""


def build_validation_page(events: list[dict], source_file: str) -> str:
    """Build a sample calendar HTML page showing the extracted events."""
    # Sort by date
    events.sort(key=lambda e: e.get("date", ""))

    # Stats
    total = len(events)
    music_count = sum(1 for e in events if e.get("_music_focus"))
    venues = set(e.get("venue_slug", "") for e in events if e.get("venue"))
    dated_events = [(e["date"], e) for e in events if e.get("date")]

    # Group by date
    grouped = defaultdict(list)
    for ev in events:
        d = ev.get("date", "")
        if d:
            grouped[d].append(ev)

    # Build day sections
    sections = []
    for ds in sorted(grouped.keys()):
        d_events = grouped[ds]
        try:
            dt = datetime.strptime(ds, "%Y-%m-%d")
            label = dt.strftime("%a %d %b %Y")
        except ValueError:
            label = ds

        cards = []
        for ev in d_events:
            time_html = f"<span class='event-time'>{ev.get('start', '')[:5]}</span>" if ev.get("start") else ""
            label_class = "label-music" if ev.get("_music_focus") else "label-other"
            label_text = "🎵 Music" if ev.get("_music_focus") else "Other"
            venue_tag = ev.get("venue", "") or ev.get("venue_slug", "")
            artist = ev.get("artist", "Untitled")
            cards.append(f"""<div class="event-card">
{time_html}
<div class="event-body">
<div class="event-artist">{artist}</div>
<div class="event-source">{ev.get("source", "")} · <a href="{ev.get("url", "#")}">View post</a></div>
</div>
<div class="event-tags">
<span class="event-venue-tag">{venue_tag}</span>
<span class="event-label {label_class}">{label_text}</span>
</div>
</div>""")

        sections.append(f"""<section class="day-section">
<h2 class="day-heading">{label}</h2>
{chr(10).join(cards)}
</section>""")

    body = f"""<!DOCTYPE html>
<html lang="en-GB">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Instagram Events — Validation</title>
<style>{SAMPLE_CSS}</style>
</head>
<body>
<div class="wrapper">
<h1>Instagram → Events</h1>
<p class="subtitle">Source: <code>{source_file}</code> &middot; Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>

<div class="stats">
<div class="stat-box"><div class="stat-num">{total}</div><div class="stat-label">Events Found</div></div>
<div class="stat-box"><div class="stat-num">{music_count}</div><div class="stat-label">Music Events</div></div>
<div class="stat-box"><div class="stat-num">{total - music_count}</div><div class="stat-label">Other Events</div></div>
<div class="stat-box"><div class="stat-num">{len(venues)}</div><div class="stat-label">Venues</div></div>
</div>

<p style="color:var(--muted);margin-bottom:1rem">
Event filtering uses keyword matching on captions. Music events include keywords like
"live music", "open mic", "band", "gig", etc. Other events cover quizzes, BBQs, sports, and charity.
</p>

{chr(10).join(sections) if sections else "<p style='color:var(--muted)'>No events found in the data.</p>"}

<div class="footer">
<p>Dragged Out — Instagram event transformation · Validation output</p>
</div>
</div>
</body>
</html>"""
    return body


# ═══════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Transform Instagram browser data to Dragged Out events",
    )
    parser.add_argument("input", nargs="+", help="JSON file(s) from Instagram browser_console extraction")
    parser.add_argument("--name", "-n", default="", help="Venue display name (overrides per-venue detection)")
    parser.add_argument("--slug", "-s", default="", help="Venue slug (overrides per-venue detection)")
    parser.add_argument("--output", "-o", help="Output filename prefix (creates .csv + .yaml)")
    parser.add_argument("--json", "-j", action="store_true", help="Output JSON instead of CSV")
    parser.add_argument("--yaml-only", "-y", action="store_true", help="Output YAML only")
    parser.add_argument("--csv-only", "-c", action="store_true", help="Output CSV only")
    parser.add_argument("--validate", "-v", action="store_true", help="Generate sample calendar page (validation.html)")
    parser.add_argument("--music-only", "-m", action="store_true", help="Only include music-focused events")

    args = parser.parse_args()

    all_events = []

    for input_path in args.input:
        path = Path(input_path)
        if not path.exists():
            print(f"  [WARN] File not found: {input_path}", file=sys.stderr)
            continue

        with open(path) as f:
            posts = json.load(f)

        # Try to infer venue from filename
        # e.g., fox_castle_instagram.json → slug: fox-castle, name: Fox Castle
        stem = path.stem
        vname = args.name
        vslug = args.slug

        if not vslug:
            # Extract venue slug from filename: "fox_castle_instagram" → "fox-castle"
            base = stem.replace("_instagram", "").replace("-instagram", "")
            vslug = base.replace("_", "-")
            # Clean any trailing/leading dashes
            vslug = vslug.strip("-")

        if not vname:
            # Convert slug to title case: "fox-castle" → "Fox Castle"
            vname = vslug.replace("-", " ").title()

        print(f"  [in] {path.name}: {len(posts)} posts → venue='{vname}', slug='{vslug}'", file=sys.stderr)

        for post in posts:
            ev = transform_post(post, vname, vslug)
            if ev:
                all_events.append(ev)

        print(f"  [in] {path.name}: {len([e for e in all_events if e.get('venue_slug') == vslug])} events extracted", file=sys.stderr)

    if not all_events:
        print("  No events extracted from input files.", file=sys.stderr)
        sys.exit(1)

    # Filter music-only if requested
    if args.music_only:
        all_events = [e for e in all_events if e.get("_music_focus")]

    # Print summary
    music_ev = sum(1 for e in all_events if e.get("_music_focus"))
    print(f"  Total: {len(all_events)} events ({music_ev} music, {len(all_events) - music_ev} other)", file=sys.stderr)

    # Output
    if args.output:
        # CSV
        if not args.yaml_only:
            csv_path = Path(args.output + ".csv")
            csv_path.write_text(to_csv(all_events))
            print(f"  [out] {csv_path}", file=sys.stderr)
        # YAML
        if not args.csv_only:
            yaml_path = Path(args.output + ".yaml")
            yaml_path.write_text(to_yaml(all_events))
            print(f"  [out] {yaml_path}", file=sys.stderr)
        # JSON
        if args.json:
            json_path = Path(args.output + ".json")
            json_path.write_text(to_json(all_events))
            print(f"  [out] {json_path}", file=sys.stderr)
    else:
        if args.json:
            print(to_json(all_events))
        elif args.yaml_only or args.csv_only:
            if args.yaml_only:
                print(to_yaml(all_events))
            else:
                print(to_csv(all_events))
        else:
            # Default: print CSV
            print(to_csv(all_events))
            print(f"--- {len(all_events)} events ({music_ev} music) ---", file=sys.stderr)

    # Validation page
    if args.validate:
        html = build_validation_page(all_events, ", ".join(args.input))
        validate_path = Path("validation.html")
        validate_path.write_text(html)
        print(f"  [out] {validate_path} (sample calendar — open in browser)", file=sys.stderr)


if __name__ == "__main__":
    main()