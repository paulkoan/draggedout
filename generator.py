#!/usr/bin/env python3
"""Dragged Out site generator — scrapes data sources, enriches with genre/YouTube, builds HTML.

Usage:
    python3 generator.py              # scrape + enrich + build
    python3 generator.py --no-scrape  # build from cache
    python3 generator.py --serve      # local preview on :8080
"""

import csv, json, os, sys, re, urllib.request, urllib.parse, http.server, socketserver
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from collections import defaultdict
from string import Template
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
SITE = ROOT / "build"

MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
DSHORT = {"Monday":"Mon","Tuesday":"Tue","Wednesday":"Wed",
          "Thursday":"Thu","Friday":"Fri","Saturday":"Sat","Sunday":"Sun"}

# ═══════════════════════════════════════════════════
#  BAND INFO — genre + YouTube from Lemonrock gig pages + cache
# ═══════════════════════════════════════════════════

BANDS_FILE = DATA / "bands.json"

def load_bands_cache():
    if BANDS_FILE.exists():
        return json.load(BANDS_FILE.open())
    return {}

def save_bands_cache(cache):
    DATA.mkdir(exist_ok=True)
    json.dump(cache, BANDS_FILE.open("w"), indent=2)

def fetch_band_info(gig_url):
    """Fetch a Lemonrock gig page and extract genre + image from JSON-LD.

    Each gig page has Schema.org JSON-LD with:
      - performer[].genre (e.g. "Indie Rock")
      - image (band/event photo URL)
    """
    try:
        resp = urllib.request.urlopen(gig_url, timeout=8)
        html = resp.read().decode("utf-8", errors="replace")
        m = re.search(r'<script type="application/ld\+json">\s*(.*?)\s*</script>', html, re.DOTALL)
        if not m:
            return None, None
        data = json.loads(m.group(1))
        # Genre
        genre = None
        performers = data.get("performer", [])
        if isinstance(performers, dict):
            performers = [performers]
        for p in performers:
            g = p.get("genre")
            if g:
                genre = str(g).strip()
                break
        # Image
        image = data.get("image", "")
        return genre, image
    except Exception:
        return None, None

def enrich_events(events, bands_cache):
    """Add genre, youtube, image to events, using cache when possible."""
    n_fetched = 0
    for e in events:
        artist = e["artist"]
        if artist in bands_cache:
            info = bands_cache[artist]
            e["genre"] = info.get("genre")
            e["youtube"] = info.get("youtube")
            e["image"] = info.get("image")
        elif e.get("url"):
            print(f"  [band] fetching info for '{artist}'...", file=sys.stderr)
            genre, image = fetch_band_info(e["url"])
            yt_search = f"https://www.youtube.com/results?search_query={urllib.parse.quote(artist + ' band')}"
            bands_cache[artist] = {"genre": genre, "youtube": yt_search, "image": image}
            e["genre"] = genre
            e["youtube"] = yt_search
            e["image"] = image
            n_fetched += 1
            import time
            time.sleep(0.5)
        else:
            e["genre"] = None
            e["youtube"] = None
            e["image"] = None
    if n_fetched:
        print(f"  [band] fetched genres for {n_fetched} new artists", file=sys.stderr)
        save_bands_cache(bands_cache)
    return events

# ═══════════════════════════════════════════════════
#  LEMONROCK SCRAPER
# ═══════════════════════════════════════════════════

def load_venues():
    import yaml
    return yaml.safe_load((ROOT / "venues.yaml").read_text())["venues"]

def fetch_lr(slug):
    url = f"https://www.lemonrock.com/csv.php?t={slug}&y=5"
    try:
        resp = urllib.request.urlopen(url, timeout=10)
        text = resp.read().decode("utf-8-sig")
        return list(csv.DictReader(text.splitlines()))
    except Exception as e:
        print(f"  [WARN] lemonrock/{slug}: {e}", file=sys.stderr)
        return []

def parse_lr(row, vname, vslug):
    raw = row.get("Date","").strip()
    if not raw: return None
    try:
        dt = datetime.strptime(raw, "%Y-%m-%d")
    except:
        return None
    start = row.get("Start Time","").strip()[:5]
    end = row.get("End Time","").strip()[:5]
    fee = row.get("Entrance Fee","").strip() or "?"
    return {
        "date": raw, "day_name": dt.strftime("%A"),
        "start": start, "end": end,
        "artist": row.get("Band Name","").strip(),
        "venue": vname, "venue_slug": vslug,
        "cost": fee, "source": "lemonrock",
        "url": row.get("URL","").strip(),
        "cancelled": bool(row.get("Cancelled?","").strip()),
        "repeating": row.get("Repeating?","").strip() == "1",
    }

