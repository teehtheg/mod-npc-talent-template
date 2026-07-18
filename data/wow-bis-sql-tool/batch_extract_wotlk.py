#!/usr/bin/env python3
"""
Batch-discover and extract Wowhead BiS guides for a given
expansion + phase + mode into a single combined SQL file.

Usage (PowerShell from wow-bis-sql-tool/):
  python batch_extract.py --expansion wotlk --phase 4 --mode pve
    --base-sql "..\\sql\\db-characters\\base\\npc_talent_template_data_1_80_pvp_s6.sql"
    --out ".\\out\\wotlk_pve_p4_bis.sql"

  Re-run extraction without re-probing URLs:
    python batch_extract.py ... --skip-discover
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from sql_idempotency import idempotency_lines_from_sql


# All WotLK specs with their BiS-relevant roles.
# Role values match the Wowhead URL slug prefix (dps / healer / tank).
# Verified against https://www.wowhead.com/wotlk/guides/classes (2025-05).
WOTLK_SPECS: list[tuple[str, str, str]] = [
    ("death-knight", "blood",         "tank"),
    ("death-knight", "frost",         "dps"),
    ("death-knight", "unholy",        "dps"),
    ("druid",        "balance",       "dps"),
    ("druid",        "feral",         "dps"),
    ("druid",        "feral",         "tank"),
    ("druid",        "restoration",   "healer"),
    ("hunter",       "beast-mastery", "dps"),
    ("hunter",       "marksmanship",  "dps"),
    ("hunter",       "survival",      "dps"),
    ("mage",         "arcane",        "dps"),
    ("mage",         "fire",          "dps"),
    ("mage",         "frost",         "dps"),
    ("paladin",      "holy",          "healer"),
    ("paladin",      "protection",    "tank"),
    ("paladin",      "retribution",   "dps"),
    ("priest",       "discipline",    "healer"),
    ("priest",       "holy",          "healer"),
    ("priest",       "shadow",        "dps"),
    ("rogue",        "assassination", "dps"),
    ("rogue",        "combat",        "dps"),
    ("rogue",        "subtlety",      "dps"),
    ("shaman",       "elemental",     "dps"),
    ("shaman",       "enhancement",   "dps"),
    ("shaman",       "restoration",   "healer"),
    ("warlock",      "affliction",    "dps"),
    ("warlock",      "demonology",    "dps"),
    ("warlock",      "destruction",   "dps"),
    ("warrior",      "arms",          "dps"),
    ("warrior",      "fury",          "dps"),
    ("warrior",      "protection",    "tank"),
]

EXPANSION_BASE_URL = {
    "wotlk": "https://www.wowhead.com/wotlk/guide/classes",
}

EXPANSION_MAX_LEVEL = {
    "wotlk": 80,
}


def build_url(expansion: str, cls: str, spec: str, role: str, phase: int, mode: str) -> str:
    base = EXPANSION_BASE_URL[expansion]
    return f"{base}/{cls}/{spec}/{role}-bis-gear-{mode}-phase-{phase}"


def probe_url(url: str) -> bool:
    """Fetch the full page and return True if a gear-planner payload is present."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            html = resp.read().decode("utf-8", "ignore")
        return "[gear-planner=" in html
    except (urllib.error.HTTPError, urllib.error.URLError, OSError):
        return False


def role_suffix(role: str, mode: str, phase: int, max_level: int) -> str:
    """
    Build the playerSpec suffix stored in the SQL.
    Examples: 80PvEP4BiS, 80PvEP4BiSTank, 80PvEP4BiSHeal
    """
    role_tag = {"dps": "", "tank": "Tank", "healer": "Heal"}[role]
    if mode == "pve":
        return f"{max_level}PvEP{phase}BiS{role_tag}"
    else:
        return f"{max_level}PvPS{phase}{role_tag}"


def load_extract_module(script_dir: Path):
    mod_path = script_dir / "extract_wotlk_bis.py"
    spec = importlib.util.spec_from_file_location("extract_wotlk_bis", mod_path)
    mod = importlib.util.module_from_spec(spec)
    # Must be registered before exec_module so @dataclass can resolve cls.__module__.
    sys.modules["extract_wotlk_bis"] = mod
    spec.loader.exec_module(mod)
    return mod


_STRIP_PREFIXES = (
    "-- Auto-generated",
    "-- class=",
    "SET @MINLEVEL",
    "SET @MAXLEVEL",
    "SET @RACEMASK",
)


