#!/usr/bin/env python3
"""
Generate Classic talent templates (60PvE suffix) for the npc-talent-template module.

For each spec we care about:
  1. Fetch the spec's Wowhead Classic "talents-builds" page
  2. Find the first [talent=class/build abilities=false] in the PvE section
  3. Decode the build string -> list of talent spell IDs (using the Wowhead
     'talents-classic' data file at out/talents_classic_raw.js)
  4. Emit INSERT statements for mod_npc_talent_template_talents

Output: out/classic_pve_talents.sql

Pre-requisite: out/talents_classic_raw.js must exist (run fetch_classic_talents_data.py once).

Usage (PowerShell):
  python extract_classic_talents.py
  python extract_classic_talents.py --out custom_path.sql
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from sql_idempotency import idempotency_lines


_GUIDE_BASE = "https://www.wowhead.com/guides"
_TALENTS_DATA_URL = "https://nether.wowhead.com/classic/data/talents-classic?dv=17"

# Canonical class-tree order (left-to-right in build string) -> Wowhead tree description name
CLASS_TREES: dict[str, list[str]] = {
    # WotLK-only class (unused by the classic scraper; needed when decode_build is
    # reused for WotLK talents). Tree order matches the talent-calc URL: Blood/Frost/Unholy.
    "death knight": ["DeathKnightBlood",  "DeathKnightFrost",    "DeathKnightUnholy"],
    "druid":   ["DruidBalance",           "DruidFeralCombat",    "DruidRestoration"],
    "hunter":  ["HunterBeastMastery",     "HunterMarksmanship",  "HunterSurvival"],
    "mage":    ["MageArcane",             "MageFire",            "MageFrost"],
    "paladin": ["PaladinHoly",            "PaladinProtection",   "PaladinCombat"],
    "priest":  ["PriestDiscipline",       "PriestHoly",          "PriestShadow"],
    "rogue":   ["RogueAssassination",     "RogueCombat",         "RogueSubtlety"],
    "shaman":  ["ShamanElementalCombat",  "ShamanEnhancement",   "ShamanRestoration"],
    "warlock": ["WarlockCurses",          "WarlockSummoning",    "WarlockDestruction"],
    "warrior": ["WarriorArms",            "WarriorFury",         "WarriorProtection"],
}

# (player_class, player_spec, list-of-talents-page-slug-candidates)
# Talents-builds URL: https://www.wowhead.com/guides/{slug}-talents-builds-classic-wow
SPECS: list[tuple[str, str, list[str]]] = [
    ("Druid",   "Balance",      ["balance-druid-dps"]),
    ("Druid",   "Cat",          ["feral-druid-dps"]),
    ("Druid",   "Bear",         ["feral-druid-tank", "druid-tank"]),
    ("Druid",   "Restoration",  ["restoration-druid-healer", "druid-healing", "druid-healer"]),
    ("Hunter",  "Marksmanship", ["hunter-dps"]),
    ("Mage",    "Fire",         ["fire-mage-dps", "mage-dps"]),
    ("Paladin", "Holy",         ["holy-paladin-healer", "paladin-healing", "paladin-healer"]),
    ("Paladin", "Protection",   ["protection-paladin-tank", "paladin-tank",
                                  "paladin-tanking", "paladin-protection-tank"]),
    ("Priest",  "Holy",         ["holy-priest-healer", "priest-healing"]),
    ("Priest",  "Shadow",       ["shadow-priest-dps"]),
    ("Rogue",   "Combat",       ["combat-rogue-dps", "rogue-dps"]),
    ("Shaman",  "Elemental",    ["elemental-shaman-dps"]),
    ("Shaman",  "Enhancement",  ["enhancement-shaman-dps"]),
    ("Shaman",  "Restoration",  ["restoration-shaman-healer", "shaman-healer", "shaman-healing"]),
    ("Warlock", "Affliction",   ["affliction-warlock-dps", "warlock-dps"]),
    ("Warrior", "Fury",         ["fury-warrior-dps", "warrior-dps"]),
    ("Warrior", "Protection",   ["protection-warrior-tank", "warrior-tank"]),
]


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "ignore")


def extract_markup(html: str) -> str:
    calls = re.findall(r"WH\.markup\.printHtml\((.*?)(?:\);\s*$|\Z)", html, re.S | re.M)
    for raw in reversed(calls):
        raw = raw.strip()
        m = re.search(r'^"((?:\\.|[^"\\])+)', raw, re.S)
        if not m:
            continue
        escaped = m.group(1)
        text = escaped.replace("\\r\\n", "\n").replace("\\r", "\n").replace("\\n", "\n")
        text = re.sub(r"\\u([0-9a-fA-F]{4})", lambda x: chr(int(x.group(1), 16)), text)
        text = text.replace('\\"', '"').replace("\\/", "/")
        if len(text) > 500:
            return text
    return ""


def load_talents_data(script_dir: Path) -> dict:
    cache = script_dir / "out" / "talents_classic_raw.js"
    if not cache.exists():
        print(f"Fetching talents data to {cache} ...")
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(fetch(_TALENTS_DATA_URL), encoding="utf-8")
    raw = cache.read_text(encoding="utf-8")
    start = raw.find('",') + 2
    while raw[start] == " ":
        start += 1
    depth = 0
    i = start
    in_str = False
    while i < len(raw):
        c = raw[i]
        if in_str:
            if c == "\\":
                i += 2
                continue
            if c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    break
        i += 1
    return json.loads(raw[start:i + 1])


def find_pve_build(markup: str, player_class: str) -> str | None:
    """
    Return the raid-PvE build string from a talents-builds page.
    Strategy: find the first [talent=class/build abilities=...] after a heading
    that mentions "PvE", "Raid", or "Raiding". Fall back to the first one on
    the page if no PvE-tagged heading is found.
    """
    pattern = rf"\[talent={re.escape(player_class.lower())}/([^\s\]]+)"
    matches = list(re.finditer(pattern, markup))
    if not matches:
        return None

    # Look at the heading context preceding each match. Prefer ones near "PvE" / "Raid".
    headings = [(m.start(), m.group(0))
                for m in re.finditer(r"\[h[23][^\]]*\][^\[]*\[/h[23]\]", markup)]
    for m in matches:
        # Find nearest preceding heading
        nearest = ""
        for h_pos, h_text in headings:
            if h_pos < m.start():
                nearest = h_text
            else:
                break
        if re.search(r"\b(PvE|Raid|Raiding|Standard)\b", nearest, re.I):
            return m.group(1)
    # Fallback: first build on the page
    return matches[0].group(1)


def discover_talents_url(slug_candidates: list[str]) -> tuple[str | None, str | None]:
    """Try each slug candidate; return (url, markup) of the first that loads."""
    for slug in slug_candidates:
        url = f"{_GUIDE_BASE}/{slug}-talents-builds-classic-wow"
        try:
            html = fetch(url)
            markup = extract_markup(html)
            if markup and re.search(r"\[talent=", markup):
                return url, markup
        except (urllib.error.HTTPError, urllib.error.URLError, OSError):
            pass
        time.sleep(0.2)
    return None, None


def decode_gear_planner_talents(gp_data: str) -> str | None:
    """
    Decode the talent portion of a [gear-planner=class/race/<base64>] string.
    Mirrors the parser in GearPlanner.js (offset ~108072):
      byte[0]   = version (must be < 5)
      byte[1]   = level         (if version > 0)
      byte[2]   = N             (talent byte count, if version > 1)
      byte[3..3+N] = packed talents, each byte = 2 nibbles
                   nibble values: 0-5 = points, 0xF = tree separator
    Returns the digit-dash hash like '050501-553051330010151-03', or None.
    """
    raw = base64.urlsafe_b64decode(gp_data + "=" * (-len(gp_data) % 4))
    data = list(raw)
    if not data:
        return None
    version = data[0]
    if version >= 5 or version < 2:
        return None  # version 0/1 doesn't carry talents; >=5 is unknown
    # version > 0 → level byte; version > 1 → N + talent bytes
    idx = 1
    idx += 1  # skip level
    if idx >= len(data):
        return None
    n_bytes = data[idx]
    idx += 1
    if idx + n_bytes > len(data):
        return None
    talent_bytes = data[idx:idx + n_bytes]
    nibbles: list[int] = []
    for b in talent_bytes:
        nibbles.append((b >> 4) & 0xF)
        nibbles.append(b & 0xF)
    hash_str = ""
    sep_count = 0
    for v in nibbles:
        if sep_count >= 3:
            break
        if v == 0xF:
            hash_str += "-"
            sep_count += 1
        else:
            hash_str += str(v)
    return re.sub(r"-+$", "", hash_str)


def discover_bis_gear_planner_talents(player_class: str, player_spec: str) -> str | None:
    """
    Fallback: search cached P6/P5/P4/P2 BiS URL files for the matching
    (class, spec) entry and decode its [gear-planner=class/race/<base64>] block.
    Used when no talents-builds page exists (e.g. Paladin Protection).
    """
    script_dir = Path(__file__).parent
    cls_lower = player_class.lower()
    for phase in (6, 5, 4, 2):  # prefer later phases (more polished builds)
        url_file = script_dir / "out" / f"classic_pve_p{phase}_urls.txt"
        if not url_file.exists():
            continue
        for line in url_file.read_text(encoding="utf-8").splitlines():
            parts = line.strip().split("\t")
            if len(parts) != 3 or parts[0] != player_class or parts[1] != player_spec:
                continue
            url = parts[2]
            try:
                html = fetch(url)
                markup = extract_markup(html)
            except (urllib.error.HTTPError, urllib.error.URLError, OSError):
                continue
            m = re.search(
                rf"\[gear-planner={re.escape(cls_lower)}/[a-z-]+/([A-Za-z0-9_\-]+)\]",
                markup,
            )
            if not m:
                continue
            hash_str = decode_gear_planner_talents(m.group(1))
            if hash_str and re.search(r"\d", hash_str):
                return hash_str
            time.sleep(0.2)
    return None


def compact_to_digit_format(build: str, tabs: list[int], talents_by_tab: dict) -> str:
    """
    Decode Wowhead's compact talent build (base64-url) into the legacy
    digit-hyphen format. Mirrors function A() in TalentCalcClassic.js.

    Per-tree layout:
      byte[0] = (4 bits unused | 4 bits = byte_count for this tree)
      byte[1..byte_count] = packed 2-bit values, 4 per byte
    Per-talent decoding from the 2-bit stream:
      00 -> 0 points
      01 -> 1 point
      10 XX -> 2 + XX points (4 bits total, gives 2..5)
      11 -> max ranks for this talent
    """
    raw = base64.urlsafe_b64decode(build + "=" * (-len(build) % 4))
    data = list(raw)
    out_segments: list[str] = []
    for tab_id in tabs:
        if not data:
            out_segments.append("")
            continue
        first = data[0]
        if (first >> 4) & 0xF != 0:
            break  # version check fails — bail like the JS does
        byte_count = first & 0xF
        tree_bytes = data[1:1 + byte_count]
        data = data[1 + byte_count:]
        # Unpack 2-bit values (high pair first within each byte)
        bits: list[int] = []
        for b in tree_bytes:
            bits.append((b >> 6) & 3)
            bits.append((b >> 4) & 3)
            bits.append((b >> 2) & 3)
            bits.append((b >> 0) & 3)
        ordered = sorted(talents_by_tab[str(tab_id)].values(),
                         key=lambda t: (t["row"], t["col"]))
        chars: list[str] = []
        idx = 0
        i = 0
        while i < len(bits) and idx < len(ordered):
            v = bits[i]
            i += 1
            if v == 0:
                chars.append("0")
            elif v == 1:
                chars.append("1")
            elif v == 3:
                chars.append(str(len(ordered[idx]["ranks"])))
            elif v == 2:
                if i >= len(bits):
                    break
                chars.append(str(bits[i] + 2))
                i += 1
            idx += 1
        out_segments.append("".join(chars))
    return "-".join(out_segments)


def decode_build(player_class: str, build: str, talents_by_tab: dict,
                 desc_to_tab: dict[str, int]) -> list[int]:
    """Decode 'tree1-tree2-tree3' -> list of talent spell IDs."""
    tree_names = CLASS_TREES[player_class.lower()]
    tabs = [desc_to_tab[name] for name in tree_names]

    # If the build is in the compact (base64-url) format, expand it first.
    if build and build[0].isalpha() and build[0].isupper():
        build = compact_to_digit_format(build, tabs, talents_by_tab)

    segments = build.split("-")
    # Wowhead strips trailing empty trees ("3030-0505" instead of "3030-0505-"), so pad.
    while len(segments) < 3:
        segments.append("")
    if len(segments) > 3:
        raise ValueError(f"Got {len(segments)} tree segments in {build!r}")
    spell_ids: list[int] = []
    for tab_id, seg in zip(tabs, segments):
        ordered = sorted(talents_by_tab[str(tab_id)].values(),
                         key=lambda t: (t["row"], t["col"]))
        for idx, ch in enumerate(seg):
            points = int(ch)
            if points == 0 or idx >= len(ordered):
                continue
            ranks = ordered[idx]["ranks"]
            if points > len(ranks):
                print(f"    WARN: tab {tab_id} pos {idx} only has {len(ranks)} ranks, build wants {points}")
                continue
            spell_ids.append(ranks[points - 1])
    return spell_ids


def render_sql(entries: list[tuple[str, str, list[int]]]) -> str:
    """Build the full SQL output."""
    lines = [
        "-- Auto-generated Classic talent templates (suffix: 60PvE)",
        f"-- {len(entries)} specs",
        "",
    ]
    # Idempotency DELETE block (before the INSERT) so re-applying cannot duplicate rows.
    idem = idempotency_lines(["talents"], [f"{pspec}60PvE" for _pcls, pspec, _ids in entries])
    if idem:
        lines += idem + [""]
    lines.append("/*!40000 ALTER TABLE `mod_npc_talent_template_talents` DISABLE KEYS */;")
    rows: list[str] = []
    for pcls, pspec, ids in entries:
        for spell_id in ids:
            rows.append(f"('{pcls}', '{pspec}60PvE', {spell_id})")
    lines.append("INSERT INTO `mod_npc_talent_template_talents` "
                 "(`playerClass`, `playerSpec`, `talentId`) VALUES")
    for i, row in enumerate(rows):
        sep = "," if i < len(rows) - 1 else ";"
        lines.append(f"{row}{sep}")
    lines.append("/*!40000 ALTER TABLE `mod_npc_talent_template_talents` ENABLE KEYS */;")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Classic talent templates from Wowhead")
    parser.add_argument("--out", default=None,
                        help="Output SQL file (default: out/classic_pve_talents.sql)")
    args = parser.parse_args()

    script_dir = Path(__file__).parent
    out_path = Path(args.out) if args.out else script_dir / "out" / "classic_pve_talents.sql"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print("Loading talents data ...")
    data = load_talents_data(script_dir)
    talents_by_tab = data["talents"]
    desc_to_tab = {meta["description"]: int(tab_id)
                   for tab_id, meta in data["trees"].items()}

    entries: list[tuple[str, str, list[int]]] = []
    errors: list[str] = []

    for pcls, pspec, slugs in SPECS:
        print(f"  {pcls}/{pspec} ...")
        build: str | None = None
        source = ""
        url, markup = discover_talents_url(slugs)
        if url is not None:
            build = find_pve_build(markup, pcls)
            if build:
                source = url
            else:
                print(f"    -- no [talent=...] tag on {url}")
        else:
            print(f"    -- no talents-builds page for slugs {slugs}")

        if build is None:
            # Fallback: decode the gear-planner block on a BiS page.
            fallback = discover_bis_gear_planner_talents(pcls, pspec)
            if fallback:
                build = fallback
                source = "(gear-planner fallback)"

        if build is None:
            errors.append(f"{pcls}/{pspec}: no build found")
            continue
        try:
            ids = decode_build(pcls, build, talents_by_tab, desc_to_tab)
        except Exception as e:
            print(f"    -- decode failed: {e}")
            errors.append(f"{pcls}/{pspec}: decode {e}")
            continue
        print(f"    OK  {source}")
        print(f"        build={build}  talents={len(ids)}")
        entries.append((pcls, pspec, ids))
        time.sleep(0.3)

    if not entries:
        print("\nNo talents extracted. Exiting.")
        sys.exit(1)

    sql = render_sql(entries)
    out_path.write_text(sql, encoding="utf-8")
    print(f"\nWrote: {out_path}  ({len(entries)} specs, "
          f"{sum(len(ids) for _, _, ids in entries)} talent rows)")
    if errors:
        print(f"  {len(errors)} spec(s) had errors:")
        for e in errors:
            print(f"    {e}")


if __name__ == "__main__":
    main()
