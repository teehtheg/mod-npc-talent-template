#!/usr/bin/env python3
"""
Backfill missing trinket (pos 12/13) gear rows in already-generated TBC BiS SQL
files, in place. Some tank/healer guides rank trinket-table rows by stat amount
("51 Stam + Proc", …) with no BiS/Best label, so the older extractor captured no
trinkets for those specs (e.g. Warrior Protection p3, Priest Disc/Holy p2/p3).

extract_tbc_bis.py now falls back to the first data row when a slot has no
BiS/Best-labelled row, so fresh generations are correct. This repairs existing
output: for each spec missing pos 12 and/or 13 it re-fetches the guide, extracts
the trinket item(s), and inserts just the missing row(s) — nothing else changes.
Trinkets carry no enchant; gems are applied when the trinket has sockets.

Usage:
  python add_missing_trinkets_inplace.py                 # all out/tbc_pve_p*_bis.sql
  python add_missing_trinkets_inplace.py out/tbc_pve_p3_bis.sql ...
  python add_missing_trinkets_inplace.py --dry-run
"""
from __future__ import annotations

import argparse
import glob
import importlib.util
import re
import sys
from pathlib import Path

_HERE = Path(__file__).parent


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, _HERE / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, mod)
    spec.loader.exec_module(mod)
    return mod


etb = _load("extract_tbc_bis", "extract_tbc_bis.py")
era = _load("era_enchants", "era_enchants.py")

# pos -> inv_slot key returned by extract_bis_by_slot for the two trinket slots
TRINKET_POS = {12: 13, 13: 14}
_GEAR = re.compile(r"^\('([^']+)', '([^']+)', @RACEMASK_ALL, (\d+), \d+, \d+, .*?\)(,|;)\s*$")
_LEVEL = re.compile(r"(60|70|80)")
_slots_cache: dict[str, dict[int, int]] = {}


def _load_urls(sql_path: Path) -> dict[tuple[str, str], str]:
    urls_path = Path(str(sql_path).replace("_bis.sql", "_urls.txt"))
    out: dict[tuple[str, str], str] = {}
    if urls_path.exists():
        for line in urls_path.read_text(encoding="utf-8").splitlines():
            c = line.split("\t")
            if len(c) >= 6 and c[5].startswith("http"):
                out[(c[3], c[4])] = c[5]
    return out


def _slots(url: str) -> dict[int, int]:
    if url not in _slots_cache:
        _slots_cache[url] = etb.extract_bis_by_slot(etb.extract_markup_text(etb.fetch_html(url)))
    return _slots_cache[url]


def patch_file(path: Path, dry_run: bool) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    urls = _load_urls(path)

    # Key by (class, spec): spec names like Protection/Holy are shared across
    # classes (Warrior/Paladin, Priest/Paladin), so one class must not mask another.
    have: dict[tuple[str, str], set[int]] = {}
    last_idx: dict[tuple[str, str], int] = {}
    meta: dict[tuple[str, str], tuple[str, str, int]] = {}
    for i, line in enumerate(lines):
        m = _GEAR.match(line.rstrip("\n"))
        if not m:
            continue
        cls, full_spec, pos, term = m.group(1), m.group(2), int(m.group(3)), m.group(4)
        key = (cls, full_spec)
        have.setdefault(key, set()).add(pos)
        if term == ";":
            last_idx[key] = i
        lv = _LEVEL.search(full_spec)
        if lv:
            meta[key] = (cls, full_spec[: lv.start()], int(lv.group(1)))

    added, skipped = [], []
    for key in sorted(last_idx, key=lambda k: -last_idx[k]):
        cls, full_spec = key
        _, base, level = meta.get(key, (None, None, None))
        if level != 70:
            continue
        missing = [p for p in (12, 13) if p not in have.get(key, set())]
        if not missing:
            continue
        url = urls.get((cls, base))
        if not url:
            skipped.append(f"{full_spec} (no guide URL)")
            continue
        try:
            slots = _slots(url)
        except Exception as e:
            skipped.append(f"{full_spec} (fetch error: {e})")
            continue
        new_rows = []
        for pos in missing:
            item = slots.get(TRINKET_POS[pos], 0)
            if not item:
                continue
            s1, s2, s3 = era.gems_for(level, cls, base, item)
            new_rows.append(f"('{cls}', '{full_spec}', @RACEMASK_ALL, "
                            f"{pos}, {item}, 0, {s1}, {s2}, {s3}, 0, 0)")
        if not new_rows:
            skipped.append(f"{cls}/{full_spec} (no trinket items in guide)")
            continue
        idx = last_idx[key]
        lines[idx] = lines[idx].rstrip("\n")[:-1] + ",\n"  # ';' -> ','
        block = ",\n".join(new_rows) + ";\n"
        lines.insert(idx + 1, block)
        added.append(f"{cls}/{full_spec}: +pos {missing} -> {[r.split(', ')[4] for r in new_rows]}")

    if added and not dry_run:
        path.write_text("".join(lines), encoding="utf-8")
    print(f"\n{path.name}: {len(added)} spec(s) backfilled" + ("  [dry-run]" if dry_run else ""))
    for a in added:
        print(f"    + {a}")
    for sk in skipped:
        print(f"    - skipped {sk}")
    return added


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("files", nargs="*")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    targets = args.files or sorted(glob.glob(str(_HERE / "out" / "tbc_pve_p*_bis.sql")))
    total = 0
    for f in targets:
        total += len(patch_file(Path(f), args.dry_run))
    print(f"\n{'Would backfill' if args.dry_run else 'Backfilled'} {total} spec(s) across {len(targets)} file(s).")


if __name__ == "__main__":
    main()
