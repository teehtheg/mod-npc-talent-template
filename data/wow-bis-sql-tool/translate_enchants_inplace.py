#!/usr/bin/env python3
"""
Translate the `enchant` column of already-generated WotLK BiS SQL files from
enchant *spell* IDs (Spell.dbc) to SpellItemEnchantment IDs, in place.

Background: the WotLK gear-planner hash encodes a permanent enchant as its
enchant spell ID (e.g. 60691 = "Enchant 2H Weapon - Massacre"). The mod's
`mod_npc_talent_template_gear.enchant` column is consumed by Item::SetEnchantment
and must instead be a SpellItemEnchantment ID (60691 -> 3827). Files generated
before extract_wotlk_bis.py learned to translate carry the raw spell IDs, so the
enchants silently never apply in-game.

This is the freeze-items counterpart to the generator fix: it rewrites ONLY the
enchant column and leaves every item, gem, and structural token untouched (same
approach as the faction-swap post-process). New generations from the fixed
extract_wotlk_bis.py already emit correct IDs; this repairs older output.

Translation rule (unambiguous by magnitude): valid SpellItemEnchantment IDs are
small (<= ~3900); enchant spell IDs are large (>= 13890). Any enchant value
>= 10000 is treated as a spell ID and translated via extract_wotlk_bis's seed
map (falling back to a live wotlkdb.com lookup for anything unseen). Values 0 and
< 10000 are left as-is.

Usage:
  python translate_enchants_inplace.py                         # all out/wotlk_pve_p*_bis.sql
  python translate_enchants_inplace.py out/wotlk_pve_p4_bis.sql ...
  python translate_enchants_inplace.py --dry-run               # report, write nothing
"""
from __future__ import annotations

import argparse
import glob
import importlib.util
import re
import sys
from pathlib import Path

_HERE = Path(__file__).parent

# Load the generator module for its authoritative spell->SIE resolver + seed map.
_spec = importlib.util.spec_from_file_location(
    "extract_wotlk_bis", _HERE / "extract_wotlk_bis.py"
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["extract_wotlk_bis"] = _mod  # dataclass introspection needs this registered
_spec.loader.exec_module(_mod)
fetch_spell_enchant_id = _mod.fetch_spell_enchant_id

SPELL_ID_THRESHOLD = 10000  # at/above this, an enchant value is a Spell.dbc ID

# A gear data row:
#   ('Class', 'Spec', @RACEMASK.., pos, item, enchant, s1, s2, s3, bonus, pris)[,;]
# Capture the enchant field (6th value) while preserving everything around it.
_ROW = re.compile(
    r"^(\('[^']*', '[^']*', @RACEMASK\w*, \d+, \d+, )(\d+)(, .*)$"
)


def translate_file(path: Path, dry_run: bool) -> tuple[int, int, dict[int, int]]:
    """Return (rows_scanned, rows_translated, {spell: sie}) for one file."""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    scanned = translated = 0
    used: dict[int, int] = {}
    out: list[str] = []
    for line in lines:
        stripped = line.rstrip("\n")
        m = _ROW.match(stripped)
        if not m:
            out.append(line)
            continue
        scanned += 1
        enchant = int(m.group(2))
        if enchant >= SPELL_ID_THRESHOLD:
            sie = fetch_spell_enchant_id(enchant)
            used[enchant] = sie
            if sie != enchant:
                translated += 1
                nl = "\n" if line.endswith("\n") else ""
                out.append(f"{m.group(1)}{sie}{m.group(3)}{nl}")
                continue
        out.append(line)
    if not dry_run and translated:
        path.write_text("".join(out), encoding="utf-8")
    return scanned, translated, used


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("files", nargs="*", help="SQL files (default: out/wotlk_pve_p*_bis.sql)")
    ap.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    args = ap.parse_args()

    targets = args.files or sorted(glob.glob(str(_HERE / "out" / "wotlk_pve_p*_bis.sql")))
    if not targets:
        print("No target files found.", file=sys.stderr)
        sys.exit(1)

    all_used: dict[int, int] = {}
    total_tx = 0
    for f in targets:
        p = Path(f)
        scanned, tx, used = translate_file(p, args.dry_run)
        all_used.update(used)
        total_tx += tx
        print(f"{p.name}: {scanned} gear rows, {tx} enchant(s) translated"
              + ("  [dry-run]" if args.dry_run else ""))

    unresolved = {sp: sie for sp, sie in all_used.items() if sie == 0}
    if unresolved:
        print(f"\nWARNING: {len(unresolved)} spell ID(s) could not be resolved "
              f"(left unchanged, will not enchant): {sorted(unresolved)}")
    print(f"\n{'Would translate' if args.dry_run else 'Translated'} {total_tx} rows across "
          f"{len(targets)} file(s); {len(all_used)} distinct spell IDs seen.")


if __name__ == "__main__":
    main()