def strip_per_spec_header(sql: str) -> str:
    """
    Remove the duplicate header lines from each per-spec SQL block.
    We keep SET @ACTION (needed per-spec to auto-increment gossipAction) but
    drop the MINLEVEL / MAXLEVEL / RACEMASK vars (set once in the combined header).
    """
    lines = []
    for line in sql.splitlines():
        if any(line.strip().startswith(p) for p in _STRIP_PREFIXES):
            continue
        lines.append(line)
    # Drop leading blank lines
    while lines and not lines[0].strip():
        lines.pop(0)
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Batch-extract all BiS guides to a single SQL file."
    )
    parser.add_argument(
        "--expansion", default="wotlk", choices=list(EXPANSION_BASE_URL),
        help="Expansion slug (default: wotlk)",
    )
    parser.add_argument(
        "--phase", type=int, default=4,
        help="Phase number (default: 4)",
    )
    parser.add_argument(
        "--mode", default="pve", choices=["pve", "pvp"],
        help="Mode (default: pve)",
    )
    parser.add_argument(
        "--out", default=None,
        help="Output SQL file (default: out/{expansion}_{mode}_p{phase}_bis.sql)",
    )
    parser.add_argument(
        "--url-file", default=None,
        help="File to save/load discovered URLs (default: out/{expansion}_{mode}_p{phase}_urls.txt)",
    )
    parser.add_argument(
        "--skip-discover", action="store_true",
        help="Skip URL probing; load from --url-file instead (file must exist)",
    )
    parser.add_argument(
        "--talent-suffix", default="80PvE",
        help="talentOverride suffix (default: 80PvE — generated by extract_wotlk_talents.py)",
    )
    parser.add_argument(
        "--glyph-suffix", default="80PvP",
        help="glyphOverride suffix (default: 80PvP — reused from the S6 base SQL; "
             "PvE glyphs need GlyphProperties IDs that aren't generated yet)",
    )
    args = parser.parse_args()

    script_dir = Path(__file__).parent
    out_dir = script_dir / "out"
    out_dir.mkdir(parents=True, exist_ok=True)

    slug = f"{args.expansion}_{args.mode}_p{args.phase}"
    out_path = Path(args.out) if args.out else out_dir / f"{slug}_bis.sql"
    url_path = Path(args.url_file) if args.url_file else out_dir / f"{slug}_urls.txt"
    max_level = EXPANSION_MAX_LEVEL[args.expansion]

    # --- Step 1: discover or load valid URLs ---
    entries: list[tuple[str, str, str, str]] = []  # (cls, spec, role, url)

    if args.skip_discover and url_path.exists():
        print(f"Loading URLs from {url_path}")
        for line in url_path.read_text(encoding="utf-8").splitlines():
            parts = line.strip().split("\t")
            if len(parts) == 4:
                entries.append((parts[0], parts[1], parts[2], parts[3]))
        print(f"  Loaded {len(entries)} entries.")
    else:
        if args.skip_discover:
            print(f"Note: --skip-discover requested but {url_path} not found — running discovery.")
        print(f"Discovering BiS URLs ({args.expansion} {args.mode} phase={args.phase}) ...")
        for cls, spec, role in WOTLK_SPECS:
            url = build_url(args.expansion, cls, spec, role, args.phase, args.mode)
            ok = probe_url(url)
            status = "OK" if ok else "--"
            print(f"  {status}  {cls}/{spec}/{role}")
            if ok:
                entries.append((cls, spec, role, url))
            time.sleep(0.3)

        url_path.write_text(
            "\n".join(f"{c}\t{s}\t{r}\t{u}" for c, s, r, u in entries),
            encoding="utf-8",
        )
        print(f"\nSaved {len(entries)} verified URLs to {url_path}")

    if not entries:
        print("No valid URLs found. Exiting.")
        sys.exit(1)

    # --- Step 2: extract and combine SQL ---
    print(f"\nExtracting {len(entries)} specs ...")
    mod = load_extract_module(script_dir)

    header_lines = [
        f"-- Batch-generated BiS gear templates",
        f"-- Expansion: {args.expansion}  Mode: {args.mode}  Phase: {args.phase}",
        f"-- {len(entries)} specs",
        "",
        "SET @MINLEVEL = 80;",
        f"SET @MAXLEVEL = {max_level};",
        "SET @RACEMASK_HUMAN = 1;",
        "SET @RACEMASK_A = 1100; -- Alliance without Human",
        "SET @RACEMASK_H = 690;  -- Horde",
        "",
    ]

    blocks: list[str] = []
    errors: list[str] = []

    for idx, (cls, spec, role, url) in enumerate(entries):
        suffix = role_suffix(role, args.mode, args.phase, max_level)
        print(f"  [{idx+1:2d}/{len(entries)}] {cls}/{spec}/{role}  ->  {suffix}")
        try:
            page_html = mod.fetch_html(url)
            markup = mod.extract_print_html_payload(page_html)
            planners = mod.extract_planner_hashes_from_markup(markup)
            player_class, spec_base = mod.parse_class_and_spec(url)
            sql = mod.render_sql(
                player_class=player_class,
                spec_base=spec_base,
                planners=planners,
                suffix=suffix,
                talent_override_suffix=args.talent_suffix,
                glyph_override_suffix=args.glyph_suffix,
                category=f"WotLK Phase {args.phase}",
            )
            cleaned = strip_per_spec_header(sql)
            blocks.append(f"-- ===== {player_class} {spec_base} {suffix} ({role}) =====")
            blocks.append(cleaned)
            blocks.append("")
        except Exception as exc:
            print(f"       ERROR: {exc}")
            blocks.append(f"-- ERROR: {cls}/{spec}/{role}: {exc}")
            blocks.append("")
            errors.append(f"{cls}/{spec}/{role}: {exc}")
        time.sleep(0.2)

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