def scrape_all():
    venues = load_venues()
    events = []
    for v in venues:
        slug = v.get("sources",{}).get("lemonrock")
        if slug:
            rows = fetch_lr(slug)
            for r in rows:
                ev = parse_lr(r, v["name"], v["slug"])
                if ev: events.append(ev)
            print(f"  [lr] {v['name']}: {len(rows)}", file=sys.stderr)

        web_url = v.get("sources",{}).get("web")
        if web_url:
            # Dispatch to the right scraper based on the venue slug
            vslug = v["slug"]
            if vslug == "duke-connaught":
                evs = scrape_duke_connaught(web_url)
            elif vslug == "swan-clewer":
                evs = scrape_swan(web_url, v["name"], vslug)
            elif vslug in ("george-eton", "unit-4"):
                evs = scrape_webrew(web_url, v["name"], vslug)
            elif vslug == "other-space-arts":
                from scrapers.other_space_arts import scrape as scrape_other_space_arts
                evs = scrape_other_space_arts(web_url)
            elif vslug == "old-court":
                from scrapers.old_court import scrape_old_court_music
                evs = scrape_old_court_music(web_url)
            elif vslug == "two-flints":
                from scrapers.two_flints import scrape as scrape_two_flints
                evs = scrape_two_flints(web_url)
            elif vslug == "horse-groom":
                from scrapers.horse_groom import scrape as scrape_horse_groom
                evs = scrape_horse_groom(web_url)
            else:
                print(f"  [web] {v['name']}: no scraper for slug '{vslug}', skipping", file=sys.stderr)
                evs = []
            events.extend(evs)

        # Instagram source — scrape music-related posts from venue profile
        ig_handle = v.get("sources",{}).get("instagram")
        if ig_handle and v.get("status") != "permanently_closed":
            from scrapers.instagram import scrape_instagram
            evs = scrape_instagram(ig_handle, v["name"], v["slug"])
            events.extend(evs)

    events.sort(key=lambda e: e["date"])
    return events, venues

# ═══════════════════════════════════════════════════
#  WEB SCRAPER — Duke of Connaught (Squarespace)
# ═══════════════════════════════════════════════════

MONTH_NAMES = ["January","February","March","April","May","June",
               "July","August","September","October","November","December"]

def scrape_duke_connaught(url):
    """Scrape gigs from the Duke of Connaught's Squarespace live-music page.

    The page uses per-gig <div class=\"sqs-html-content\"> blocks with:
      <p class=\"sqsrte-large\"><strong>Sunday 14th June</strong></p>
      <p><strong>Aaron Norton</strong></p>
      <p><strong><em>6pm</em></strong></p>
    Month headings (<h2>June</h2>) appear in separate blocks.
    """
    try:
        resp = urllib.request.urlopen(url, timeout=10)
        html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  [WARN] duke-connaught: {e}", file=sys.stderr)
        return []

    soup = BeautifulSoup(html, "html.parser")
    events = []
    current_month = None
    now = datetime.now()
    current_year = now.year

    for div in soup.find_all("div", class_="sqs-html-content"):
        # Month heading?
        h2 = div.find("h2")
        if h2:
            text = h2.get_text(strip=True)
            m = re.match(r'(January|February|March|April|May|June|July|August|September|October|November|December)', text, re.I)
            if m:
                current_month = m.group(1).capitalize()
            continue

        # Gig block?
        p_date = div.find("p", class_="sqsrte-large")
        if not p_date:
            continue
        date_text = p_date.get_text(strip=True)

        # Match "Sunday 14th June", "Sunday 2nd August", etc.
        dm = re.match(r'(Sunday|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday)\s+(\d+)(st|nd|rd|th)\s+(\w+)', date_text)
        if not dm:
            continue

        day_num = int(dm.group(2))
        month_name = dm.group(4).capitalize()

        if month_name in MONTH_NAMES:
            month_num = MONTH_NAMES.index(month_name) + 1
        elif current_month and current_month in MONTH_NAMES:
            month_num = MONTH_NAMES.index(current_month) + 1
        else:
            continue

        # Extract artist + time from remaining <p> tags
        ps = div.find_all("p")
        artist = None
        time_raw = None
        for p in ps:
            t = p.get_text(strip=True)
            if p is p_date or not t:
                continue
            if artist is None:
                artist = t
            elif time_raw is None:
                time_raw = t

        if not artist:
            continue

        # Clean artist name (collapse whitespace, strip junk)
        artist = re.sub(r'\s+', ' ', artist).strip().rstrip(",")

        # If the text-month conflicts with the heading-month (site typo),
        # trust the heading when it's the more recent month. This catches
        # "Sunday 26th April" appearing under a July heading.
        if (current_month and current_month in MONTH_NAMES
                and month_name in MONTH_NAMES
                and month_name != current_month):
            txt_idx = MONTH_NAMES.index(month_name)
            hdr_idx = MONTH_NAMES.index(current_month)
            # Trust heading if text month is 2+ months before the heading
            if hdr_idx - txt_idx >= 2:
                month_name = current_month
                month_num = hdr_idx + 1

        # Parse time like "6pm", "6:30pm"
        start = ""
        tm = re.match(r'(\d{1,2})(?::(\d{2}))?\s*(pm|am)', time_raw or "", re.I)
        if tm:
            h, m, a = int(tm.group(1)), tm.group(2) or "00", tm.group(3).lower()
            if a == "pm" and h < 12:
                h += 12
            elif a == "am" and h == 12:
                h = 0
            start = f"{h:02d}:{m}"

        # Determine year: if the gig month is >= current month, use current year
        # otherwise use next year (assuming they've posted ahead)
        year = current_year if month_num >= now.month else current_year + 1

        date_str = f"{year}-{month_num:02d}-{day_num:02d}"

        events.append({
            "date": date_str,
            "day_name": dm.group(1),
            "start": start,
            "end": "",
            "artist": artist,
            "venue": "The Duke of Connaught",
            "venue_slug": "duke-connaught",
            "cost": "FREE",
            "source": "web:duke-connaught",
            "url": url,
            "cancelled": False,
            "repeating": False,
        })

    print(f"  [web] Duke of Connaught: {len(events)} events", file=sys.stderr)
    events.sort(key=lambda e: e["date"])
    return events


