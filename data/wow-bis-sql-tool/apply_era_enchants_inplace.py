#!/usr/bin/env python3
"""
Apply era-appropriate enchants (era_enchants.py) to already-generated Classic
(level-60) and TBC (level-70) BiS SQL files, in place — rewriting ONLY the
`enchant` column and leaving items/gems untouched (freeze-items approach).

Each gear row looks like:
  ('Class', 'Spec60PvE...BiS', @RACEMASK_ALL, <pos>, <item>, <enchant>, ...)
The build level (60/70/80) is read from the spec name; the base spec (text before
that level) selects the enchant set. Level-80 rows are skipped (their enchants
come from the WotLK gear planner).

Usage:
  python apply_era_enchants_inplace.py                       # all classic + tbc out files
  python apply_era_enchants_inplace.py out/classic_pve_p5_bis.sql ...
  python apply_era_enchants_inplace.py --dry-run
"""
from __future__ import annotations

import argparse
import glob
import importlib.util
import re
import sys
from pathlib import Path

_HERE = Path(__file__).parent
_spec = importlib.util.spec_from_file_location("era_enchants", _HERE / "era_enchants.py")
era_enchants = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("era_enchants", era_enchants)
_spec.loader.exec_module(era_enchants)

# ('Class', 'Spec', @RACEMASK.., pos, item, enchant, s1, s2, s3, bonus, pris)
_ROW = re.compile(
    r"^(\('([^']+)', '([^']+)', @RACEMASK\w*, (\d+), \d+, )(\d+)(, .*)$"
)
_LEVEL = re.compile(r"(60|70|80)")


def _base_spec_and_level(full_spec: str):
    m = _LEVEL.search(full_spec)
    if not m:
        return None, None
    return full_spec[: m.start()], int(m.group(1))


def apply_file(path: Path, dry_run: bool) -> tuple[int, int]:
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    scanned = changed = 0
    out = []
    for line in lines:
        m = _ROW.match(line.rstrip("\n"))
        if not m:
            out.append(line)
            continue
        cls, full_spec, pos, old = m.group(2), m.group(3), int(m.group(4)), int(m.group(5))
        base, level = _base_spec_and_level(full_spec)
        if base is None or level >= 80:
            out.append(line)
            continue
        scanned += 1
        new = era_enchants.enchants_for(level, cls, base).get(pos, 0)
        if new != old:
            changed += 1
            nl = "\n" if line.endswith("\n") else ""
            out.append(f"{m.group(1)}{new}{m.group(6)}{nl}")
        else:
            out.append(line)
    if not dry_run and changed:
        path.write_text("".join(out), encoding="utf-8")
    return scanned, changed


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("files", nargs="*")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    targets = args.files or (
        sorted(glob.glob(str(_HERE / "out" / "classic_pve_*_bis.sql")))
        + sorted(glob.glob(str(_HERE / "out" / "tbc_pve_*_bis.sql")))
    )
    total = 0
    for f in targets:
        p = Path(f)
        scanned, changed = apply_file(p, args.dry_run)
        total += changed
        print(f"{p.name}: {scanned} enchantable gear rows, {changed} enchant(s) set"
              + ("  [dry-run]" if args.dry_run else ""))
    print(f"\n{'Would set' if args.dry_run else 'Set'} {total} enchants across {len(targets)} file(s).")


if __name__ == "__main__":
    main()
