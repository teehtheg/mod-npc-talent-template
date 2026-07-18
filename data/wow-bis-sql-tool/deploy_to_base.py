#!/usr/bin/env python3
"""Deploy generated SQL from out/ into the module's db-characters/base/ folder.

The module's SQL updater applies files in ascending filename order (compared by
filename only). Data files must therefore land *after* the schema-creation file
and the category migration, so this deploy step renames each generated file with
a numeric prefix:

    00_npc_talent_template.sql                 schema        (hand-maintained)
    01_..._index_category.sql                  migration     (hand-maintained)
    10_/11_ npc_talent_template_data_*         S6/T6 sets    (hand-maintained)
    20_  classic talents
    21_-24_  classic BiS (p2, p4, p5, p6)
    30_-33_  tbc BiS (p0-p3)
    40_-43_  wotlk BiS (p1-p4)

Only the generated files below are copied; the hand-maintained 00_/01_/10_/11_
files are never touched. The DELETE idempotency block is already baked into the
generated files by the extractors (see sql_idempotency.py), so a full
regenerate -> deploy cycle reproduces the committed base/ files exactly.

Usage (from wow-bis-sql-tool/):
    python deploy_to_base.py            # copy out/* -> base/ with prefixes
    python deploy_to_base.py --dry-run  # show what would be copied
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

# out/ filename -> base/ filename. Extend this when adding phases/expansions.
MAPPING = {
    "classic_pve_talents.sql": "20_classic_pve_talents.sql",
    "classic_pve_p2_bis.sql":  "21_classic_pve_p2_bis.sql",
    "classic_pve_p4_bis.sql":  "22_classic_pve_p4_bis.sql",
    "classic_pve_p5_bis.sql":  "23_classic_pve_p5_bis.sql",
    "classic_pve_p6_bis.sql":  "24_classic_pve_p6_bis.sql",
    "tbc_pve_p0_bis.sql":      "30_tbc_pve_p0_bis.sql",
    "tbc_pve_p1_bis.sql":      "31_tbc_pve_p1_bis.sql",
    "tbc_pve_p2_bis.sql":      "32_tbc_pve_p2_bis.sql",
    "tbc_pve_p3_bis.sql":      "33_tbc_pve_p3_bis.sql",
    "wotlk_pve_p1_bis.sql":    "40_wotlk_pve_p1_bis.sql",
    "wotlk_pve_p2_bis.sql":    "41_wotlk_pve_p2_bis.sql",
    "wotlk_pve_p3_bis.sql":    "42_wotlk_pve_p3_bis.sql",
    "wotlk_pve_p4_bis.sql":    "43_wotlk_pve_p4_bis.sql",
    "wotlk_pve_talents.sql":   "44_wotlk_pve_talents.sql",
    "wotlk_pve_glyphs.sql":    "45_wotlk_pve_glyphs.sql",
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be copied without writing.")
    args = parser.parse_args()

    here = Path(__file__).parent
    out_dir = here / "out"
    base_dir = here.parent / "sql" / "db-characters" / "base"

    if not base_dir.is_dir():
        print(f"ERROR: base dir not found: {base_dir}", file=sys.stderr)
        sys.exit(1)

    copied = 0
    missing = []
    for src_name, dst_name in MAPPING.items():
        src = out_dir / src_name
        dst = base_dir / dst_name
        if not src.exists():
            missing.append(src_name)
            continue
        action = "would copy" if args.dry_run else "copying"
        print(f"  {action}: out/{src_name}  ->  base/{dst_name}")
        if not args.dry_run:
            shutil.copyfile(src, dst)
        copied += 1

    # Warn about generated out/ files with no mapping (e.g. a new phase).
    if out_dir.is_dir():
        known = set(MAPPING)
        for f in sorted(out_dir.glob("*_bis.sql")):
            if f.name not in known:
                print(f"  NOTE: out/{f.name} has no mapping - add it to MAPPING if it should deploy.")

    print()
    verb = "Would deploy" if args.dry_run else "Deployed"
    print(f"{verb} {copied} file(s) to {base_dir}")
    if missing:
        print(f"  {len(missing)} mapped file(s) not present in out/ (not generated yet): "
              + ", ".join(missing))


if __name__ == "__main__":
    main()