# ═══════════════════════════════════════════════════
#  WEB SCRAPER — The Swan, Clewer (GorillaHub)
# ═══════════════════════════════════════════════════

def scrape_swan(url, vname, vslug):
    """Scrape events from The Swan's GorillaHub events page.

    The page uses <h3> headings for event titles followed by a date
    line in the format 'Month Day, Year' inside a <p> tag.
    """
    try:
        resp = urllib.request.urlopen(url, timeout=10)
        html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  [WARN] swan-clewer: {e}", file=sys.stderr)
        return []

    soup = BeautifulSoup(html, "html.parser")
    events = []
    now = datetime.now()
    current_year = now.year

    # Find event blocks: <h3> title followed by a date div sibling
    for h3 in soup.find_all("h3"):
        title = h3.get_text(strip=True)
        if not title or title.lower() in ("events", "quick links", "opening hours"):
            continue

        # Look for the next sibling div/span/p with a date
        date_str = None
        description = ""
        for sib in h3.find_next_siblings():
            if sib.name == "h3":
                break
            if sib.name in ("div", "p", "span"):
                text = sib.get_text(strip=True)
                # Try to match "Month Day, Year" or "Month Day"
                dm = re.match(r'[^\w]*([A-Z][a-z]+)\s+(\d+)(?:st|nd|rd|th)?,?\s*(\d{4})?', text)
                if dm and not date_str:
                    month_name = dm.group(1)
                    day_num = int(dm.group(2))
                    year = int(dm.group(3)) if dm.group(3) else current_year
                    if month_name in MONTH_NAMES:
                        month_num = MONTH_NAMES.index(month_name) + 1
                        date_str = f"{year}-{month_num:02d}-{day_num:02d}"
                        continue
                if not description and text and len(text) > 20:
                    description = text

        if not date_str:
            continue

        # Filter to music-related events only
        music_keywords = [
            "live music", "live band", "acoustic", "singer", "songwriter",
            "gig", "dj", "open mic", "karaoke", "jam session",
            "swanfest", "beer fest", "tribute", "cover band",
            "rock", "blues", "jazz", "folk", "soul",
            "christmas in july",  # has live music
        ]
        is_music = any(kw in title.lower() for kw in music_keywords)
        if not is_music:
            continue

        events.append({
            "date": date_str,
            "day_name": datetime.strptime(date_str, "%Y-%m-%d").strftime("%A"),
            "start": "",
            "end": "",
            "artist": title,
            "venue": vname,
            "venue_slug": vslug,
            "cost": "",
            "source": "web:swan-clewer",
            "url": url,
            "cancelled": False,
            "repeating": "quiz" in title.lower(),
        })

    print(f"  [web] {vname}: {len(events)} events", file=sys.stderr)
    events.sort(key=lambda e: e["date"])
    return events


# ═══════════════════════════════════════════════════
#  WEB SCRAPER — WeBrew events page (The George + Unit 4)
# ═══════════════════════════════════════════════════

