#!/usr/bin/env python3
"""Dragged Out site generator — scrapes data sources, builds static HTML.

Usage:
    python3 generator.py              # scrape + build
    python3 generator.py --no-scrape  # build from existing data/events.json
    python3 generator.py --serve      # local preview on :8080
"""

import csv
import json
import os
import sys
import http.server
import socketserver
from datetime import datetime, date, timedelta
from pathlib import Path
from collections import defaultdict
from string import Template

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"

# Output to build/ for gh-pages deploy
SITE = ROOT / "build"

MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
DAY_SHORT = {"Monday":"Mon","Tuesday":"Tue","Wednesday":"Wed",
             "Thursday":"Thu","Friday":"Fri","Saturday":"Sat","Sunday":"Sun"}

def load_venues():
    import yaml
    return yaml.safe_load((ROOT / "venues.yaml").read_text())["venues"]

def fetch_lemonrock(slug):
    url = f"https://www.lemonrock.com/csv.php?t={slug}&y=5"
    try:
        import urllib.request
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

def scrape_lemonrock(venues):
    events = []
    for v in venues:
        slug = v.get("sources",{}).get("lemonrock")
        if not slug: continue
        rows = fetch_lemonrock(slug)
        for r in rows:
            ev = parse_lr(r, v["name"], v["slug"])
            if ev: events.append(ev)
        print(f"  [lemonrock] {v['name']}: {len(rows)}", file=sys.stderr)
    return events

def scrape_all():
    venues = load_venues()
    events = scrape_lemonrock(venues)
    events.sort(key=lambda e: e["date"])
    return events, venues

def group_by_date(events):
    g = defaultdict(list)
    for e in events:
        g[e["date"]].append(e)
    return dict(g)

def filter_upcoming(events, days=90):
    today = date.today()
    cutoff = today + timedelta(days=days)
    out = []
    for e in events:
        if not e["date"]: continue
        try:
            d = datetime.strptime(e["date"], "%Y-%m-%d").date()
            if today <= d <= cutoff:
                out.append(e)
        except:
            pass
    return out

# ── CSS ──
CSS = """
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#0d1117;--fg:#e6edf3;--card:#161b22;--card-hover:#1c2333;--border:#30363d;--accent:#58a6ff;--green:#3fb950;--amber:#d29922;--red:#f85149;--muted:#8b949e;--font:'Segoe UI',Helvetica,Arial,sans-serif}
html{font-family:var(--font);font-size:16px;color:var(--fg);background:var(--bg)}
body{max-width:900px;margin:0 auto;padding:1.5rem;min-height:100vh;display:flex;flex-direction:column}
a{color:var(--accent);text-decoration:none;border-bottom:1px solid transparent;transition:border-color .15s}
a:hover{border-bottom-color:var(--accent)}
.site-header{margin-bottom:2rem}
.site-header h1{font-size:2rem;font-weight:800;letter-spacing:-0.03em}
.site-header .subtitle{color:var(--muted);font-size:.9rem;margin-top:.2rem}
nav{display:flex;gap:1.5rem;margin-top:.75rem;font-size:.95rem}
nav a{color:var(--muted);border:0}
nav a:hover{color:var(--fg);border:0}
.day-section{margin-bottom:2rem}
.day-heading{font-size:1rem;font-weight:600;color:var(--accent);padding-bottom:.35rem;margin-bottom:.75rem;border-bottom:1px solid var(--border)}
.event-card{display:flex;align-items:center;gap:.75rem;padding:.7rem .9rem;background:var(--card);border:1px solid var(--border);border-radius:8px;margin-bottom:.4rem;transition:background .1s,border-color .15s}
.event-card:hover{background:var(--card-hover);border-color:var(--accent)}
.event-time{font-size:.85rem;font-weight:600;color:var(--fg);min-width:4rem;white-space:nowrap;text-align:center;padding:.2rem .5rem;background:rgba(255,255,255,.04);border-radius:4px}
.event-artist{flex:1;font-weight:500;font-size:.95rem}
.event-venue-tag{font-size:.8rem;color:var(--muted);padding:.15rem .45rem;background:rgba(88,166,255,.08);border-radius:4px;white-space:nowrap}
.cost-free{color:var(--green);font-size:.8rem;font-weight:600}
.cost-other{color:var(--amber);font-size:.8rem}
.cancelled{color:var(--red);font-weight:600;margin-left:.5rem}
.venue-header{margin-bottom:2rem}
.venue-header h2{font-size:1.5rem;margin-bottom:.25rem}
.venue-meta{color:var(--muted);font-size:.85rem;line-height:1.6}
.venue-meta a{font-size:.85rem}
.about-section{line-height:1.7;max-width:65ch}
.about-section h2{font-size:1.2rem;color:var(--accent);margin:1.5rem 0 .5rem}
.about-section ul{list-style:none;padding:0}
.about-section li{padding:.35rem 0;padding-left:1.2rem;position:relative}
.about-section li::before{content:"→";position:absolute;left:0;color:var(--muted)}
.about-section p{margin:.75rem 0}
.footer{margin-top:auto;padding-top:1.5rem;border-top:1px solid var(--border);font-size:.8rem;color:var(--muted);display:flex;justify-content:space-between;flex-wrap:wrap;gap:.5rem}
@media(max-width:600px){body{padding:1rem}.event-card{flex-wrap:wrap;gap:.4rem}.event-time{min-width:3rem}.event-artist{flex:1 1 100%;order:-1}.day-heading{font-size:.9rem}}
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
<header class="site-header">
<h1>Dragged Out</h1>
<p class="subtitle">Local live music in Windsor, Clewer &amp; Eton &mdash; because you didn&rsquo;t want to go out but went anyway</p>
<nav><a href="/">Calendar</a><a href="/about.html">About</a><a href="/venues/">Venues</a></nav>
</header>
""")

