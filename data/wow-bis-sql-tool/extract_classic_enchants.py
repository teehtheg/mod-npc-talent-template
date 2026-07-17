#!/usr/bin/env python3
"""
Scrape Wowhead Classic per-spec enchants guide pages and dump the first
[spell=N] / [item=N] reference inside each enchant slot section.

Output is a human-readable Python dict to be reviewed and curated into
extract_classic_bis.py's CLASSIC_ENCHANTS_BY_ROLE dict.

Wowhead's enchants pages use section headers like:
  [h3 toc=...]Head & Leg Enchants for Boomkins[/h3]
  [h4]Shoulder Enchants for Boomkins[/h4]

The slot tokens before "Enchants for" map to inventory positions (pos column
in mod_npc_talent_template_gear). Combined headers like "Head & Leg" populate
multiple positions from the same paragraph block.

IMPORTANT — translation step still required:
  Wowhead references are usually [spell=N] where N is a Spell.dbc ID
  (e.g., 22749 = "Enchant Weapon - Crusader"). The gear template's `enchant`
  column wants a SpellItemEnchantment.dbc ID (e.g., 2673). Map at curation time:
    SELECT Effect1AffectsItems_misc FROM spell_dbc WHERE ID = N;
  Or use known lookup tables. [item=N] references usually mean an enchant
  delivered via a consumable item — the item's onUse spell casts the enchant.

Usage:
  python extract_classic_enchants.py                # scrape all 17 specs
  python extract_classic_enchants.py --out path.py  # custom output path
"""
from __future__ import annotations

import argparse
import re
import sys
import time
import urllib.request
from pathlib import Path


# (player_class, spec) -> enchants guide URL.
# Discovered via [nav-item=...]Enchants[/nav-item] in BiS pages for 15 specs;
# the remaining 2 (Druid Cat, Paladin Protection) follow the same Wowhead slug
# pattern as their sibling specs and are confirmed reachable.
ENCHANT_URLS: dict[tuple[str, str], str] = {
    ("Druid",   "Balance"):       "https://www.wowhead.com/guides/balance-druid-dps-enchants-classic-wow",
    ("Druid",   "Cat"):           "https://www.wowhead.com/guides/feral-druid-dps-enchants-classic-wow",
    ("Druid",   "Bear"):          "https://www.wowhead.com/guides/feral-druid-tank-enchants-classic-wow",
    ("Druid",   "Restoration"):   "https://www.wowhead.com/guides/druid-healing-enchants-classic-wow",
    ("Hunter",  "Marksmanship"):  "https://www.wowhead.com/guides/hunter-dps-enchants-classic-wow",
    ("Mage",    "Fire"):          "https://www.wowhead.com/guides/mage-dps-enchants-classic-wow",
    ("Paladin", "Holy"):          "https://www.wowhead.com/guides/paladin-healing-enchants-classic-wow",
    # Paladin Protection has no dedicated Classic enchants page on Wowhead
    # (the slug redirects to a modern Midnight-expansion guide). Fall back to
    # the Holy Paladin page — both are plate, the universal slot enchants
    # (Greater Stats chest, Greater Resistance back, Minor Speed boots) are
    # identical; tank-specific overrides need to be hand-set on top of the
    # scrape output (Defense bracer, Threat gloves, Shield Block weapon, etc.)
    ("Paladin", "Protection"):    "https://www.wowhead.com/guides/paladin-healing-enchants-classic-wow",
    ("Priest",  "Holy"):          "https://www.wowhead.com/guides/priest-healing-enchants-classic-wow",
    ("Priest",  "Shadow"):        "https://www.wowhead.com/guides/shadow-priest-dps-enchants-classic-wow",
    ("Rogue",   "Combat"):        "https://www.wowhead.com/guides/rogue-dps-enchants-classic-wow",
    ("Shaman",  "Elemental"):     "https://www.wowhead.com/guides/elemental-shaman-dps-enchants-classic-wow",
    ("Shaman",  "Enhancement"):   "https://www.wowhead.com/guides/enhancement-shaman-dps-enchants-classic-wow",
    ("Shaman",  "Restoration"):   "https://www.wowhead.com/guides/shaman-healing-enchants-classic-wow",
    ("Warlock", "Affliction"):    "https://www.wowhead.com/guides/warlock-dps-enchants-classic-wow",
    ("Warrior", "Fury"):          "https://www.wowhead.com/guides/fury-warrior-dps-enchants-classic-wow",
    ("Warrior", "Protection"):    "https://www.wowhead.com/guides/warrior-tank-enchants-classic-wow",
}