def scrape_webrew(url, vname, vslug):
    """Scrape events from Windsor & Eton Brewery's The Events Calendar page.

    The page lists <article> elements, each with a heading (event title),
    time element (date), and optional venue info in the text.
    Filters to only events matching the given venue name.
    """
    try:
        resp = urllib.request.urlopen(url, timeout=10)
        html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  [WARN] webrew: {e}", file=sys.stderr)
        return []

    soup = BeautifulSoup(html, "html.parser")
    events = []
    now = datetime.now()
    current_year = now.year

    # Map venue names in events to our slugs
    venue_names = {
        "unit-4": ["unit 4 taproom", "unit 4", "the taproom",
                    "windsor & eton brewery", "windsor beer festival"],
        "george-eton": ["the george"],
    }
    expected_names = venue_names.get(vslug, [])

    for article in soup.find_all("article"):
        # Event title from h4 heading
        h4 = article.find("h4")
        if not h4:
            continue
        a_tag = h4.find("a")
        title = (a_tag.get_text(strip=True) if a_tag else h4.get_text(strip=True))
        if not title:
            continue

        # Skip non-music/event listings (meeting rooms, accommodation, etc.)
        skip_keywords = ["the boardroom", "the mezzanine", "meeting room",
                         "accommodation", "brewery tour"]
        if any(kw in title.lower() for kw in skip_keywords):
            continue

        # Check venue relevance from article text
        article_text = article.get_text(" ", strip=True).lower()
        if not any(n in article_text for n in expected_names):
            continue

        # Try to extract date from time element
        date_str = None
        time_tag = article.find("time")
        if time_tag and time_tag.get("datetime"):
            dt_str = time_tag["datetime"]
            try:
                dt = datetime.strptime(dt_str[:10], "%Y-%m-%d")
                date_str = dt_str[:10]
            except ValueError:
                pass

        # Fallback: look for date in text (dd.mm.yyyy or similar)
        if not date_str:
            dm = re.search(r'(\d{1,2})[./](\d{1,2})[./](\d{4})', article_text)
            if dm:
                d, m, y = int(dm.group(1)), int(dm.group(2)), int(dm.group(3))
                date_str = f"{y}-{m:02d}-{d:02d}"

        if not date_str:
            continue

        # Extract description
        desc_p = article.find("p")
        description = desc_p.get_text(strip=True) if desc_p else ""

        events.append({
            "date": date_str,
            "day_name": datetime.strptime(date_str, "%Y-%m-%d").strftime("%A"),
            "start": "",
            "end": "",
            "artist": title,
            "venue": vname,
            "venue_slug": vslug,
            "cost": "FREE" if "free" in article_text else "",
            "source": "web:webrew",
            "url": a_tag["href"] if a_tag else url,
            "cancelled": False,
            "repeating": False,
        })

    print(f"  [web] {vname}: {len(events)} events", file=sys.stderr)
    events.sort(key=lambda e: e["date"])
    return events


# ═══════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════

def group_by_date(events):
    g = defaultdict(list)
    for e in events: g[e["date"]].append(e)
    return dict(g)

def filter_upcoming(events, days=90):
    today = date.today()
    cutoff = today + timedelta(days=days)
    out = []
    for e in events:
        if not e["date"]: continue
        try:
            d = datetime.strptime(e["date"], "%Y-%m-%d").date()
            if today <= d <= cutoff: out.append(e)
        except: pass
    return out

# ═══════════════════════════════════════════════════
#  CSS + TEMPLATES
# ═══════════════════════════════════════════════════