FOOT = """<footer class="footer">
<span>Data from Lemonrock, venue websites &amp; Instagram. Updated weekly.</span>
<span><a href="https://github.com/paulkoan/dragged-out">GitHub</a></span>
</footer></body></html>"""

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
    tag_html = f"<span class='event-venue-tag'>{e['venue']}</span>"
    cancelled = "<span class='cancelled'>✕ CANCELLED</span>" if e.get("cancelled") else ""
    return f"""<div class="event-card" itemscope itemtype="https://schema.org/Event">
<script type="application/ld+json">{ld}</script>
<div class="event-time">{time_fmt}</div>
<div class="event-artist">{e['artist']}{cancelled}</div>
{tag_html}
{cost_html}
</div>"""

def day_section(date_str, events):
    d = datetime.strptime(date_str, "%Y-%m-%d")
    day_s = events[0].get("day_name", d.strftime("%A"))
    short = DAY_SHORT.get(day_s, day_s[:3])
    date_label = f"{short} {d.day} {MONTH_NAMES[d.month-1]} {d.year}"
    cards = "\n".join(event_card(ev) for ev in events)
    return f"""<section class="day-section">
<h2 class="day-heading">{date_label}</h2>
{cards}
</section>"""

def build_index(events, venues):
    up = filter_upcoming(events, 90)
    grouped = group_by_date(up)
    today = date.today()
    sections = []
    shown = set()
    days_until_thu = (3 - today.weekday()) % 7
    start = today + timedelta(days=days_until_thu)
    for week_offset in range(5):
        for d in range(4):
            dt = start + timedelta(weeks=week_offset, days=d)
            ds = dt.strftime("%Y-%m-%d")
            if ds in grouped and ds not in shown:
                sections.append(day_section(ds, grouped[ds]))
                shown.add(ds)
    remaining = sorted(grouped.keys())
    for ds in remaining:
        if ds not in shown:
            sections.append(day_section(ds, grouped[ds]))
            shown.add(ds)
    ws_ld = json.dumps({"@context":"https://schema.org","@type":"WebSite",
                        "name":"Dragged Out",
                        "url":"https://draggedout.cybr.fi/"})
    body = "\n".join(sections)
    return (HEAD.substitute(title="Dragged Out \u2014 Live Music in Windsor & Eton",
                            desc="Pubs and breweries with live music in Windsor, Clewer, and Eton. Find your next local gig.",
                            ogtitle="Dragged Out \u2014 Local Live Music",
                            jsonld=ws_ld, css=CSS)
            + "<main>\n" + body + "\n</main>\n" + FOOT)

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
{"".join(sections) if sections else "<p style='color:var(--muted)'>No upcoming gigs listed yet.</p>"}
</main>"""
    return (HEAD.substitute(title=f"{n} \u2014 Dragged Out",
                            desc=f"Live music at {n} in {venue.get('area','').title()}",
                            ogtitle=f"{n} \u2014 Dragged Out",
                            jsonld=json.dumps({"@context":"https://schema.org","@type":"Place","name":n}), css=CSS)
            + body + FOOT)

def build_venues_index(events, venues):
    rows = []
    for v in venues:
        count = len([e for e in events if e["venue_slug"] == v["slug"]])
        rows.append(f"""<div class="event-card">