# Header-token (lowercased) → list of inv_slot pos values
# (same `pos` column as mod_npc_talent_template_gear)
SLOT_HEADER_TO_POS: dict[str, list[int]] = {
    "head":     [0],
    "helm":     [0],
    "neck":     [1],   # rarely enchanted in Classic; included for completeness
    "shoulder": [2],
    "shoulders":[2],
    "chest":    [4],
    "waist":    [5],
    "belt":     [5],
    "leg":      [6],
    "legs":     [6],
    "boot":     [7],
    "boots":    [7],
    "feet":     [7],
    "bracer":   [8],
    "bracers":  [8],
    "wrist":    [8],
    "wrists":   [8],
    "glove":    [9],
    "gloves":   [9],
    "hand":     [9],
    "hands":    [9],
    "cloak":    [14],
    "back":     [14],
    "weapon":   [15],
    "weapons":  [15],
    "main hand":[15],
    "main-hand":[15],
    "mainhand": [15],
    "off hand": [16],
    "off-hand": [16],
    "offhand":  [16],
    "ranged":   [17],
    "wand":     [17],
}


def fetch_html(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=45) as resp:
        return resp.read().decode("utf-8", "ignore")


def extract_markup_text(page_html: str) -> str:
    """Decode the WH.markup.printHtml payload from a Wowhead guide page."""
    calls = re.findall(
        r"WH\.markup\.printHtml\((.*?)(?:\);\s*$|\Z)", page_html, re.S | re.M
    )
    if not calls:
        raise ValueError("No WH.markup.printHtml call found.")

    for raw in reversed(calls):
        raw = raw.strip()
        m = re.search(r'^"((?:\\.|[^"\\])+)', raw, re.S)
        if not m:
            continue
        escaped = m.group(1)
        text = (
            escaped
            .replace("\\r\\n", "\n").replace("\\r", "\n").replace("\\n", "\n")
        )
        text = re.sub(r"\\u([0-9a-fA-F]{4})", lambda x: chr(int(x.group(1), 16)), text)
        text = text.replace('\\"', '"').replace("\\/", "/")
        if len(text) > 500:
            return text

    raise ValueError("Could not extract guide markup text.")


def parse_slot_tokens(header_raw: str) -> list[int]:
    """Extract slot positions from a header like 'Head & Leg Enchants for Boomkins'."""
    # Strip markup, then strip 'Enchants for X' suffix.
    clean = re.sub(r"\[[^\]]*\]", "", header_raw).strip()
    # Cut at 'Enchant' (handles 'Enchant', 'Enchants', 'Enchanting')
    clean = re.split(r"\s+Enchant", clean, maxsplit=1)[0].strip()
    # Split on '&', 'and', '/', ','
    parts = re.split(r"\s*(?:&|and|/|,)\s*", clean, flags=re.I)
    positions: list[int] = []
    for part in parts:
        key = part.strip().lower()
        # Strip trailing 's' if exact form not in map
        if key in SLOT_HEADER_TO_POS:
            positions.extend(SLOT_HEADER_TO_POS[key])
        elif key.rstrip("s") in SLOT_HEADER_TO_POS:
            positions.extend(SLOT_HEADER_TO_POS[key.rstrip("s")])
    # Dedupe preserving order
    return list(dict.fromkeys(positions))


def first_reference(body: str) -> tuple[str, int] | None:
    """Return ('spell'|'item', id) for the first [spell=N] or [item=N] in body."""
    m = re.search(r"\[(spell|item)=(\d+)", body)
    if not m:
        return None
    return (m.group(1), int(m.group(2)))


