#!/usr/bin/env python3
"""
Batch-extract Wowhead TBC BiS guides for all specs into one combined SQL file.

URL discovery strategy:
  1. Fetch the spec's index page on www.wowhead.com/tbc/guide/classes/...
     If it still shows TBC content ([db=classic]), extract the phase-specific
     [cta-button=URL] link for the requested phase.
  2. Otherwise fall back to probing tbc.wowhead.com/guides/ with a guessed URL slug.

Usage (PowerShell from wow-bis-sql-tool/):
  python batch_extract_tbc.py --phase 3 --out ".\\out\\tbc_pve_p3_bis.sql"

  Re-run without re-discovering URLs:
    python batch_extract_tbc.py --phase 3 --skip-discover
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
import time
import urllib.error
import re
from pathlib import Path

from sql_idempotency import idempotency_lines_from_sql


# -------------------------------------------------------------------------
# Spec definitions
# (wowhead_class_slug, wowhead_spec_slug, wowhead_role, player_class, player_spec)
# player_spec must match the naming convention from npc_talent_template_data_2_70_*.sql
# -------------------------------------------------------------------------
TBC_SPECS: list[tuple[str, str, str, str, str]] = [
    ("druid",   "balance",       "dps",    "Druid",   "Balance"),
    ("druid",   "feral",         "dps",    "Druid",   "Cat"),
    ("druid",   "feral",         "tank",   "Druid",   "Bear"),
    ("druid",   "restoration",   "healer", "Druid",   "Restoration"),
    ("hunter",  "beast-mastery", "dps",    "Hunter",  "Beastmastery"),
    ("hunter",  "marksmanship",  "dps",    "Hunter",  "Marksmanship"),
    ("hunter",  "survival",      "dps",    "Hunter",  "Survival"),
    ("mage",    "arcane",        "dps",    "Mage",    "Arcane"),
    ("mage",    "fire",          "dps",    "Mage",    "Fire"),
    ("mage",    "frost",         "dps",    "Mage",    "Frost"),
    ("paladin", "holy",          "healer", "Paladin", "Holy"),
    ("paladin", "protection",    "tank",   "Paladin", "Protection"),
    ("paladin", "retribution",   "dps",    "Paladin", "Retribution"),
    ("priest",  "discipline",    "healer", "Priest",  "Discipline"),
    ("priest",  "holy",          "healer", "Priest",  "Holy"),
    ("priest",  "shadow",        "dps",    "Priest",  "Shadow"),
    ("rogue",   "assassination", "dps",    "Rogue",   "Assassination"),
    ("rogue",   "combat",        "dps",    "Rogue",   "Combat"),
    ("shaman",  "elemental",     "dps",    "Shaman",  "Elemental"),
    ("shaman",  "enhancement",   "dps",    "Shaman",  "Enhancement"),
    ("shaman",  "restoration",   "healer", "Shaman",  "Restoration"),
    ("warlock", "affliction",    "dps",    "Warlock", "Affliction"),
    ("warlock", "demonology",    "dps",    "Warlock", "Demonology"),
    ("warlock", "destruction",   "dps",    "Warlock", "Destruction"),
    ("warrior", "arms",          "dps",    "Warrior", "Arms"),
    ("warrior", "fury",          "dps",    "Warrior", "Fury"),
    ("warrior", "protection",    "tank",   "Warrior", "Protection"),
]

# Specs without TBC guides of their own, extracted from another class's guide:
# (player_class, player_spec, source player_class, source player_spec).
# Death Knights didn't exist in TBC; the Warrior guides share their plate/strength
# stat priorities. extract_tbc_bis applies the DK weapon and sigil rules.
DERIVED_SPECS: list[tuple[str, str, str, str]] = [
    ("Death Knight", "Blood",  "Warrior", "Protection"),
    ("Death Knight", "Frost",  "Warrior", "Fury"),
    ("Death Knight", "Unholy", "Warrior", "Arms"),
]

# Derived specs whose weapons come from a different source guide. Blood DKs can't
# use the Protection Warrior's shield and tanked with a two-hander, so they take
# the Arms Warrior's weapons: (player_class, player_spec) -> (class, spec).
# Slots left empty because the main source only lists items the class can't use
# (e.g. Warrior tier shoulders) are filled from this guide too.
DERIVED_WEAPON_SOURCE: dict[tuple[str, str], tuple[str, str]] = {
    ("Death Knight", "Blood"): ("Warrior", "Arms"),
}


def add_derived_entries(entries: list[tuple[str, str, str, str, str, str]]) -> None:
    """Append DERIVED_SPECS entries that reuse their source spec's guide URL."""
    by_spec = {(e[3], e[4]): e for e in entries}
    for pcls, pspec, src_cls, src_spec in DERIVED_SPECS:
        if (pcls, pspec) in by_spec:
            continue
        src = by_spec.get((src_cls, src_spec))
        if src:
            entries.append((src[0], src[1], src[2], pcls, pspec, src[5]))
        else:
            print(f"  --  {pcls}/{pspec}: no {src_cls}/{src_spec} guide to derive from")

