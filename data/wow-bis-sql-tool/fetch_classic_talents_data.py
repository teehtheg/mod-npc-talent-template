#!/usr/bin/env python3
"""Download Wowhead's Classic talent data file to out/talents_classic_raw.js.

Run once to seed the cache that extract_classic_talents.py reads. The URL carries
a Wowhead version stamp (dv/db params) that may need refreshing over time.
"""
import urllib.request

URL = "https://nether.wowhead.com/classic/data/talents-classic?dv=17&db=1761625051"

req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
with urllib.request.urlopen(req, timeout=30) as r:
    data = r.read().decode("utf-8", "ignore")

print(f"Size: {len(data):,}")
# Save full file for inspection
with open("out/talents_classic_raw.js", "w", encoding="utf-8") as f:
    f.write(data)
print("Saved to out/talents_classic_raw.js")
print()
print("--- First 3000 chars ---")
print(data[:3000])