CSS = """
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#0d1117;--fg:#e6edf3;--card:#161b22;--card-hover:#1c2333;--border:#30363d;--accent:#58a6ff;--green:#3fb950;--amber:#d29922;--red:#f85149;--muted:#8b949e;--warm:#b8860b;--hero-bg:#1a0e0a;--font:'Segoe UI',Helvetica,Arial,sans-serif}
html{font-family:var(--font);font-size:16px;color:var(--fg);background:var(--bg)}
body{min-height:100vh;display:flex;flex-direction:column}

/* ── Hero ── */
.event-card .event-thumb{width:40px;height:40px;border-radius:6px;overflow:hidden;flex-shrink:0;border:1px solid var(--border)}
.event-card .event-thumb img{width:100%;height:100%;object-fit:cover;display:block}
.event-card .event-thumb:hover{border-color:var(--accent)}
.hero{position:relative;overflow:hidden;background:linear-gradient(160deg,#0d0806 0%,#1a0e0a 30%,#2a1a0e 50%,#1a0e0a 70%,#0d0806 100%);min-height:260px;display:flex;align-items:center;justify-content:center;padding:3rem 1.5rem}
.hero::before{content:'';position:absolute;inset:0;background:radial-gradient(ellipse 60% 35% at 50% 25%,rgba(180,130,30,.10) 0%,transparent 60%),radial-gradient(ellipse 40% 30% at 25% 70%,rgba(120,80,20,.06) 0%,transparent 50%),radial-gradient(ellipse 35% 25% at 75% 65%,rgba(100,60,15,.05) 0%,transparent 50%);pointer-events:none}
.hero::after{content:'';position:absolute;bottom:0;left:0;right:0;height:60%;background:linear-gradient(0deg,rgba(13,8,6,.3) 0%,transparent 100%);pointer-events:none}
.hero-amber{position:absolute;width:200px;height:200px;border-radius:50%;background:radial-gradient(circle,rgba(200,150,40,.06) 0%,transparent 60%);pointer-events:none}
.hero-amber:nth-child(1){top:5%;left:30%}
.hero-amber:nth-child(2){bottom:15%;right:20%;width:300px;height:300px;background:radial-gradient(circle,rgba(180,130,30,.04) 0%,transparent 50%)}
 /* Content on top */
.hero-content{position:relative;z-index:1;text-align:center;max-width:650px}
.hero-content h1{font-size:2.8rem;font-weight:800;letter-spacing:-.03em;color:#e6edf3;text-shadow:0 2px 20px rgba(0,0,0,.5);margin-bottom:.3rem}
.hero-content .tagline{font-size:1rem;color:var(--muted);margin-bottom:2rem}
.hero-content p{font-size:.9rem;color:var(--muted);max-width:500px;margin:0 auto}

/* ── Main content ── */
.wrapper{max-width:900px;margin:0 auto;padding:1.5rem 1.5rem 0;width:100%;flex:1}
a{color:var(--accent);text-decoration:none;border-bottom:1px solid transparent;transition:border-color .15s}
a:hover{border-bottom-color:var(--accent)}
nav{display:flex;gap:1.5rem;margin-bottom:1.5rem;font-size:.95rem}
nav a{color:var(--muted);border:0}
nav a:hover{color:var(--fg);border:0}

/* ── Day sections ── */
.day-section{margin-bottom:2rem}
.day-heading{font-size:1rem;font-weight:600;color:var(--accent);padding-bottom:.35rem;margin-bottom:.75rem;border-bottom:1px solid var(--border)}

/* ── Event cards ── */
.event-card{display:flex;align-items:center;gap:.65rem;padding:.6rem .85rem;background:var(--card);border:1px solid var(--border);border-radius:8px;margin-bottom:.4rem;transition:background .1s,border-color .15s}
.event-card:hover{background:var(--card-hover);border-color:var(--accent)}
.event-time{font-size:.82rem;font-weight:600;min-width:3.5rem;text-align:center;padding:.2rem .4rem;background:rgba(255,255,255,.04);border-radius:4px;white-space:nowrap}
.event-body{flex:1;display:flex;flex-direction:column}
.event-artist{font-weight:500;font-size:.95rem}
.event-genre{font-size:.75rem;color:var(--muted);margin-top:.1rem}
.event-tags{display:flex;gap:.35rem;align-items:center;flex-wrap:wrap}
.event-venue-tag{font-size:.78rem;color:var(--muted);padding:.12rem .4rem;background:rgba(88,166,255,.08);border-radius:4px;white-space:nowrap}
.cost-free{color:var(--green);font-size:.78rem;font-weight:600;white-space:nowrap}
.cost-other{color:var(--amber);font-size:.78rem;white-space:nowrap}
.cancelled{color:var(--red);font-weight:600;margin-left:.4rem}

/* ── Venue page ── */
.venue-header{margin-bottom:2rem}
.venue-header h2{font-size:1.5rem;margin-bottom:.25rem}
.venue-meta{color:var(--muted);font-size:.85rem;line-height:1.6}
.venue-meta a{font-size:.85rem}

/* ── About ── */
.about-section{line-height:1.7;max-width:65ch}
.about-section h2{font-size:1.2rem;color:var(--accent);margin:1.5rem 0 .5rem}
.about-section ul{list-style:none;padding:0}
.about-section li{padding:.35rem 0;padding-left:1.2rem;position:relative}
.about-section li::before{content:"→";position:absolute;left:0;color:var(--muted)}
.about-section p{margin:.75rem 0}

/* ── Footer ── */
.footer{margin-top:2rem;padding:1.5rem 0;border-top:1px solid var(--border);font-size:.8rem;color:var(--muted);display:flex;justify-content:space-between;flex-wrap:wrap;gap:.5rem}

@media(max-width:600px){
.hero{min-height:240px;padding:2rem 1rem}
.hero-content h1{font-size:2rem}
.wrapper{padding:1rem 1rem 0}
.event-card{flex-wrap:wrap;gap:.4rem}
.event-time{min-width:2.8rem}
.event-body{flex:1 1 100%;order:-1}
.event-tags{width:100%}
}
"""

HEAD = Template("""<!DOCTYPE html>
<html lang="en-GB">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>$title</title>
<meta name="description" content="$desc">
<meta name="keywords" content="live music,Windsor,Eton,Clewer,pubs,breweries,gig guide">
<meta property="og:title" content="$ogtitle">
<meta property="og:description" content="$desc">
<meta property="og:type" content="website">
<meta property="og:url" content="https://draggedout.cybr.fi/">
<meta property="og:locale" content="en_GB">
<meta name="twitter:card" content="summary">
<meta name="twitter:title" content="$ogtitle">
<script type="application/ld+json">$jsonld</script>
<link rel="alternate" type="application/rss+xml" title="Dragged Out — Local Live Music" href="https://draggedout.cybr.fi/feed.xml">
<style>$css</style>
</head>
<body>
""")

HERO = """<header class="hero">
<div class="hero-amber"></div>
<div class="hero-amber"></div>
<div class="hero-content">
<h1>Dragged Out</h1>
<p class="tagline">Local live music &mdash; because you didn&rsquo;t want to go out but went anyway</p>
<nav><a href="/">Calendar</a><a href="/about.html">About</a><a href="/venues/">Venues</a></nav>
</div>
</header>"""

FOOT = """<div class="wrapper">
<footer class="footer">
<span>Data from Lemonrock, venue websites &amp; Instagram. Updated weekly.</span>
<span><a href="https://github.com/paulkoan/dragged-out">GitHub</a></span>
</footer></div></body></html>"""

def hero_ld():
    return json.dumps({"@context":"https://schema.org","@type":"WebSite",
                       "name":"Dragged Out","url":"https://draggedout.cybr.fi/",
                       "description":"Local live music calendar for pubs and breweries in Windsor and Eton"})

