#!/usr/bin/env python3
"""Analyze the talents data structure to figure out class-to-tab mapping."""
import json
import re

with open("out/talents_classic_raw.js", encoding="utf-8") as f:
    raw = f.read()

# Find start of JSON object (just after the first comma in setPageData)
start = raw.find('",') + 2
while raw[start] == " ":
    start += 1
assert raw[start] == "{"

# Brace counter to find the end of the JSON object — respect strings & escapes
depth = 0
i = start
in_string = False
while i < len(raw):
    c = raw[i]
    if in_string:
        if c == "\\":
            i += 2
            continue
        if c == '"':
            in_string = False
    else:
        if c == '"':
            in_string = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                break
    i += 1
data = json.loads(raw[start:i+1])

print("Top-level keys:", list(data.keys()))
print()
talents = data["talents"]
print(f"Talent tabs ({len(talents)}):")
for tab_id, tab in talents.items():
    sample = next(iter(tab.values()))
    print(f"  tab {tab_id}: {len(tab)} talents, first id={sample['id']}, icon={sample['icon']!r}")
