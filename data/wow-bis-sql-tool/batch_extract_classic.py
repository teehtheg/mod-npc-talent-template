#!/usr/bin/env python3
"""
Batch-extract Wowhead Classic BiS guides for all specs into a per-phase SQL file.

Supports 4 phases:
  P2 - Molten Core + Dire Maul
  P4 - Blackwing Lair + Zul'Gurub
  P5 - Ahn'Qiraj
  P6 - Naxxramas

Guide pages live on www.wowhead.com/classic/guide/ using the pattern:
  wow-classic-{spec-slug}-{phase-slug}-best-in-slot-gear

Each spec has multiple candidate spec-slugs (tried in order) to handle Wowhead's
inconsistent naming (e.g. "shaman-healer" for P2 vs "shaman-healing" for P4+).

Usage (PowerShell from wow-bis-sql-tool/):
  python batch_extract_classic.py --phase 6 --out ".\\out\\classic_pve_p6_bis.sql"

  Re-run without re-discovering URLs:
    python batch_extract_classic.py --phase 6 --skip-discover
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
import time
import urllib.error
import urllib.request
import re
from pathlib import Path

from sql_idempotency import idempotency_lines_from_sql


_BASE_URL = "https://www.wowhead.com/classic/guide"

PHASE_LABELS: dict[int, str] = {
    2: "Molten Core / Dire Maul",
    4: "Blackwing Lair / Zul'Gurub",
    5: "Ahn'Qiraj",
    6: "Naxxramas",
}

# Phase number → URL phase slug(s) to try (in order)
PHASE_URL_SLUGS: dict[int, list[str]] = {
    2: ["molten-core-phase-2"],
    4: ["blackwing-lair-zg"],
    5: ["ahnqiraj"],
    6: ["naxxramas"],
}

# (player_class, player_spec, spec-slug-candidates)
# Full URL probed: {_BASE_URL}/wow-classic-{spec-slug}-{phase-slug}-best-in-slot-gear
# Multiple candidates handle Wowhead's inconsistent slug naming across phases.
CLASSIC_SPECS: list[tuple[str, str, list[str]]] = [
    ("Druid",   "Balance",      ["balance-druid-dps", "druid-balance-dps", "druid-balance"]),
    ("Druid",   "Cat",          ["feral-druid-dps", "druid-feral-dps"]),
    ("Druid",   "Bear",         ["druid-tank", "feral-druid-tank", "druid-feral-tank"]),
    ("Druid",   "Restoration",  ["restoration-druid-healer", "druid-healing", "druid-healer"]),
    ("Hunter",  "Marksmanship", ["hunter-dps", "marksmanship-hunter-dps", "hunter-marksmanship-dps"]),
    ("Mage",    "Fire",         ["mage-dps", "fire-mage-dps", "mage-fire-dps"]),
    ("Paladin", "Holy",         ["paladin-healer", "paladin-healing", "holy-paladin-healer"]),
    ("Paladin", "Protection",   ["paladin-tank", "protection-paladin-tank", "paladin-protection-tank"]),
    ("Paladin", "Retribution",  ["retribution-paladin-dps", "paladin-retribution-dps", "paladin-retribution"]),
    ("Priest",  "Holy",         ["priest-healing", "holy-priest-healer", "priest-healer", "priest-holy-healer"]),
    ("Priest",  "Shadow",       ["shadow-priest-dps", "priest-shadow-dps"]),
    ("Rogue",   "Combat",       ["rogue-dps", "combat-rogue-dps", "rogue-combat-dps"]),
    ("Shaman",  "Elemental",    ["elemental-shaman-dps", "shaman-elemental-dps"]),
    ("Shaman",  "Enhancement",  ["enhancement-shaman-dps", "shaman-enhancement-dps"]),
    ("Shaman",  "Restoration",  ["shaman-healer", "shaman-healing", "restoration-shaman-healer"]),
    ("Warlock", "Affliction",   ["warlock-dps", "affliction-warlock-dps", "warlock-affliction-dps"]),
    ("Warrior", "Arms",         ["arms-warrior-dps", "warrior-arms-dps"]),
    ("Warrior", "Fury",         ["warrior-dps", "fury-warrior-dps", "warrior-fury-dps"]),
    ("Warrior", "Protection",   ["warrior-tank", "protection-warrior-tank", "warrior-protection-tank"]),
]


def fetch_html(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "ignore")


def extract_markup_text(page_html: str) -> str:
    calls = re.findall(
        r"WH\.markup\.printHtml\((.*?)(?:\);\s*$|\Z)", page_html, re.S | re.M
    )
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


def probe_url(url: str) -> bool:
    """Return True if the page has extractable Classic guide content."""
    try:
        html = fetch_html(url)
        markup = extract_markup_text(html)
        has_items = bool(re.search(r"\[item=\d+\]", markup))
        has_slots = (
            bool(re.search(r"\[h4[^\]]*\]", markup))       # [h4] style
            or bool(re.search(r'\[h3[^\]]*toc=', markup))  # [h3 toc=...] (quoted or unquoted)
            or bool(re.search(r"\[h3\]", markup))           # plain [h3] (Hunter/Rogue style)
        )
        return has_items and has_slots
    except (urllib.error.HTTPError, urllib.error.URLError, OSError):
        return False


def discover_url(spec_slugs: list[str], phase: int) -> str | None:
    """Find the first valid Wowhead Classic phase guide URL for a spec."""
    phase_slugs = PHASE_URL_SLUGS.get(phase, [f"phase-{phase}"])
    for spec_slug in spec_slugs:
        for phase_slug in phase_slugs:
            url = f"{_BASE_URL}/wow-classic-{spec_slug}-{phase_slug}-best-in-slot-gear"
            if probe_url(url):
                return url
            time.sleep(0.15)
    return None


def load_classic_module(script_dir: Path):
    mod_path = script_dir / "extract_classic_bis.py"
    spec = importlib.util.spec_from_file_location("extract_classic_bis", mod_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["extract_classic_bis"] = mod
    spec.loader.exec_module(mod)
    return mod


_STRIP_PREFIXES = (
    "-- Auto-generated",
    "-- class=",
    "SET @MINLEVEL",
    "SET @MAXLEVEL",
    "SET @RACEMASK_ALL",
)


def strip_per_spec_header(sql: str) -> str:
    lines = []
    for line in sql.splitlines():
        if any(line.strip().startswith(p) for p in _STRIP_PREFIXES):
            continue
        lines.append(line)
    while lines and not lines[0].strip():
        lines.pop(0)
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Batch-extract all Classic BiS guides to a single SQL file."
    )
    parser.add_argument(
        "--phase", type=int, default=6,
        choices=[2, 4, 5, 6],
        help="Phase: 2=MC/DM, 4=BWL/ZG, 5=AQ, 6=Naxx (default: 6)",
    )
    parser.add_argument(
        "--out", default=None,
        help="Output SQL file (default: out/classic_pve_p{phase}_bis.sql)",
    )
    parser.add_argument(
        "--url-file", default=None,
        help="File to save/load discovered URLs (default: out/classic_pve_p{phase}_urls.txt)",
    )
    parser.add_argument(
        "--skip-discover", action="store_true",
        help="Skip URL probing; load from --url-file instead",
    )
    parser.add_argument(
        "--talent-suffix", default="60PvE",
        help="Talent/glyph suffix fragment (spec name is prepended, default: 60PvE)",
    )
    args = parser.parse_args()

    script_dir = Path(__file__).parent
    out_dir = script_dir / "out"
    out_dir.mkdir(parents=True, exist_ok=True)

    slug = f"classic_pve_p{args.phase}"
    out_path = Path(args.out) if args.out else out_dir / f"{slug}_bis.sql"
    url_path = Path(args.url_file) if args.url_file else out_dir / f"{slug}_urls.txt"

    phase_label = PHASE_LABELS.get(args.phase, f"Phase {args.phase}")

    # entries: (player_class, player_spec, url)
    entries: list[tuple[str, str, str]] = []

    if args.skip_discover and url_path.exists():
        print(f"Loading URLs from {url_path}")
        for line in url_path.read_text(encoding="utf-8").splitlines():
            parts = line.strip().split("\t")
            if len(parts) == 3:
                entries.append((parts[0], parts[1], parts[2]))
        print(f"  Loaded {len(entries)} entries.")
    else:
        if args.skip_discover:
            print(f"Note: --skip-discover requested but {url_path} not found — running discovery.")
        print(f"Discovering Classic BiS URLs (Phase {args.phase}: {phase_label}) ...")
        for pcls, pspec, spec_slugs in CLASSIC_SPECS:
            url = discover_url(spec_slugs, args.phase)
            status = "OK" if url else "--"
            print(f"  {status}  {pcls}/{pspec}")
            if url:
                entries.append((pcls, pspec, url))
            time.sleep(0.3)

        url_path.write_text(
            "\n".join(f"{pc}\t{ps}\t{u}" for pc, ps, u in entries),
            encoding="utf-8",
        )
        print(f"\nSaved {len(entries)} verified URLs to {url_path}")

    if not entries:
        print("No valid URLs found. Exiting.")
        sys.exit(1)

    print(f"\nExtracting {len(entries)} specs ...")
    mod = load_classic_module(script_dir)

    suffix = f"60PvEP{args.phase}BiS"
    talent_suffix_base = args.talent_suffix

    header_lines = [
        "-- Batch-generated Classic BiS gear templates",
        f"-- Phase: {args.phase}  Mode: pve  Level: 60",
        f"-- {len(entries)} specs",
        "",
        "SET @MINLEVEL = 60;",
        "SET @MAXLEVEL = 69;",
        "SET @RACEMASK_ALL = 1791;",
        "",
    ]

    blocks: list[str] = []
    errors: list[str] = []

    for idx, (pcls, pspec, url) in enumerate(entries):
        talent_override = f"{pspec}{talent_suffix_base}"
        full_spec = f"{pspec}{suffix}"
        print(f"  [{idx+1:2d}/{len(entries)}] {pcls}/{pspec}  ->  {full_spec}")
        try:
            page_html = mod.fetch_html(url)
            markup = mod.extract_markup_text(page_html)
            slot_items = mod.extract_bis_by_slot(markup)
            if not slot_items:
                raise ValueError("No BiS items extracted — guide may have unexpected structure.")
            sql = mod.render_sql(
                player_class=pcls,
                player_spec=pspec,
                suffix=suffix,
                talent_override=talent_override,
                slot_items=slot_items,
                category=f"Classic Phase {args.phase}",
            )
            cleaned = strip_per_spec_header(sql)
            blocks.append(f"-- ===== {pcls} {full_spec} =====")
            blocks.append(cleaned)
            blocks.append("")
        except Exception as exc:
            print(f"       ERROR: {exc}")
            blocks.append(f"-- ERROR: {pcls}/{pspec}: {exc}")
            blocks.append("")
            errors.append(f"{pcls}/{pspec}: {exc}")
        time.sleep(0.25)

    # Prepend an idempotency DELETE block (before the SET constants) so re-applying
    # this file cannot duplicate rows. Derived from the emitted INSERTs.
    idem = idempotency_lines_from_sql("\n".join(blocks))
    if idem:
        for i, ln in enumerate(header_lines):
            if ln.startswith("SET "):
                header_lines[i:i] = idem + [""]
                break
    header = "\n".join(header_lines)

    out_path.write_text(header + "\n".join(blocks) + "\n", encoding="utf-8")
    print(f"\nWrote: {out_path}")
    if errors:
        print(f"  {len(errors)} spec(s) had errors:")
        for e in errors:
            print(f"    {e}")
    else:
        print("  All specs extracted successfully.")


if __name__ == "__main__":
    main()
