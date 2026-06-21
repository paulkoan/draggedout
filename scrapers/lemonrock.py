#!/usr/bin/env python3
"""Lemonrock scraper — fetch CSV exports per venue and parse into events."""

import csv
import json
import sys
from datetime import datetime, date
from pathlib import Path
from urllib.request import urlopen

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
VENUES_YAML = Path(__file__).resolve().parent.parent / "venues.yaml"

# Venue slugs → Lemonrock CSV URL key
# y=5 seems to give 2+ years of data for most venues
LEMONROCK_TEMPLATE = "https://www.lemonrock.com/csv.php?t={slug}&y=5"

def fetch_venue_csv(slug):
    """Fetch CSV from Lemonrock for a venue slug. Returns list of dicts."""
    url = LEMONROCK_TEMPLATE.format(slug=slug)
    try:
        resp = urlopen(url, timeout=15)
        text = resp.read().decode("utf-8-sig")
        rows = list(csv.DictReader(text.splitlines()))
        return rows
    except Exception as e:
        print(f"  WARN: failed to fetch {slug}: {e}", file=sys.stderr)
        return []

def clean_event(row, venue_name, venue_slug):
    """Normalise a Lemonrock CSV row into our event format."""
    raw_date = row.get("Date", "").strip()
    if not raw_date:
        return None
    try:
        dt = datetime.strptime(raw_date, "%Y-%m-%d")
    except ValueError:
        return None

    start = row.get("Start Time", "").strip()[:5]  # HH:MM
    end = row.get("End Time", "").strip()[:5]
    time_str = f"{start}–{end}" if end else start

    fee = row.get("Entrance Fee", "").strip()
    if not fee:
        fee = "?"

    return {
        "date": raw_date,
        "day_of_week": dt.strftime("%A"),
        "start": start,
        "end": end,
        "artist": row.get("Band Name", "").strip(),
        "venue": venue_name,
        "venue_slug": venue_slug,
        "cost": fee,
        "source": "lemonrock",
        "source_url": row.get("URL", "").strip(),
        "repeating": row.get("Repeating?", "").strip() == "1",
    }

def fetch_all():
    """Fetch all Lemonrock venues and return events."""
    # Load venues list
    import yaml
    config = yaml.safe_load(open(VENUES_YAML))
    
    events = []
    for venue in config["venues"]:
        slug = venue.get("sources", {}).get("lemonrock")
        if not slug:
            continue
        rows = fetch_venue_csv(slug)
        for r in rows:
            ev = clean_event(r, venue["name"], venue["slug"])
            if ev:
                events.append(ev)
        print(f"  {venue['name']}: {len(rows)} gigs", file=sys.stderr)
    
    return events

if __name__ == "__main__":
    events = fetch_all()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    json.dump(events, open(DATA_DIR / "events.json", "w"), indent=2, default=str)
    print(f"Wrote {len(events)} events to data/events.json")