# Phase number → substring keywords for matching cta-button URLs (index page)
PHASE_MATCH_KEYWORDS: dict[int, list[str]] = {
    0: ["pre-raid"],
    1: ["karazhan", "phase-1"],
    2: ["tk-ssc", "phase-2"],
    3: ["bt-hyjal", "phase-3"],
    4: ["za-phase-4", "zulaman", "phase-4"],
    5: ["swp-phase-5", "sunwell", "phase-5"],
}

# Phase number → full path slugs used directly in tbc.wowhead.com/guides/ URLs
PHASE_URL_SLUGS: dict[int, list[str]] = {
    0: ["pre-raid"],
    1: ["karazhan-best-in-slot-gear", "karazhan"],
    2: ["tk-ssc-phase-2", "ssc-tk-phase-2"],
    3: ["bt-hyjal-phase-3"],
    4: ["za-phase-4"],
    5: ["swp-phase-5"],
}

# Some P0/P1 guides have an extra "-classic-wow" suffix in their tbc.wowhead.com URL.
# Strategy 3 (direct probe) tries both the plain URL and this variant.
PHASE_URL_EXTRA_SUFFIXES: dict[int, list[str]] = {
    0: ["-classic-wow", ""],
    1: ["-classic-wow", ""],
}


def load_tbc_module(script_dir: Path):
    mod_path = script_dir / "extract_tbc_bis.py"
    spec = importlib.util.spec_from_file_location("extract_tbc_bis", mod_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["extract_tbc_bis"] = mod
    spec.loader.exec_module(mod)
    return mod


# Page fetching (with 403 backoff) and markup decoding are shared with the
# single-page extractor, so discovery and extraction throttle the same way.
etb = load_tbc_module(Path(__file__).parent)
fetch_html = etb.fetch_html
RateLimited = etb.RateLimited


def extract_markup_text(page_html: str) -> str:
    try:
        return etb.extract_markup_text(page_html)
    except ValueError:
        return ""


def is_tbc_guide(markup_text: str) -> bool:
    """Return True if the page still shows TBC content (not Cata/later)."""
    return "[db=classic]" in markup_text


def extract_cta_button_url(markup_text: str, phase: int) -> str | None:
    """Find the phase-specific URL from [cta-button=URL] tags."""
    keywords = PHASE_MATCH_KEYWORDS.get(phase, [f"phase-{phase}"])
    buttons = re.findall(r"\[cta-button=([^\s\]]+)", markup_text)
    for btn_url in buttons:
        for kw in keywords:
            if kw in btn_url:
                return btn_url
    return None


def probe_tbc_url(url: str) -> bool:
    """Return True if the page has TBC guide item content.

    A missing page is False; being rate-limited raises RateLimited instead, so a
    throttled probe can't silently drop a spec from the URL cache."""
    try:
        markup = extract_markup_text(fetch_html(url))
        # TBC guides have [item=] tags and [h3 toc=] slot headings
        return bool(re.search(r"\[item=\d+\]", markup) and re.search(r'\[h3 [^\]]*toc="', markup))
    except (urllib.error.HTTPError, urllib.error.URLError, OSError):
        return False


def discover_phase_url(
    class_slug: str, spec_slug: str, role: str, phase: int
) -> str | None:
    """
    Try to find the tbc.wowhead.com phase guide URL for a spec.
    Returns the URL if found, None otherwise.

    Strategy 1: spec-specific index page  /{class}/{spec}/{role}-bis-gear-pve
                contains [db=classic] and cta-button links to phase URLs.
    Strategy 2: consolidated index page   /{class}/{role}-bis-gear-pve
                used for healer/tank specs with no per-spec sub-page (e.g.
                shaman/healer, rogue/dps). Has cta-button links even without
                [db=classic], and the linked tbc.wowhead.com URL is the real guide.
    Strategy 3: direct probe on tbc.wowhead.com with various slug combinations.
    """
    # Strategy 1: spec-specific index page (works for most DPS specs).
    # Checks for [db=classic] to confirm the page still shows TBC content.
    index_url = (
        f"https://www.wowhead.com/tbc/guide/classes/{class_slug}/{spec_slug}"
        f"/{role}-bis-gear-pve"
    )
    try:
        html = fetch_html(index_url)
        markup = extract_markup_text(html)
        if is_tbc_guide(markup):
            url = extract_cta_button_url(markup, phase)
            if url:
                return url
    except (urllib.error.HTTPError, urllib.error.URLError, OSError):
        pass

    # Strategy 2: probe tbc.wowhead.com directly with spec-class-role slug.
    # Must run BEFORE the consolidated-page fallback so that specs which do
    # have their own tbc.wowhead.com URL (e.g. Hunter MM/Survival) are not
    # accidentally assigned another spec's URL from the shared index page.
    # Tries both hyphen-stripped and original spec slug (Hunter BM needs the
    # hyphen: "beast-mastery-hunter-dps-...").
    # For phases 0/1, guides may have an extra "-classic-wow" suffix.
    slugs = PHASE_URL_SLUGS.get(phase, [f"phase-{phase}"])
    extra_suffixes = PHASE_URL_EXTRA_SUFFIXES.get(phase, [""])
    spec_display = spec_slug.replace("-", "")  # "beast-mastery" → "beastmastery"
    base = "https://tbc.wowhead.com/guides"
    for slug in slugs:
        for sp in dict.fromkeys([spec_display, spec_slug]):  # dedup, order: plain first
            for extra in extra_suffixes:
                candidate = (
                    f"{base}/{sp}-{class_slug}-{role}-{slug}"
                    f"-best-in-slot-gear-burning-crusade{extra}"
                )
                if probe_tbc_url(candidate):
                    return candidate
                time.sleep(1.0)

    # Strategy 3: consolidated index page without spec subfolder.
    # Used for healer/tank specs where Wowhead merged all specs into one guide
    # (e.g. shaman/healer, druid/healer, priest/healer, paladin/tank, rogue/dps).
    # These pages don't have [db=classic] but their cta-button links point to
    # valid tbc.wowhead.com phase-specific guides.
    alt_index_url = (
        f"https://www.wowhead.com/tbc/guide/classes/{class_slug}/{role}-bis-gear-pve"
    )
    if alt_index_url != index_url:
        try:
            html = fetch_html(alt_index_url)
            markup = extract_markup_text(html)
            url = extract_cta_button_url(markup, phase)
            if url and probe_tbc_url(url):
                return url
        except (urllib.error.HTTPError, urllib.error.URLError, OSError):
            pass

    return None



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


# Slots every TBC template should fill (inv_slot -> name), for the MISSING report.
_EXPECTED_SLOTS = {
    1: "head", 2: "neck", 3: "shoulder", 5: "chest", 6: "waist", 7: "legs",
    8: "feet", 9: "wrist", 10: "hands", 11: "ring1", 12: "ring2",
    13: "trinket1", 14: "trinket2", 15: "back", 16: "main hand", 17: "off hand",
    18: "ranged/relic",
}


def _is_two_hand(item_id: int) -> bool:
    info = etb.item_info.get(item_id)
    return bool(info and info.is_two_hand)


def tbc_category(phase: int) -> str:
    """Gossip sub-menu label for a phase (phase 0 is the pre-raid list)."""
    return "TBC Pre-Raid" if phase == 0 else f"TBC Phase {phase}"


def role_suffix(role: str, phase: int, player_spec: str) -> str:
    """Build the playerSpec suffix, e.g. '70PvEP3BiS'."""
    _ = role  # TBC gear is same suffix regardless of role (role is in spec name)
    _ = player_spec
    return f"70PvEP{phase}BiS"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Batch-extract all TBC BiS guides to a single SQL file."
    )
    parser.add_argument(
        "--phase", type=int, default=3,
        help="Phase number: 0=pre-raid, 1=Kara, 2=SSC/TK, 3=BT/Hyjal, 4=ZA, 5=SWP (default: 3)",
    )
    parser.add_argument(
        "--out", default=None,
        help="Output SQL file (default: out/tbc_pve_p{phase}_bis.sql)",
    )
    parser.add_argument(
        "--url-file", default=None,
        help="File to save/load discovered URLs (default: out/tbc_pve_p{phase}_urls.txt)",
    )
    parser.add_argument(
        "--skip-discover", action="store_true",
        help="Skip URL probing; load from --url-file instead",
    )
    parser.add_argument(
        "--talent-suffix", default="70PvE",
        help="Talent/glyph suffix fragment (spec name is prepended, default: 70PvE)",
    )
    args = parser.parse_args()

    script_dir = Path(__file__).parent
    out_dir = script_dir / "out"
    out_dir.mkdir(parents=True, exist_ok=True)

    slug = f"tbc_pve_p{args.phase}"
    out_path = Path(args.out) if args.out else out_dir / f"{slug}_bis.sql"
    url_path = Path(args.url_file) if args.url_file else out_dir / f"{slug}_urls.txt"

    # entries: (class_slug, spec_slug, role, player_class, player_spec, guide_url)
    entries: list[tuple[str, str, str, str, str, str]] = []

    if args.skip_discover and url_path.exists():
        print(f"Loading URLs from {url_path}")
        for line in url_path.read_text(encoding="utf-8").splitlines():
            parts = line.strip().split("\t")
            if len(parts) == 6:
                entries.append((parts[0], parts[1], parts[2], parts[3], parts[4], parts[5]))
        print(f"  Loaded {len(entries)} entries.")
    else:
        if args.skip_discover:
            print(f"Note: --skip-discover requested but {url_path} not found — running discovery.")
        phase_label = {
            0: "pre-raid", 1: "Phase 1 (Kara)", 2: "Phase 2 (SSC/TK)",
            3: "Phase 3 (BT/Hyjal)", 4: "Phase 4 (ZA)", 5: "Phase 5 (SWP)",
        }.get(args.phase, f"Phase {args.phase}")
        print(f"Discovering TBC BiS URLs ({phase_label}) ...")
        for cls, spec, role, pcls, pspec in TBC_SPECS:
            try:
                url = discover_phase_url(cls, spec, role, args.phase)
            except RateLimited as exc:
                print(f"\nRate-limited by Wowhead during discovery ({exc}).")
                print("Nothing written; wait a few minutes and re-run, or use build_url_caches.py.")
                sys.exit(2)
            status = "OK" if url else "--"
            print(f"  {status}  {pcls}/{pspec} ({role})")
            if url:
                entries.append((cls, spec, role, pcls, pspec, url))
            time.sleep(1.0)

        url_path.write_text(
            "\n".join(f"{c}\t{s}\t{r}\t{pc}\t{ps}\t{u}" for c, s, r, pc, ps, u in entries),
            encoding="utf-8",
        )
        print(f"\nSaved {len(entries)} verified URLs to {url_path}")

    add_derived_entries(entries)

    if not entries:
        print("No valid URLs found. Exiting.")
        sys.exit(1)

    print(f"\nExtracting {len(entries)} specs ...")
    mod = etb

    header_lines = [
        "-- Batch-generated TBC BiS gear templates",
        f"-- Phase: {args.phase}  Mode: pve",
        f"-- {len(entries)} specs",
        "",
        "SET @MINLEVEL = 70;",
        "SET @MAXLEVEL = 79;",
        "SET @RACEMASK_ALL = 1791;",
        "",
    ]

    blocks: list[str] = []
    errors: list[str] = []

    markup_cache: dict[str, str] = {}
    for idx, (cls, spec, role, pcls, pspec, url) in enumerate(entries):
        suffix = role_suffix(role, args.phase, pspec)
        override = f"{pspec}{args.talent_suffix}"
        print(f"  [{idx+1:2d}/{len(entries)}] {pcls}/{pspec}  ->  {pspec}{suffix}")
        try:
            if url not in markup_cache:  # derived specs reuse their source's page
                markup_cache[url] = mod.extract_markup_text(mod.fetch_html(url))
            markup = markup_cache[url]
            warnings: list[str] = []
            slot_items = mod.extract_bis_by_slot(markup, pcls, pspec, warnings, args.phase)
            weapon_src = DERIVED_WEAPON_SOURCE.get((pcls, pspec))
            if weapon_src:
                src_url = next((e[5] for e in entries if (e[3], e[4]) == weapon_src), None)
                if src_url:
                    if src_url not in markup_cache:
                        markup_cache[src_url] = mod.extract_markup_text(mod.fetch_html(src_url))
                    secondary = mod.extract_bis_by_slot(markup_cache[src_url], pcls, pspec, phase=args.phase)
                    for slot in (mod.MAIN_HAND, mod.OFF_HAND):
                        slot_items.pop(slot, None)
                        if slot in secondary:
                            slot_items[slot] = secondary[slot]
                    for slot, item in secondary.items():
                        slot_items.setdefault(slot, item)
            for w in warnings:
                print(f"       WARN {w}")
            missing = [
                name for slot, name in _EXPECTED_SLOTS.items()
                if slot not in slot_items
                and not (slot == 17 and 16 in slot_items and _is_two_hand(slot_items[16]))
            ]
            if missing:
                print(f"       MISSING {', '.join(missing)}")
            if not slot_items:
                raise ValueError("No BiS items extracted — guide may have unexpected structure.")
            sql = mod.render_sql(
                player_class=pcls,
                player_spec=pspec,
                suffix=suffix,
                talent_override=mod.talent_template(pcls, pspec, slot_items, override),
                slot_items=slot_items,
                category=tbc_category(args.phase),
                category_order=200 + args.phase,  # menu order: Classic 1xx, TBC 2xx, WotLK 3xx
                glyph_override=override,
            )
            cleaned = strip_per_spec_header(sql)
            blocks.append(f"-- ===== {pcls} {pspec}{suffix} =====")
            blocks.append(cleaned)
            blocks.append("")
        except Exception as exc:
            print(f"       ERROR: {exc}")
            blocks.append(f"-- ERROR: {pcls}/{pspec}: {exc}")
            blocks.append("")
            errors.append(f"{pcls}/{pspec}: {exc}")
        time.sleep(1.0)

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