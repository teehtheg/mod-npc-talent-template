#!/usr/bin/env python3
"""
Derive Classic 60PvE talent templates by reusing TBC-70 PvE talent data.

The Wowhead Classic talent calculator data is actually Season of Discovery
flavored and uses Classic-era 51-point trees. AzerothCore runs WotLK 3.3.5a
where (1) talent trees are 71-point and reshuffled, (2) some Classic talent
spell IDs no longer exist.

WotLK kept every TBC talent in its original tree position (it only ADDED
talents at the bottom of each tree). So a TBC-70 41-point build is a valid
subset of a level-60 WotLK build — 9 points short of the 50 a level-60
character normally has, but using talent spell IDs that all resolve on AC.

This script reads the existing TBC PvE-70 SQL and emits a copy with the
spec suffix changed from `70PvE` to `60PvE`, filtered to the 17 specs that
appear in our Classic BiS gear sets.

Usage (PowerShell from wow-bis-sql-tool/):
  python derive_classic_talents_from_tbc.py
  python derive_classic_talents_from_tbc.py --out custom_path.sql
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path


# (player_class, tbc_spec_with_70PvE) -> new_spec_with_60PvE
SPEC_MAP: dict[tuple[str, str], str] = {
    ("Druid",   "Balance70PvE"):      "Balance60PvE",
    ("Druid",   "Bear70PvE"):         "Bear60PvE",
    ("Druid",   "Cat70PvE"):          "Cat60PvE",
    ("Druid",   "Restoration70PvE"):  "Restoration60PvE",
    ("Hunter",  "Marksmanship70PvE"): "Marksmanship60PvE",
    ("Mage",    "Fire70PvE"):         "Fire60PvE",
    ("Paladin", "Holy70PvE"):         "Holy60PvE",
    ("Paladin", "Protection70PvE"):   "Protection60PvE",
    ("Priest",  "Holy70PvE"):         "Holy60PvE",
    ("Priest",  "Shadow70PvE"):       "Shadow60PvE",
    ("Rogue",   "Combat70PvE"):       "Combat60PvE",
    ("Shaman",  "Elemental70PvE"):    "Elemental60PvE",
    ("Shaman",  "Enhancement70PvE"):  "Enhancement60PvE",
    ("Shaman",  "Restoration70PvE"):  "Restoration60PvE",
    ("Warlock", "Affliction70PvE"):   "Affliction60PvE",
    ("Warrior", "Fury70PvE"):         "Fury60PvE",
    ("Warrior", "Protection70PvE"):   "Protection60PvE",
}

DEFAULT_SOURCE = (
    Path(__file__).resolve().parents[1]
    / "sql" / "db-characters" / "base"
    / "npc_talent_template_data_2_70_pve_t6.sql"
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Derive 60PvE talent templates from TBC 70PvE data."
    )
    parser.add_argument("--source", default=str(DEFAULT_SOURCE),
                        help=f"Source SQL file (default: {DEFAULT_SOURCE})")
    parser.add_argument("--out", default=None,
                        help="Output SQL file (default: out/classic_pve_talents.sql)")
    args = parser.parse_args()

    source = Path(args.source)
    if not source.exists():
        raise SystemExit(f"Source file not found: {source}")

    out_path = (
        Path(args.out) if args.out
        else Path(__file__).parent / "out" / "classic_pve_talents.sql"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    sql = source.read_text(encoding="utf-8")

    # Each talent row in the source looks like:  ('Druid', 'Bear70PvE', 16930)
    row_pat = re.compile(r"\('(\w+)',\s*'(\w+70PvE)',\s*(\d+)\)")
    rows_by_spec: dict[tuple[str, str], list[int]] = {}
    for m in row_pat.finditer(sql):
        cls, tbc_spec, tid = m.group(1), m.group(2), int(m.group(3))
        if (cls, tbc_spec) in SPEC_MAP:
            rows_by_spec.setdefault((cls, tbc_spec), []).append(tid)

    missing = [k for k in SPEC_MAP if k not in rows_by_spec]
    if missing:
        print(f"WARN: {len(missing)} expected spec(s) not found in source:")
        for k in missing:
            print(f"  {k[0]}/{k[1]}")

    if not rows_by_spec:
        raise SystemExit("No matching talent rows found in source.")

    total_rows = sum(len(v) for v in rows_by_spec.values())
    lines = [
        "-- Classic-era 60PvE talent templates, derived from TBC-70 PvE data.",
        "-- WotLK kept every TBC talent in its original tree position, so TBC",
        "-- spell IDs are valid on AzerothCore 3.3.5a. Result: a 41-point build",
        "-- applied to a level-60 bot (which natively gets 50 points).",
        f"-- {len(rows_by_spec)} specs / {total_rows} talent rows",
        "",
        "/*!40000 ALTER TABLE `mod_npc_talent_template_talents` DISABLE KEYS */;",
        "INSERT INTO `mod_npc_talent_template_talents` "
        "(`playerClass`, `playerSpec`, `talentId`) VALUES",
    ]
    rows: list[str] = []
    for (cls, tbc_spec), tids in sorted(rows_by_spec.items()):
        new_spec = SPEC_MAP[(cls, tbc_spec)]
        for tid in tids:
            rows.append(f"('{cls}', '{new_spec}', {tid})")
    for i, row in enumerate(rows):
        sep = "," if i < len(rows) - 1 else ";"
        lines.append(f"{row}{sep}")
    lines.append("/*!40000 ALTER TABLE `mod_npc_talent_template_talents` ENABLE KEYS */;")

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"Wrote: {out_path}")
    print(f"  {len(rows_by_spec)} specs, {total_rows} talent rows")
    for (cls, tbc_spec), tids in sorted(rows_by_spec.items()):
        new_spec = SPEC_MAP[(cls, tbc_spec)]
        print(f"    {cls:8s} {tbc_spec:25s} -> {new_spec:25s} ({len(tids)} talents)")


if __name__ == "__main__":
    main()