def parse_enchants_page(markup: str) -> dict[int, list[tuple[str, int]]]:
    """
    Return {pos: [(kind, id), ...]} — references found per slot, in document order.

    Captures the first ~6 [spell=N]/[item=N] tokens after each section header,
    so the curator can see the recommended option plus a few alternatives.
    """
    result: dict[int, list[tuple[str, int]]] = {}

    # Split on h3/h4/h5 (toc= or plain) to get (header, body) pairs.
    # Cat/Bear Druid enchants pages use h5; most other specs use h3/h4.
    # We find each h*-opening tag, then read until the next one or end.
    parts = list(re.finditer(r"\[(h[345])(?:\s+[^\]]*)?\]", markup))
    for i, m in enumerate(parts):
        body_start = m.end()
        body_end = parts[i + 1].start() if i + 1 < len(parts) else len(markup)
        body = markup[body_start:body_end]

        # Extract the header text — between the opening tag and [/hN]
        close = re.search(rf"\[/{m.group(1)}\]", body)
        if not close:
            continue
        header_raw = body[:close.start()]
        section_body = body[close.end():]

        positions = parse_slot_tokens(header_raw)
        if not positions:
            continue

        # Find up to 6 references in the section body
        refs = re.findall(r"\[(spell|item)=(\d+)", section_body)[:6]
        if not refs:
            continue
        ref_tuples = [(kind, int(rid)) for kind, rid in refs]
        for pos in positions:
            result.setdefault(pos, []).extend(ref_tuples)

    # Dedupe per-pos preserving order
    for pos, refs in result.items():
        seen: set[tuple[str, int]] = set()
        deduped: list[tuple[str, int]] = []
        for r in refs:
            if r not in seen:
                seen.add(r)
                deduped.append(r)
        result[pos] = deduped[:6]

    return result


def render_dump(per_spec: dict[tuple[str, str], dict[int, list[tuple[str, int]]]]) -> str:
    """Render the scraped data as a reviewable Python dict source."""
    POS_NAMES = {0: "Head", 1: "Neck", 2: "Shoulder", 4: "Chest", 5: "Waist",
                 6: "Legs", 7: "Feet", 8: "Wrist", 9: "Hands", 14: "Back",
                 15: "MainHand", 16: "OffHand", 17: "Ranged"}

    lines: list[str] = []
    lines.append("# Auto-extracted Classic L60 enchant candidates from Wowhead guides.")
    lines.append("# Each entry is (kind, id) where kind is 'spell' or 'item'.")
    lines.append("# spell IDs from [spell=N] are Spell.dbc — need translation to")
    lines.append("# SpellItemEnchantment.dbc IDs before use in the gear template.")
    lines.append("# First entry per slot is Wowhead's recommended pick.")
    lines.append("")
    lines.append("CLASSIC_ENCHANT_CANDIDATES = {")
    for (cls, spec) in sorted(per_spec.keys()):
        per_pos = per_spec[(cls, spec)]
        lines.append(f"    ({cls!r}, {spec!r}): {{")
        for pos in sorted(per_pos.keys()):
            refs = per_pos[pos]
            ref_str = ", ".join(f"({k!r}, {v})" for k, v in refs)
            slot_name = POS_NAMES.get(pos, f"slot{pos}")
            lines.append(f"        {pos}: [{ref_str}],  # {slot_name}")
        lines.append("    },")
    lines.append("}")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract Wowhead Classic enchant recommendations per spec.",
    )
    parser.add_argument(
        "--out", default=None,
        help="Output Python dict file (default: out/classic_enchant_candidates.py)",
    )
    parser.add_argument(
        "--spec", default=None,
        help="Scrape only this spec (e.g. 'Balance'). Default: all specs.",
    )
    args = parser.parse_args()

    out_path = (
        Path(args.out) if args.out
        else Path(__file__).parent / "out" / "classic_enchant_candidates.py"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    targets = [
        (cls, spec, url) for (cls, spec), url in ENCHANT_URLS.items()
        if not args.spec or spec == args.spec
    ]
    print(f"Scraping {len(targets)} enchant guide(s)...")

    results: dict[tuple[str, str], dict[int, list[tuple[str, int]]]] = {}
    errors: list[str] = []
    for idx, (cls, spec, url) in enumerate(targets):
        print(f"  [{idx+1:2d}/{len(targets)}] {cls}/{spec}")
        try:
            html = fetch_html(url)
            markup = extract_markup_text(html)
            per_pos = parse_enchants_page(markup)
            if not per_pos:
                raise ValueError("No enchant sections matched — page layout may have changed.")
            results[(cls, spec)] = per_pos
        except Exception as exc:
            print(f"        ERROR: {exc}", file=sys.stderr)
            errors.append(f"{cls}/{spec}: {exc}")
        time.sleep(0.3)

    out_path.write_text(render_dump(results), encoding="utf-8")
    print(f"\nWrote {out_path}")
    if errors:
        print(f"\n{len(errors)} error(s):")
        for e in errors:
            print(f"  {e}")


if __name__ == "__main__":
    main()