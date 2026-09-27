#!/usr/bin/env python3
"""
Item metadata (class / subclass / InventoryType / sockets) from the 3.3.5a
world DB, for the BiS extractors.

Wowhead guides only tell us *which* item is ranked where; they don't reliably
say which hand a weapon goes in, and TBC-Classic pages sometimes link items that
don't exist in the 3.3.5a client at all (e.g. Anniversary-only ids > 200000).
Both are answered by item_template, which this module reads from the core
repo's `data/sql/base/db_world/item_template.sql` (same source enchant_translate
uses) and caches as `out/item_info_cache.json` so later runs start instantly.

Public API:
  get(item_id)            -> ItemInfo | None   (None = not in the 3.3.5a client)
  fits_slot(item, inv)    -> bool              can `item` go in extractor inv_slot `inv`
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import NamedTuple

_HERE = Path(__file__).parent
_CACHE = _HERE / "out" / "item_info_cache.json"
_CACHE_VERSION = 2  # bump when the cached fields change

_et_spec = importlib.util.spec_from_file_location("enchant_translate", _HERE / "enchant_translate.py")
enchant_translate = importlib.util.module_from_spec(_et_spec)
sys.modules.setdefault("enchant_translate", enchant_translate)
_et_spec.loader.exec_module(enchant_translate)

# item_template.InventoryType values
INV_HEAD, INV_NECK, INV_SHOULDER, INV_BODY, INV_CHEST = 1, 2, 3, 4, 5
INV_WAIST, INV_LEGS, INV_FEET, INV_WRIST, INV_HANDS = 6, 7, 8, 9, 10
INV_FINGER, INV_TRINKET, INV_ONE_HAND, INV_SHIELD, INV_RANGED = 11, 12, 13, 14, 15
INV_CLOAK, INV_TWO_HAND, INV_ROBE, INV_MAIN_HAND, INV_OFF_HAND = 16, 17, 20, 21, 22
INV_HOLDABLE, INV_THROWN, INV_RANGED_RIGHT, INV_RELIC = 23, 25, 26, 28

# Extractor inv_slot (1-based gear slot, see extract_tbc_bis.INV_SLOT_TO_POS)
# -> InventoryTypes that may be equipped there. Hands (16/17) are resolved
# separately by the extractor since they depend on the spec (dual wield, 2H).
_SLOT_TYPES: dict[int, set[int]] = {
    1: {INV_HEAD},
    2: {INV_NECK},
    3: {INV_SHOULDER},
    5: {INV_CHEST, INV_ROBE},
    6: {INV_WAIST},
    7: {INV_LEGS},
    8: {INV_FEET},
    9: {INV_WRIST},
    10: {INV_HANDS},
    11: {INV_FINGER},
    12: {INV_FINGER},
    13: {INV_TRINKET},
    14: {INV_TRINKET},
    15: {INV_CLOAK},
    18: {INV_RANGED, INV_THROWN, INV_RANGED_RIGHT, INV_RELIC},
}

MAIN_HAND_TYPES = {INV_ONE_HAND, INV_MAIN_HAND, INV_TWO_HAND}
RANGED_TYPES = _SLOT_TYPES[18]


class ItemInfo(NamedTuple):
    entry: int
    name: str
    item_class: int
    subclass: int
    inventory_type: int
    socket_colors: tuple[int, ...]
    allowable_class: int  # class bitmask (1 << (classId - 1)); 0 / -1 = any class

    @property
    def is_two_hand(self) -> bool:
        return self.inventory_type == INV_TWO_HAND

    def usable_by(self, player_class: str) -> bool:
        """False if item_template restricts the item to other classes."""
        bit = CLASS_MASK.get(player_class)
        return bit is None or self.allowable_class <= 0 or bool(self.allowable_class & bit)


# playerClass (as written in the SQL) -> item_template.AllowableClass bit
CLASS_MASK = {
    "Warrior": 1, "Paladin": 2, "Hunter": 4, "Rogue": 8, "Priest": 16,
    "Death Knight": 32, "Shaman": 64, "Mage": 128, "Warlock": 256, "Druid": 1024,
}


_items: dict[int, ItemInfo] | None = None


def _build_cache() -> dict[str, list]:
    sql_path = enchant_translate.ITEM_TEMPLATE_SQL
    if not sql_path.exists():
        raise FileNotFoundError(
            f"item_template.sql not found at {sql_path} — item_info needs the AzerothCore "
            "core repo's data/sql/base/db_world/ next to this module."
        )
    print(f"[item_info] building cache from {sql_path.name} (one-off) ...", file=sys.stderr)
    text = sql_path.read_text(encoding="utf-8", errors="ignore")
    cols = enchant_translate._parse_create_columns(text, "item_template")
    idx = {c: cols.index(c) for c in (
        "entry", "class", "subclass", "name", "InventoryType",
        "socketColor_1", "socketColor_2", "socketColor_3", "AllowableClass",
    )}
    data: dict = {"__version__": _CACHE_VERSION}
    for t in enchant_translate._iter_value_tuples(text):
        if len(t) != len(cols):
            continue
        sockets = [int(t[idx[f"socketColor_{n}"]]) for n in (1, 2, 3)]
        data[t[idx["entry"]]] = [
            t[idx["name"]].replace("''", "'").replace("\\'", "'"),
            int(t[idx["class"]]),
            int(t[idx["subclass"]]),
            int(t[idx["InventoryType"]]),
            [c for c in sockets if c],
            int(t[idx["AllowableClass"]]),
        ]
    _CACHE.parent.mkdir(parents=True, exist_ok=True)
    _CACHE.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    return data


def _load() -> dict[int, ItemInfo]:
    global _items
    if _items is None:
        raw = json.loads(_CACHE.read_text(encoding="utf-8")) if _CACHE.exists() else {}
        if raw.get("__version__") != _CACHE_VERSION:
            raw = _build_cache()
        _items = {
            int(k): ItemInfo(int(k), v[0], v[1], v[2], v[3], tuple(v[4]), v[5])
            for k, v in raw.items() if k != "__version__"
        }
    return _items


def get(item_id: int) -> ItemInfo | None:
    return _load().get(item_id)


def fits_slot(item: ItemInfo, inv_slot: int) -> bool:
    """True if `item` can be equipped in a non-hand extractor inv_slot."""
    allowed = _SLOT_TYPES.get(inv_slot)
    return allowed is not None and item.inventory_type in allowed


if __name__ == "__main__":
    for arg in sys.argv[1:] or ["30910", "33687", "32375", "281748"]:
        print(arg, get(int(arg)))