def event_ld(e):
    loc = {"@type":"Place","name":e["venue"],"address":""}
    obj = {"@context":"https://schema.org","@type":"Event",
           "name":f"{e['artist']} at {e['venue']}",
           "startDate":e["date"],"endDate":e["date"],
           "location":loc,"performer":{"@type":"Person","name":e["artist"]},
           "eventStatus":"https://schema.org/EventScheduled",
           "eventAttendanceMode":"https://schema.org/OfflineEventAttendanceMode"}
    if e.get("start"): obj["startTime"] = e["start"]
    if e["cost"] == "FREE": obj["isAccessibleForFree"] = True
    return json.dumps(obj).replace("</script>", "<\\/script>")

def event_card(e):
    ld = event_ld(e)
    time_fmt = e["start"][:5] if e["start"] else ""
    cost_html = ("<span class='cost-free'>Free</span>" if e["cost"]=="FREE"
                 else f"<span class='cost-other'>{e['cost']}</span>")
    cancelled = "<span class='cancelled'>✕ CANCELLED</span>" if e.get("cancelled") else ""
    genre_html = f"<div class='event-genre'>{e['genre']}</div>" if e.get("genre") else ""
    
    # Thumbnail — clickable image linking to YouTube
    thumb_html = ""
    if e.get("image") and e.get("youtube"):
        thumb_html = f"<a href='{e['youtube']}' target='_blank' rel='noopener' class='event-thumb'><img src='{e['image']}' alt='{e['artist']}' loading='lazy'></a>"
    # Fallback text link when no image available
    yt_link = ""
    if e.get("youtube"):
        yt_link = f"<a href='{e['youtube']}' target='_blank' rel='noopener' style='font-size:.75rem;color:var(--muted)'>▶</a>"
    
    # Band name hyperlink — link to the gig/venue page if available, otherwise a Google search
    band_url = e.get("url") or ""
    band_url = band_url.strip()
    if not band_url:
        band_url = f"https://www.google.com/search?q={urllib.parse.quote(e['artist'] + ' live music Windsor')}"
    artist_html = f"<a href='{band_url}' target='_blank' rel='noopener noreferrer'>{e['artist']}</a>"
    
    return f"""<div class="event-card" itemscope itemtype="https://schema.org/Event">
<script type="application/ld+json">{ld}</script>
{thumb_html}
<div class="event-time">{time_fmt}</div>
<div class="event-body">
<div class="event-artist">{artist_html}{cancelled}</div>
{genre_html}
</div>
<div class="event-tags">
<span class='event-venue-tag'>{e['venue']}</span>
{cost_html}
{yt_link}
</div>
</div>"""

def day_section(date_str, events):
    d = datetime.strptime(date_str, "%Y-%m-%d")
    day_s = events[0].get("day_name", d.strftime("%A"))
    short = DSHORT.get(day_s, day_s[:3])
    label = f"{short} {d.day} {MONTHS[d.month-1]} {d.year}"
    cards = "\n".join(event_card(ev) for ev in events)
    return f"""<section class="day-section">
<h2 class="day-heading">{label}</h2>
{cards}
</section>"""

# ── Page builders ──

def build_index(events, venues):
    up = filter_upcoming(events, 90)
    grouped = group_by_date(up)
    today = date.today()
    sections = []
    shown = set()
    days_until_thu = (3 - today.weekday()) % 7
    start = today + timedelta(days=days_until_thu)
    for wo in range(5):
        for d in range(4):
            dt = start + timedelta(weeks=wo, days=d)
            ds = dt.strftime("%Y-%m-%d")
            if ds in grouped and ds not in shown:
                sections.append(day_section(ds, grouped[ds]))
                shown.add(ds)
    for ds in sorted(grouped.keys()):
        if ds not in shown:
            sections.append(day_section(ds, grouped[ds]))
            shown.add(ds)
    body = "\n".join(sections)
    return (HEAD.substitute(title="Dragged Out \u2014 Live Music in Windsor & Eton",
                            desc="Pubs and breweries with live music in Windsor, Clewer, and Eton.",
                            ogtitle="Dragged Out \u2014 Local Live Music",
                            jsonld=hero_ld(), css=CSS)
            + HERO + '<div class="wrapper"><main>\n' + body + "\n</main>\n" + FOOT)

