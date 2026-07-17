#!/usr/bin/env python3
"""
Backfill missing ranged/relic (pos 17) gear rows in already-generated TBC BiS
SQL files, in place. Some Wowhead guides head that section "Ranged Weapons" /
"Wands" (etc.), which older TOC_TO_INV_SLOTS entries didn't map, so hunters,
casters and rogues came out with no pos-17 item (hunters had no bow/gun at all).

extract_tbc_bis.py's slot map is now fixed, so fresh generations are correct.
This repairs existing output surgically: for each spec missing a pos-17 row it
re-fetches that spec's guide, extracts the ranged/relic item, and inserts a
single pos-17 row (with the era enchant) — every other row is left untouched.

Usage:
  python add_missing_ranged_inplace.py                 # all out/tbc_pve_p*_bis.sql
  python add_missing_ranged_inplace.py out/tbc_pve_p3_bis.sql ...
  python add_missing_ranged_inplace.py --dry-run
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

RANGED_INV_SLOT = 18   # extract_bis_by_slot key for ranged/relic
RANGED_POS = 17        # gear-template column

_GEAR = re.compile(
    r"^\('([^']+)', '([^']+)', @RACEMASK_ALL, (\d+), \d+, \d+, .*?\)(,|;)\s*$"
)
_LEVEL = re.compile(r"(60|70|80)")
_url_cache: dict[str, str] = {}


def _load_urls(sql_path: Path) -> dict[tuple[str, str], str]:
    """(Class, base Spec) -> guide URL, from the sibling *_urls.txt cache."""
    urls_path = Path(str(sql_path).replace("_bis.sql", "_urls.txt"))
    out: dict[tuple[str, str], str] = {}
    if not urls_path.exists():
        return out
    for line in urls_path.read_text(encoding="utf-8").splitlines():
        cols = line.split("\t")
        if len(cols) >= 6 and cols[5].startswith("http"):
            out[(cols[3], cols[4])] = cols[5]
    return out


# Section headings (lowercased) that hold the ranged/relic item.
_RANGED_HEADINGS = {
    "ranged", "ranged weapon", "ranged weapons", "wand", "wands",
    "thrown", "thrown weapon", "thrown weapons", "bow", "bows",
    "gun", "guns", "crossbow", "crossbows", "relic", "relics",
    "idol", "idols", "totem", "totems", "libram", "librams",
}


def _first_item_in_ranged_section(markup: str) -> int:
    """Fallback: first [item=N] under a ranged/relic heading, ignoring the
    BiS/Best row label (some guides label the ranged table differently)."""
    parts = re.split(r'\[h3 [^\]]*toc="([^"]+)"[^\]]*\]', markup)
    for i in range(1, len(parts) - 1, 2):
        if parts[i].strip().lower() in _RANGED_HEADINGS:
            m = re.search(r"\[item=(\d+)", parts[i + 1])
            if m:
                return int(m.group(1))
    return 0


_subclass_cache: dict[int, str] = {}


def _ranged_subclass(item_id: int) -> str:
    """'Bow'/'Gun'/'Crossbow' if the item is a ranged weapon, else ''."""
    if item_id not in _subclass_cache:
        try:
            h = etb.fetch_html(f"https://wotlkdb.com/?item={item_id}")
            m = re.search(r">(Bow|Gun|Crossbow)<", h) or re.search(r'"(Bow|Gun|Crossbow)"', h)
            _subclass_cache[item_id] = m.group(1) if m else ""
        except Exception:
            _subclass_cache[item_id] = ""
    return _subclass_cache[item_id]


def _first_ranged_in_weapons(markup: str) -> int:
    """Last-resort (pre-raid/Kara hunter guides): the ranged weapon is folded
    into a combined "Weapons" section. Return the first item there whose weapon
    subclass is Bow/Gun/Crossbow (guides list the BiS pick first)."""
    parts = re.split(r'\[h3 [^\]]*toc="([^"]+)"[^\]]*\]', markup)
    for i in range(1, len(parts) - 1, 2):
        if parts[i].strip().lower() in ("weapons", "weapon"):
            for it in dict.fromkeys(int(x) for x in re.findall(r"\[item=(\d+)", parts[i + 1])):
                if _ranged_subclass(it):
                    return it
    return 0


def _fetch_ranged_item(url: str) -> int:
    if url not in _url_cache:
        markup = etb.extract_markup_text(etb.fetch_html(url))
        slots = etb.extract_bis_by_slot(markup)
        item = (slots.get(RANGED_INV_SLOT, 0)
                or _first_item_in_ranged_section(markup)
                or _first_ranged_in_weapons(markup))
        _url_cache[url] = str(item)
    return int(_url_cache[url])


def patch_file(path: Path, dry_run: bool) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    urls = _load_urls(path)

    # Which specs already have a pos-17 row?
    have_17: set[str] = set()
    last_row_idx: dict[str, int] = {}   # spec -> line index of its terminating (`);`) gear row
    spec_meta: dict[str, tuple[str, str, int]] = {}  # spec -> (class, base_spec, level)
    for i, line in enumerate(lines):
        m = _GEAR.match(line.rstrip("\n"))
        if not m:
            continue
        cls, full_spec, pos, term = m.group(1), m.group(2), int(m.group(3)), m.group(4)
        if pos == RANGED_POS:
            have_17.add(full_spec)
        if term == ";":
            last_row_idx[full_spec] = i
        lv = _LEVEL.search(full_spec)
        if lv:
            spec_meta[full_spec] = (cls, full_spec[: lv.start()], int(lv.group(1)))

    added, skipped = [], []
    # Insert from the bottom up so earlier indices stay valid.
    for full_spec in sorted(last_row_idx, key=lambda s: -last_row_idx[s]):
        if full_spec in have_17 or full_spec not in spec_meta:
            continue
        cls, base, level = spec_meta[full_spec]
        url = urls.get((cls, base))
        if not url:
            skipped.append(f"{full_spec} (no guide URL)")
            continue
        try:
            item = _fetch_ranged_item(url)
        except Exception as e:
            skipped.append(f"{full_spec} (fetch error: {e})")
            continue
        if not item:
            skipped.append(f"{full_spec} (no ranged item in guide)")
            continue
        ench = era.enchants_for(level, cls, base).get(RANGED_POS, 0)
        idx = last_row_idx[full_spec]
        cur = lines[idx].rstrip("\n")
        # turn the current terminator ';' into ',' and append the new pos-17 row as terminator
        lines[idx] = cur[:-1] + ",\n"
        new_row = (f"('{cls}', '{full_spec}', @RACEMASK_ALL, "
                   f"{RANGED_POS}, {item}, {ench}, 0, 0, 0, 0, 0);\n")
        lines.insert(idx + 1, new_row)
        added.append(f"{full_spec}: pos17 item={item} enchant={ench}")

    if added and not dry_run:
        path.write_text("".join(lines), encoding="utf-8")
    print(f"\n{path.name}: +{len(added)} ranged row(s)" + ("  [dry-run]" if dry_run else ""))
    for a in added:
        print(f"    + {a}")
    for s in skipped:
        print(f"    - skipped {s}")
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
    print(f"\n{'Would add' if args.dry_run else 'Added'} {total} ranged row(s) across {len(targets)} file(s).")


if __name__ == "__main__":
    main()
