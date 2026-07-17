#!/usr/bin/env python3
"""
Fill the socket columns (socket1/2/3) of already-generated TBC (level-70) BiS SQL
files with colour-matched gems, in place. TBC introduced sockets but the TBC
extractor previously left gems at 0 (unlike level 80, which gets gems from the
gear planner). Classic (level 60) gear has no sockets and is left untouched.

Gems come from era_enchants.gems_for(70, class, spec, item) — colour-matched to
each item's sockets (item_template colours bundled in tbc_socket_colors.json) so
socket bonuses fire. Only socket1/2/3 are rewritten; items, enchant and the rest
are left as-is. extract_tbc_bis.py now fills sockets too, so fresh generations
are already gemmed; this repairs older output.

Usage:
  python apply_tbc_gems_inplace.py                 # all out/tbc_pve_p*_bis.sql
  python apply_tbc_gems_inplace.py out/tbc_pve_p3_bis.sql ...
  python apply_tbc_gems_inplace.py --dry-run
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
era = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("era_enchants", era)
_spec.loader.exec_module(era)

# ('Class', 'Spec', @RACEMASK.., pos, item, enchant, s1, s2, s3, bonus, pris)
_ROW = re.compile(
    r"^(\('([^']+)', '([^']+)', @RACEMASK\w*, \d+, (\d+), \d+, )\d+, \d+, \d+(, \d+, \d+\))([,;])\s*$"
)
_LEVEL = re.compile(r"(60|70|80)")


def apply_file(path: Path, dry_run: bool) -> tuple[int, int]:
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    scanned = changed = 0
    out = []
    for line in lines:
        m = _ROW.match(line.rstrip("\n"))
        if not m:
            out.append(line)
            continue
        cls, full_spec, item = m.group(2), m.group(3), int(m.group(4))
        lv = _LEVEL.search(full_spec)
        if not lv or int(lv.group(1)) != 70:
            out.append(line)
            continue
        scanned += 1
        base = full_spec[: lv.start()]
        s1, s2, s3 = era.gems_for(70, cls, base, item)
        nl = "\n" if line.endswith("\n") else ""
        new = f"{m.group(1)}{s1}, {s2}, {s3}{m.group(5)}{m.group(6)}{nl}"
        if new != line:
            changed += 1
        out.append(new)
    if not dry_run and changed:
        path.write_text("".join(out), encoding="utf-8")
    return scanned, changed


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("files", nargs="*")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    targets = args.files or sorted(glob.glob(str(_HERE / "out" / "tbc_pve_p*_bis.sql")))
    total = 0
    for f in targets:
        p = Path(f)
        scanned, changed = apply_file(p, args.dry_run)
        total += changed
        print(f"{p.name}: {scanned} lvl-70 gear rows, {changed} row(s) gemmed"
              + ("  [dry-run]" if args.dry_run else ""))
    print(f"\n{'Would gem' if args.dry_run else 'Gemmed'} {total} rows across {len(targets)} file(s).")


if __name__ == "__main__":
    main()
