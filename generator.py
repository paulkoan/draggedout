#!/usr/bin/env python3
"""Dragged Out site generator — scrapes data sources, enriches with genre/YouTube, builds HTML.

Usage:
    python3 generator.py              # scrape + enrich + build
    python3 generator.py --no-scrape  # build from cache
    python3 generator.py --serve      # local preview on :8080
"""

import csv, json, os, sys, re, urllib.request, urllib.parse, http.server, socketserver
from datetime import datetime, date, timedelta
from pathlib import Path
from collections import defaultdict
from string import Template

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

def fetch_genre_from_gig(gig_url):
    """Fetch a Lemonrock gig page and extract genre from the embedded JSON-LD.

    Each gig page has Schema.org JSON-LD with performer info including genre:
      \"performer\": {\"@type\": \"PerformingGroup\", \"name\": \"Euphoria\", \"genre\": \"Indie Rock\"}
    """
    try:
        resp = urllib.request.urlopen(gig_url, timeout=8)
        html = resp.read().decode("utf-8", errors="replace")
        # Find JSON-LD script block
        m = re.search(r'<script type="application/ld\+json">\s*(.*?)\s*</script>', html, re.DOTALL)
        if not m:
            return None
        data = json.loads(m.group(1))
        # performer can be a dict or a list
        performers = data.get("performer", [])
        if isinstance(performers, dict):
            performers = [performers]
        for p in performers:
            genre = p.get("genre")
            if genre:
                return str(genre).strip()
        return None
    except Exception:
        return None