def build_venue_page(venue, events):
    n = venue["name"]
    closed = venue.get("status") == "permanently_closed"

    if closed:
        body = f"""<main>
<div class="venue-header">
<h2>{n}</h2>
<p class="venue-meta">
<span style="color:var(--red);font-weight:600">⛔ PERMANENTLY CLOSED</span><br>
{venue.get("address","")}<br>
{venue.get("area","").title()}<br>
{venue.get("notes","").replace(chr(10),"<br>")}
</p>
</div>
</main>"""
    else:
        v_events = [e for e in events if e["venue_slug"] == venue["slug"]]
        v_events.sort(key=lambda e: e["date"])
        up = filter_upcoming(v_events, 180)
        grouped = group_by_date(up)
        sections = []
        for ds in sorted(grouped.keys()):
            sections.append(day_section(ds, grouped[ds]))
        body = f"""<main>
<div class="venue-header">
<h2>{n}</h2>
<p class="venue-meta">
{venue.get("address","")}<br>
{venue.get("area","").title()} &middot; {len(v_events)} gigs listed{f' &middot; <a href="{venue.get("website","")}">Website</a>' if venue.get("website") else ''}{f' &middot; <a href="https://instagram.com/{venue.get("instagram")}">Instagram</a>' if venue.get("instagram") else ''}
</p>
</div>
{"".join(sections) if sections else "<p style='color:var(--muted)'>No upcoming gigs.</p>"}
</main>"""
    if closed:
        desc = f"{n} — permanently closed (converted to housing, CAMRA confirmed 2025)"
    else:
        desc = f"Live music at {n} in {venue.get('area','').title()}"
    return (HEAD.substitute(title=f"{n} — Dragged Out",
                            desc=desc,
                            ogtitle=f"{n} — Dragged Out",
                            jsonld=json.dumps({"@context":"https://schema.org","@type":"Place","name":n}), css=CSS)
            + HERO + '<div class="wrapper">' + body + FOOT)

def build_venues_index(events, venues):
    active_venues = [v for v in venues if v.get("status") != "permanently_closed"]
    closed_venues = [v for v in venues if v.get("status") == "permanently_closed"]
    rows = []
    for v in active_venues:
        count = len([e for e in events if e["venue_slug"] == v["slug"]])
        rows.append(f"""<div class="event-card">
<div class="event-body"><div class="event-artist"><a href="/venue/{v['slug']}.html">{v['name']}</a></div></div>
<span style='font-size:.8rem;color:var(--muted)'>{v.get('area','').title()}</span>
<span style='font-size:.8rem;color:var(--muted)'>{count} gigs</span>
</div>""")
    if closed_venues:
        rows.append(f"""<div style="margin-top:2rem;padding-top:1rem;border-top:1px solid var(--border)">
<p style="font-size:.8rem;color:var(--muted);margin-bottom:.5rem">Permanently closed:</p>""")
        for v in closed_venues:
            rows.append(f"""<div class="event-card" style="opacity:.5">
<div class="event-body"><div class="event-artist">{v['name']}</div></div>
<span style='font-size:.8rem;color:var(--red)'>{v.get('area','').title()} · Closed</span>
</div>""")
    body = f"""<main>
<h2 style='margin-bottom:1rem;font-size:1.2rem'>Venues</h2>
<p style='color:var(--muted);margin-bottom:1rem'>Pubs and breweries in the area with live music.</p>
{"".join(rows)}
</main>"""
    return (HEAD.substitute(title="Venues \u2014 Dragged Out",
                            desc=f"{len(venues)} venues with live music in Windsor, Clewer, and Eton.",
                            ogtitle="Venues \u2014 Dragged Out",
                            jsonld=json.dumps({"@context":"https://schema.org","@type":"CollectionPage","name":"Venues"}), css=CSS)
            + HERO + '<div class="wrapper">' + body + FOOT)

def build_about():
    body = """<main class="about-section">
<h2>What is Dragged Out?</h2>
<p>A calendar of live music at pubs and breweries in the Windsor, Clewer, Old Windsor and Eton area.</p>
<p>Local venues have live music all the time &mdash; but finding it means checking Lemonrock, Facebook, Instagram, or each pub&rsquo;s website separately. This collects it all in one place.</p>
<h2>How it works</h2>
<ul>
<li>Data is scraped weekly from Lemonrock, venue websites, and Instagram</li>
<li>Every Wednesday the site regenerates with the latest listings</li>
<li>Music runs Thursday through Sunday</li>
<li>Most gigs are free entry</li>
</ul>
<h2>Coverage</h2>
<p>Currently tracking venues across Windsor, Clewer Village, Eton, and Old Windsor. Send tips for new venues.</p>
<h2>Tech</h2>
<ul>
<li>Built with Python, static HTML, and JSON-LD for Google</li>
<li>Hosted on GitHub Pages at <strong>draggedout.cybr.fi</strong></li>
<li>Part of <a href="https://cybr.fi">cybr.fi</a></li>
</ul>
</main>"""
    return (HEAD.substitute(title="About \u2014 Dragged Out",
                            desc="About the Dragged Out live music calendar",
                            ogtitle="About \u2014 Dragged Out",
                            jsonld=json.dumps({"@context":"https://schema.org","@type":"WebPage","name":"About"}), css=CSS)
            + HERO + '<div class="wrapper">' + body + FOOT)

def build_sitemap(events, venues):
    urls = ["https://draggedout.cybr.fi/","https://draggedout.cybr.fi/about.html","https://draggedout.cybr.fi/venues/"]
    for v in venues:
        if v.get("status") != "permanently_closed":
            urls.append(f"https://draggedout.cybr.fi/venue/{v['slug']}.html")
    return '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + \
           "\n".join(f"<url><loc>{u}</loc></url>" for u in urls) + "\n</urlset>"

# ═══════════════════════════════════════════════════
#  RSS FEED
# ═══════════════════════════════════════════════════

def _xml_escape(s):
    s = str(s) if s is not None else ""
    return s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace('"',"&quot;").replace("'","&apos;")

