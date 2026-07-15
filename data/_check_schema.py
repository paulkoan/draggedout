#!/usr/bin/env python3
import json

with open("data/instagram-fox-castle.json") as f:
    events = json.load(f)

print(f"{len(events)} events (music only)")
for ev in events:
    print(f'  {ev["date"]} | {ev["start"]:>5s} | {ev["artist"][:50]:50s} | {ev["venue"]}')

required = ["date", "day_name", "start", "end", "artist", "venue", "venue_slug", "cost", "source", "url", "cancelled", "repeating"]
print()
print("Schema check:")
for ev in events:
    missing = [k for k in required if k not in ev]
    extras = [k for k in ev if k not in required + ["_music_focus", "_img_url"]]
    if missing:
        print(f"  FAIL Missing fields: {missing}")
    elif extras:
        print(f"  WARN Extra fields: {extras}")
    else:
        print(f"  PASS All required fields present")
        break