<div class="event-artist"><a href="/venue/{v['slug']}.html">{v['name']}</a></div>
<span style='font-size:.8rem;color:var(--muted)'>{v.get('area','').title()}</span>
<span style='font-size:.8rem;color:var(--muted)'>{count} gigs listed</span>
</div>""")
    list_html = "\n".join(rows)
    body = f"""<main>
<h2 style='margin-bottom:1rem;font-size:1.2rem'>Venues</h2>
<p style='color:var(--muted);margin-bottom:1rem'>Pubs and breweries in the area with live music.</p>
{list_html}
</main>"""
    return (HEAD.substitute(title="Venues \u2014 Dragged Out",
                            desc=f"{len(venues)} venues with live music in Windsor, Clewer, and Eton.",
                            ogtitle="Venues \u2014 Dragged Out",
                            jsonld=json.dumps({"@context":"https://schema.org","@type":"CollectionPage","name":"Venues"}), css=CSS)
            + body + FOOT)

def build_about():
    body = """<main class="about-section">
<h2>What is Dragged Out?</h2>
<p>A calendar of live music at pubs and breweries in the Windsor, Clewer, Old Windsor and Eton area.</p>
<p>Local venues have live music all the time &mdash; but finding out about it means checking Lemonrock, Facebook, Instagram, or each pub&rsquo;s website separately. Miss one, miss the gig. This site collects it all in one place.</p>
<h2>How it works</h2>
<ul>
<li>Data is scraped weekly from Lemonrock, venue websites, and Instagram</li>
<li>Each Wednesday the site regenerates with the latest gig listings</li>
<li>Music tends to be Thursday through Sunday</li>
<li>Most gigs are free entry</li>
</ul>
<h2>Coverage</h2>
<p>Currently tracking venues across Windsor, Clewer Village, Eton, and Old Windsor. If you know a venue that should be listed, get in touch.</p>
<h2>Tech</h2>
<ul>
<li>Built with Python, static HTML, and JSON-LD for Google</li>
<li>Hosted on GitHub Pages at <strong>draggedout.cybr.fi</strong></li>
<li>Part of the <a href="https://cybr.fi">cybr.fi</a> project</li>
</ul>
</main>"""
    return (HEAD.substitute(title="About \u2014 Dragged Out",
                            desc="About the Dragged Out live music calendar",
                            ogtitle="About \u2014 Dragged Out",
                            jsonld=json.dumps({"@context":"https://schema.org","@type":"WebPage","name":"About"}), css=CSS)
            + body + FOOT)

def build_sitemap(events, venues):
    urls = [
        "https://draggedout.cybr.fi/",
        "https://draggedout.cybr.fi/about.html",
        "https://draggedout.cybr.fi/venues/",
    ]
    for v in venues:
        urls.append(f"https://draggedout.cybr.fi/venue/{v['slug']}.html")
    return '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + \
           "\n".join(f"<url><loc>{u}</loc></url>" for u in urls) + "\n</urlset>"

# ═══════════════════════════════════════════════════

def build():
    print("Scraping...", file=sys.stderr)
    events, venues = scrape_all()
    print(f"Total: {len(events)} events from {len(venues)} venues", file=sys.stderr)
    DATA.mkdir(exist_ok=True)
    json.dump(events, (DATA / "events.json").open("w"), indent=2)
    SITE.mkdir(exist_ok=True)
    (SITE / "index.html").write_text(build_index(events, venues)); print("  index.html", file=sys.stderr)
    (SITE / "venues").mkdir(exist_ok=True)
    (SITE / "venues" / "index.html").write_text(build_venues_index(events, venues)); print("  venues/index.html", file=sys.stderr)
    (SITE / "venue").mkdir(exist_ok=True)
    for v in venues:
        (SITE / "venue" / f"{v['slug']}.html").write_text(build_venue_page(v, events))
        print(f"  venue/{v['slug']}.html", file=sys.stderr)
    (SITE / "about.html").write_text(build_about()); print("  about.html", file=sys.stderr)
    (SITE / "sitemap.xml").write_text(build_sitemap(events, venues))
    (SITE / "robots.txt").write_text("User-agent: *\nAllow: /\nSitemap: https://draggedout.cybr.fi/sitemap.xml\n")
    (SITE / "CNAME").write_text("draggedout.cybr.fi\n")
    print("  sitemap.xml, robots.txt, CNAME", file=sys.stderr)
    print("Done.", file=sys.stderr)

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
    if "--no-scrape" in sys.argv:
        build_no_scrape()
    elif "--serve" in sys.argv:
        serve()
    else:
        build()