def enrich_events(events, bands_cache):
    """Add genre and youtube to events, using cache when possible."""
    n_fetched = 0
    for e in events:
        artist = e["artist"]
        if artist in bands_cache:
            info = bands_cache[artist]
            e["genre"] = info.get("genre")
            e["youtube"] = info.get("youtube")
        elif e.get("url"):
            print(f"  [band] fetching genre for '{artist}'...", file=sys.stderr)
            genre = fetch_genre_from_gig(e["url"])
            # Default YouTube: search link — can be overridden manually in bands.json
            yt_search = f"https://www.youtube.com/results?search_query={urllib.parse.quote(artist + ' band')}"
            bands_cache[artist] = {"genre": genre, "youtube": yt_search}
            e["genre"] = genre
            e["youtube"] = yt_search
            n_fetched += 1
            import time
            time.sleep(0.5)
        else:
            e["genre"] = None
            e["youtube"] = None
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
        if not slug: continue
        rows = fetch_lr(slug)
        for r in rows:
            ev = parse_lr(r, v["name"], v["slug"])
            if ev: events.append(ev)
        print(f"  [lr] {v['name']}: {len(rows)}", file=sys.stderr)
    events.sort(key=lambda e: e["date"])
    return events, venues

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
.hero{position:relative;overflow:hidden;background:var(--hero-bg);min-height:320px;display:flex;align-items:center;justify-content:center;padding:3rem 1.5rem}
.hero-scene{position:absolute;inset:0;pointer-events:none}
/* Pub ceiling/wall */
.hero-scene::before{content:'';position:absolute;inset:0;background:radial-gradient(ellipse 70% 50% at 50% 30%,#2a1a0e 0%,transparent 70%),radial-gradient(ellipse 60% 40% at 30% 60%,#1a0e0a 0%,transparent 60%)}
/* Warm light pools */
.hero-light{position:absolute;border-radius:50%;background:radial-gradient(circle,rgba(184,134,11,.15) 0%,transparent 60%);width:300px;height:300px}
.hero-light:nth-child(1){top:15%;left:20%}
.hero-light:nth-child(2){top:10%;right:25%;width:250px;height:250px}
.hero-light:nth-child(3){bottom:5%;left:40%;width:350px;height:350px}
/* Band silhouettes — out of focus */
.hero-band{position:absolute;bottom:10%;width:80px;height:180px;background:radial-gradient(ellipse 30px 80px at 50% 50%,#0a0503 0%,transparent 70%);filter:blur(8px);opacity:.6}
.hero-band:nth-child(4){left:25%}
.hero-band:nth-child(5){left:38%;width:60px;height:160px}
.hero-band:nth-child(6){left:55%;width:70px;height:170px}
/* Ambient haze */
.hero-haze{position:absolute;inset:0;background:radial-gradient(ellipse 50% 30% at 50% 70%,rgba(88,166,255,.03) 0%,transparent 50%)}
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
<style>$css</style>
</head>
<body>
""")

HERO = """<header class="hero">
<div class="hero-scene">
<div class="hero-light"></div>
<div class="hero-light"></div>
<div class="hero-light"></div>
<div class="hero-band"></div>
<div class="hero-band"></div>
<div class="hero-band"></div>
<div class="hero-haze"></div>
</div>
<div class="hero-content">
<h1>Dragged Out</h1>
<p class="tagline">Local live music &mdash; because you didn&rsquo;t want to go out but went anyway</p>
<nav><a href="/">Calendar</a><a href="/about.html">About</a><a href="/venues/">Venues</a></nav>
</div>
</header>
"""

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
    youtube_html = ""
    if e.get("youtube"):
        youtube_html = f"<a href='{e['youtube']}' target='_blank' rel='noopener' style='font-size:.75rem;color:var(--muted)'>▶ YouTube</a>"
    return f"""<div class="event-card" itemscope itemtype="https://schema.org/Event">
<script type="application/ld+json">{ld}</script>
<div class="event-time">{time_fmt}</div>
<div class="event-body">
<div class="event-artist">{e['artist']}{cancelled}</div>
{genre_html}
</div>
<div class="event-tags">
<span class='event-venue-tag'>{e['venue']}</span>
{cost_html}
{youtube_html}
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
    v_events = [e for e in events if e["venue_slug"] == venue["slug"]]
    v_events.sort(key=lambda e: e["date"])
    up = filter_upcoming(v_events, 180)
    grouped = group_by_date(up)
    sections = []
    for ds in sorted(grouped.keys()):
        sections.append(day_section(ds, grouped[ds]))
    n = venue["name"]
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
    return (HEAD.substitute(title=f"{n} \u2014 Dragged Out",
                            desc=f"Live music at {n} in {venue.get('area','').title()}",
                            ogtitle=f"{n} \u2014 Dragged Out",
                            jsonld=json.dumps({"@context":"https://schema.org","@type":"Place","name":n}), css=CSS)
            + HERO + '<div class="wrapper">' + body + FOOT)

def build_venues_index(events, venues):
    rows = []
    for v in venues:
        count = len([e for e in events if e["venue_slug"] == v["slug"]])
        rows.append(f"""<div class="event-card">
<div class="event-body"><div class="event-artist"><a href="/venue/{v['slug']}.html">{v['name']}</a></div></div>
<span style='font-size:.8rem;color:var(--muted)'>{v.get('area','').title()}</span>
<span style='font-size:.8rem;color:var(--muted)'>{count} gigs</span>
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
        urls.append(f"https://draggedout.cybr.fi/venue/{v['slug']}.html")
    return '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + \
           "\n".join(f"<url><loc>{u}</loc></url>" for u in urls) + "\n</urlset>"

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
        (SITE / "venue" / f"{v['slug']}.html").write_text(build_venue_page(v, events))
        print(f"  venue/{v['slug']}.html", file=sys.stderr)
    (SITE / "about.html").write_text(build_about()); print("  about.html", file=sys.stderr)
    (SITE / "sitemap.xml").write_text(build_sitemap(events, venues))
    (SITE / "robots.txt").write_text("User-agent: *\nAllow: /\nSitemap: https://draggedout.cybr.fi/sitemap.xml\n")
    (SITE / "CNAME").write_text("draggedout.cybr.fi\n")
    print("  sitemap, robots, CNAME", file=sys.stderr)
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