def build_feed(events):
    up = filter_upcoming(events, 90)
    now = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    items = []
    for e in up:
        title = f"{e['artist']} at {e['venue']}"
        date_d = datetime.strptime(e["date"], "%Y-%m-%d")
        pub_date = date_d.strftime("%a, %d %b %Y %H:%M:%S +0000")
        desc_parts = [f"<p><strong>{_xml_escape(e['artist'])}</strong> at {_xml_escape(e['venue'])}</p>"]
        if e.get("genre"):
            desc_parts.append(f"<p>Genre: {_xml_escape(e['genre'])}</p>")
        if e.get("start"):
            desc_parts.append(f"<p>Time: {_xml_escape(e['start'])}</p>")
        if e.get("cost"):
            desc_parts.append(f"<p>Entry: {_xml_escape(e['cost'])}</p>")
        if e.get("cancelled"):
            desc_parts.append("<p><strong>CANCELLED</strong></p>")
        description = "".join(desc_parts)
        items.append(f"""  <item>
    <title>{_xml_escape(title)}</title>
    <link>https://draggedout.cybr.fi/</link>
    <guid isPermaLink="false">draggedout-{e['date']}-{_xml_escape(e['artist'])}-{_xml_escape(e['venue'])}</guid>
    <description><![CDATA[{description}]]></description>
    <pubDate>{pub_date}</pubDate>
  </item>""")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
<channel>
  <title>Dragged Out — Local Live Music</title>
  <link>https://draggedout.cybr.fi/</link>
  <description>Live music at pubs and breweries in Windsor, Clewer, and Eton</description>
  <language>en-gb</language>
  <lastBuildDate>{now}</lastBuildDate>
  <atom:link href="https://draggedout.cybr.fi/feed.xml" rel="self" type="application/rss+xml"/>
{chr(10).join(items)}
</channel>
</rss>"""

# ═══════════════════════════════════════════════════

def build():
    print("Scraping...", file=sys.stderr)
    events, venues = scrape_all()
    print(f"Total: {len(events)} raw events", file=sys.stderr)

    print("Enriching with band info...", file=sys.stderr)
    bands_cache = load_bands_cache()
    events = enrich_events(events, bands_cache)

    DATA.mkdir(exist_ok=True)
    clean_events = []
    for e in events:
        ce = dict(e)
        clean_events.append(ce)
    json.dump(clean_events, (DATA / "events.json").open("w"), indent=2)

    SITE.mkdir(exist_ok=True)
    (SITE / "index.html").write_text(build_index(events, venues)); print("  index.html", file=sys.stderr)
    (SITE / "venues").mkdir(exist_ok=True)
    (SITE / "venues" / "index.html").write_text(build_venues_index(events, venues))
    (SITE / "venue").mkdir(exist_ok=True)
    for v in venues:
        if v.get("status") != "permanently_closed":
            (SITE / "venue" / f"{v['slug']}.html").write_text(build_venue_page(v, events))
            print(f"  venue/{v['slug']}.html", file=sys.stderr)
    (SITE / "about.html").write_text(build_about()); print("  about.html", file=sys.stderr)
    (SITE / "sitemap.xml").write_text(build_sitemap(events, venues))
    (SITE / "feed.xml").write_text(build_feed(events))
    (SITE / "robots.txt").write_text("User-agent: *\nAllow: /\nSitemap: https://draggedout.cybr.fi/sitemap.xml\n")
    (SITE / "CNAME").write_text("draggedout.cybr.fi\n")
    print("  sitemap, feed, robots, CNAME", file=sys.stderr)
    print(f"Done. {sum(1 for e in events if e.get('genre'))} events with genre info.", file=sys.stderr)

def build_no_scrape():
    events = json.load((DATA / "events.json").open())
    venues = load_venues()
    SITE.mkdir(exist_ok=True)
    (SITE / "index.html").write_text(build_index(events, venues))
    (SITE / "venues").mkdir(exist_ok=True)
    (SITE / "venues" / "index.html").write_text(build_venues_index(events, venues))
    (SITE / "venue").mkdir(exist_ok=True)
    for v in venues:
        (SITE / "venue" / f"{v['slug']}.html").write_text(build_venue_page(v, events))
    (SITE / "about.html").write_text(build_about())
    (SITE / "sitemap.xml").write_text(build_sitemap(events, venues))
    (SITE / "feed.xml").write_text(build_feed(events))
    (SITE / "robots.txt").write_text("User-agent: *\nAllow: /\nSitemap: https://draggedout.cybr.fi/sitemap.xml\n")
    (SITE / "CNAME").write_text("draggedout.cybr.fi\n")

def serve(port=8080):
    os.chdir(SITE)
    handler = http.server.SimpleHTTPRequestHandler
    with socketserver.TCPServer(("", port), handler) as httpd:
        print(f"Serving at http://localhost:{port}", file=sys.stderr)
        httpd.serve_forever()

if __name__ == "__main__":
    if "--no-scrape" in sys.argv: build_no_scrape()
    elif "--serve" in sys.argv: serve()
    else: build()