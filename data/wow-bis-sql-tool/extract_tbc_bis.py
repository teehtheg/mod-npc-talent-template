#!/usr/bin/env python3
"""
Extract one Wowhead TBC BiS guide page (tbc.wowhead.com) into mod-npc-talent-template SQL rows.

TBC guides use Wowhead markup with [h3 toc="SlotName"] + [table] structure rather than
gear-planner hashes. Enchants and gems come from era_enchants.py (per class/spec).

Slot resolution:
  - Armor / jewelry sections map by heading (TOC_TO_INV_SLOTS). Each row's item is
    checked against item_template (item_info.py): items missing from the 3.3.5a
    client or with the wrong InventoryType for the slot are skipped.
  - Weapon, off-hand, shield and ranged/relic sections are pooled and resolved by
    the items' InventoryType, not by heading: a "Weapons" table ranking a PvP gavel
    second can no longer push the shield out of the off-hand, 2H weapons clear the
    off-hand, and one-handers only go to the off-hand for dual-wield specs.
  - Rows labelled BiS/Best rank first, then other rows in page order; rows
    labelled PvP rank last. Two-slot sections (rings, trinkets) take the two
    best distinct items.

Usage (PowerShell from wow-bis-sql-tool/):
  python extract_tbc_bis.py \\
    --url "https://tbc.wowhead.com/guides/feral-druid-dps-bt-hyjal-phase-3-..." \\
    --player-class "Druid" \\
    --player-spec "Cat" \\
    --suffix "70PvEP3BiS" \\
    --talent-suffix "Cat70PvE" \\
    --out ".\\out\\tbc_feral_cat_p3_bis.sql"
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import NamedTuple


def _load_sibling(name: str):
    """Path-load a sibling module so this works whether run directly or imported
    via importlib by the batch tool."""
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, mod)
    spec.loader.exec_module(mod)
    return mod


# Level-70 TBC-era enchants + colour-matched gems (per class/spec, SpellItemEnchantment
# ids verified against 3.3.5a) and item_template metadata.
era_enchants = _load_sibling("era_enchants")
item_info = _load_sibling("item_info")

# ---------------------------------------------------------------------------

# Wowhead markup h3 toc heading → list of inv_slot numbers, for armor/jewelry.
# For multi-slot sections (Rings, Trinkets) the list has 2 entries; the
# extractor assigns the 1st ranked item to slots[0], 2nd to slots[1].
# Weapon / off-hand / ranged headings are classified by _hand_section_kind().
TOC_TO_INV_SLOTS: dict[str, list[int]] = {
    "Head": [1],
    "Neck": [2],
    "Shoulder": [3],
    "Shoulders": [3],
    "Back": [15],
    "Cloak": [15],
    "Chest": [5],
    "Wrist": [9],
    "Wrists": [9],
    "Hand": [10],
    "Hands": [10],
    "Gloves": [10],
    "Waist": [6],
    "Belt": [6],
    "Legs": [7],
    "Feet": [8],
    "Boots": [8],
    "Ring": [11, 12],
    "Rings": [11, 12],
    "Finger": [11, 12],
    "Fingers": [11, 12],
    "Trinket": [13, 14],
    "Trinkets": [13, 14],
}
_ARMOR_HEADINGS = {k.lower(): v for k, v in TOC_TO_INV_SLOTS.items()}

MAIN_HAND, OFF_HAND, RANGED = 16, 17, 18

# Heading keyword → section kind for the hand pool. Checked in order, so
# "Ranged Weapons" is ranged (not melee) and "Off Hands and Shields" is off-hand.
_RANGED_WORDS = re.compile(r"ranged|wand|thrown|\bbows?\b|\bguns?\b|crossbow|idol|totem|libram|relic")
_OFF_WORDS = re.compile(r"off[\s-]?hand|shield")
_MAIN_WORDS = re.compile(r"main[\s-]?hand")
_WEAPON_WORDS = re.compile(
    r"weapon|melee|staff|staves|polearm|two[\s-]?hand|one[\s-]?hand|\b[12]h\b|dagger|sword|mace|axe|fist"
)

# Dual-wield / two-hand preference per spec. Hunters may use either (guide order).
_DUAL_WIELD_CLASSES = {"Rogue", "Hunter"}
_DUAL_WIELD_SPECS = {("Warrior", "Fury"), ("Shaman", "Enhancement"), ("Death Knight", "Frost")}
_PREFER_TWO_HAND = {
    ("Warrior", "Arms"), ("Warrior", "ArmsAxe"), ("Warrior", "ArmsSword"),
    ("Paladin", "Retribution"), ("Druid", "Cat"), ("Druid", "Bear"),
    ("Death Knight", "Blood"), ("Death Knight", "Unholy"),
}

# Death Knights didn't exist in TBC: their level-70 sets are derived from Warrior
# guides (see batch_extract_tbc.DERIVED_SPECS), so Warrior-only weapon types are
# dropped and the ranged slot gets a sigil, which TBC had none of.
_DK_WEAPON_SUBCLASSES = {0, 1, 4, 5, 6, 7, 8}  # 1H/2H axe, 1H/2H mace, polearm, 1H/2H sword
DK_SIGIL = 39208  # Sigil of the Dark Rider (Acherus quest reward, the only sigil below level 80)

_OFF_HAND_TYPES = {item_info.INV_OFF_HAND, item_info.INV_SHIELD, item_info.INV_HOLDABLE}
# Weapons that come as a main-hand/off-hand set. Guides rank the set once as
# "Best Pair" and only link the main-hand item.
_PAIRED_OFF_HAND = {32837: 32838}  # Warglaive of Azzinoth (MH) -> (OH)
_PAIR_LABEL = re.compile(r"\bpair\b", re.I)

_OFF_LABEL = re.compile(r"off[\s-]?hand|\boh\b", re.I)
_MAIN_LABEL = re.compile(r"main[\s-]?hand|\bmh\b", re.I)

# inv_slot (1-based WoW gear slot) → pos (0-based column in mod_npc_talent_template_gear)
INV_SLOT_TO_POS: dict[int, int] = {
    1: 0,   # head
    2: 1,   # neck
    3: 2,   # shoulder
    5: 4,   # chest
    6: 5,   # waist
    7: 6,   # legs
    8: 7,   # feet
    9: 8,   # wrist
    10: 9,  # hands
    11: 10, # ring1
    12: 11, # ring2
    13: 12, # trinket1
    14: 13, # trinket2
    15: 14, # back
    16: 15, # main hand
    17: 16, # off hand
    18: 17, # ranged / relic
}

# Icons for gossipText, keyed by (playerClass, playerSpec)
SPEC_ICONS: dict[tuple[str, str], str] = {
    ("Druid",   "Balance"):       "spell_nature_starfall",
    ("Druid",   "Cat"):           "ability_druid_catform",
    ("Druid",   "Bear"):          "ability_racial_bearform",
    ("Druid",   "Restoration"):   "spell_nature_healingtouch",
    ("Hunter",  "Beastmastery"):  "ability_hunter_beasttaming",
    ("Hunter",  "Marksmanship"):  "ability_marksmanship",
    ("Hunter",  "Survival"):      "ability_Hunter_swiftstrike",
    ("Mage",    "Arcane"):        "spell_holy_magicalsentry",
    ("Mage",    "Fire"):          "spell_fire_flamebolt",
    ("Mage",    "Frost"):         "spell_frost_frostbolt02",
    ("Paladin", "Holy"):          "spell_holy_holybolt",
    ("Paladin", "Protection"):    "spell_holy_devotionaura",
    ("Paladin", "Retribution"):   "spell_holy_auraoflight",
    ("Priest",  "Discipline"):    "spell_holy_wordfortitude",
    ("Priest",  "Holy"):          "spell_holy_holybolt",
    ("Priest",  "Shadow"):        "spell_shadow_shadowwordpain",
    ("Rogue",   "Assassination"): "ability_rogue_eviscerate",
    ("Rogue",   "Combat"):        "ability_backstab",
    ("Rogue",   "Subtlety"):      "ability_stealth",
    ("Shaman",  "Elemental"):     "spell_nature_lightning",
    ("Shaman",  "Enhancement"):   "spell_nature_lightningshield",
    ("Shaman",  "Restoration"):   "spell_nature_magicimmunity",
    ("Warlock", "Affliction"):    "spell_shadow_deathcoil",
    ("Warlock", "Demonology"):    "spell_shadow_metamorphosis",
    ("Warlock", "Destruction"):   "spell_shadow_rainoffire",
    ("Warrior", "Arms"):          "ability_rogue_eviscerate",
    ("Warrior", "ArmsAxe"):       "ability_rogue_eviscerate",
    ("Warrior", "ArmsSword"):     "ability_rogue_eviscerate",
    ("Warrior", "Fury"):          "ability_warrior_innerrage",
    ("Warrior", "Protection"):    "ability_warrior_defensivestance",
    ("Death Knight", "Blood"):    "spell_deathknight_bloodpresence",
    ("Death Knight", "Frost"):    "spell_deathknight_frostpresence",
    ("Death Knight", "Unholy"):   "spell_deathknight_unholypresence",
}


class RateLimited(RuntimeError):
    """Wowhead kept refusing requests (403/429/503) after all retries."""


_RETRY_STATUSES = {403, 429, 503}


def fetch_html(url: str, retries: int = 5, backoff: float = 20.0) -> str:
    """GET a page. Wowhead answers request bursts with 403, so those (and 429/503)
    are retried with a growing pause before giving up with RateLimited — callers
    must not mistake a throttled request for a missing guide."""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                return resp.read().decode("utf-8", "ignore")
        except urllib.error.HTTPError as exc:
            if exc.code not in _RETRY_STATUSES:
                raise
            if attempt == retries:
                raise RateLimited(f"HTTP {exc.code} after {retries} retries: {url}") from exc
            wait = backoff * (attempt + 1)
            print(f"       (HTTP {exc.code}, retrying in {wait:.0f}s)", file=sys.stderr)
            time.sleep(wait)
    raise AssertionError("unreachable")


def extract_markup_text(page_html: str) -> str:
    """Decode the WH.markup.printHtml payload from a TBC guide page."""
    calls = re.findall(
        r"WH\.markup\.printHtml\((.*?)(?:\);\s*$|\Z)", page_html, re.S | re.M
    )
    if not calls:
        raise ValueError("No WH.markup.printHtml call found.")

    for raw in reversed(calls):
        raw = raw.strip()
        m = re.search(r'^"((?:\\.|[^"\\])+)', raw, re.S)
        if not m:
            continue
        escaped = m.group(1)
        text = (
            escaped
            .replace("\\r\\n", "\n").replace("\\r", "\n").replace("\\n", "\n")
        )
        text = re.sub(r"\\u([0-9a-fA-F]{4})", lambda x: chr(int(x.group(1), 16)), text)
        text = text.replace('\\"', '"').replace("\\/", "/")
        if len(text) > 500:
            return text

    raise ValueError("Could not extract guide markup text.")


class _Row(NamedTuple):
    item: int
    label: str
    tier: int   # 0 = BiS/Best, 1 = other, 2 = PvP
    order: int  # document order across the whole page
    kind: str   # hand pool only: "main" | "off" | "any" | "ranged"


def _rank_tier(label: str) -> int:
    if re.search(r"pvp", label, re.I):
        return 2
    if re.match(r"^(BiS|Best)", label, re.I):
        return 0
    return 1


def _section_rows(content: str, order_start: int) -> list[tuple[int, str, int]]:
    """(item, rank label, order) for every item row of every table in a section."""
    out: list[tuple[int, str, int]] = []
    order = order_start
    for table_content in re.findall(r"\[table[^\]]*\](.*?)\[/table\]", content, re.S):
        # Tolerate a malformed row-closing tag ("[/tr}" appears in some guides).
        for row in re.findall(r"\[tr\](.*?)\[/tr[\]\}]", table_content, re.S):
            tds = re.findall(r"\[td[^\]]*\](.*?)\[/td\]", row, re.S)
            if len(tds) < 2:
                continue
            m = re.search(r"\[item=(\d+)", tds[1])
            if not m:
                continue  # header/label row (no item link)
            label = re.sub(r"\[[^\]]+\]", "", tds[0]).strip()
            out.append((int(m.group(1)), label, order))
            order += 1
    return out


def _hand_section_kind(heading: str) -> str | None:
    h = heading.lower()
    if _RANGED_WORDS.search(h):
        return "ranged"
    if _OFF_WORDS.search(h):
        return "off"
    if _MAIN_WORDS.search(h):
        return "main"
    if _WEAPON_WORDS.search(h):
        return "any"
    return None


def _ranked(rows: list[_Row]) -> list[_Row]:
    return sorted(rows, key=lambda r: (r.tier, r.order))


def _resolve_hands(
    pool: list[_Row], player_class: str | None, player_spec: str | None, result: dict[int, int]
) -> None:
    """Fill main hand, off hand and ranged/relic from the pooled weapon rows by
    InventoryType (see module docstring)."""
    key = (player_class, player_spec)
    dual_wield = player_class in _DUAL_WIELD_CLASSES or key in _DUAL_WIELD_SPECS
    prefer_2h = key in _PREFER_TWO_HAND

    def inv(r: _Row) -> int:
        return item_info.get(r.item).inventory_type

    if player_class == "Death Knight":
        pool = [r for r in pool if item_info.get(r.item).item_class == 2
                and item_info.get(r.item).subclass in _DK_WEAPON_SUBCLASSES]

    # Main hand: a main-hand-capable item from a main/any section. Rows labelled
    # "Off Hand" in a mixed table only count once nothing else is left.
    mh_rows = [r for r in pool if r.kind in ("main", "any") and inv(r) in item_info.MAIN_HAND_TYPES]
    if prefer_2h and any(inv(r) == item_info.INV_TWO_HAND for r in mh_rows):
        mh_rows = [r for r in mh_rows if inv(r) == item_info.INV_TWO_HAND]
    elif (dual_wield and player_class != "Hunter"
          and any(inv(r) != item_info.INV_TWO_HAND for r in mh_rows)):
        mh_rows = [r for r in mh_rows if inv(r) != item_info.INV_TWO_HAND]
    mh_rows.sort(key=lambda r: (bool(_OFF_LABEL.search(r.label)), r.tier, r.order))
    mh = mh_rows[0] if mh_rows else None
    if mh:
        result[MAIN_HAND] = mh.item

    # Off hand: nothing next to a two-hander. Dedicated off-hand/shield sections
    # win over a second pick from a combined "Weapons" table.
    if (mh and dual_wield and mh.item in _PAIRED_OFF_HAND and _PAIR_LABEL.search(mh.label)
            and item_info.get(_PAIRED_OFF_HAND[mh.item])):
        result[OFF_HAND] = _PAIRED_OFF_HAND[mh.item]
    elif not (mh and inv(mh) == item_info.INV_TWO_HAND):
        def oh_ok(r: _Row) -> bool:
            if mh and r.item == mh.item:
                return False
            t = inv(r)
            return t in _OFF_HAND_TYPES or (dual_wield and t == item_info.INV_ONE_HAND)

        oh_rows = _ranked([r for r in pool if r.kind == "off" and oh_ok(r)])
        if not oh_rows:
            oh_rows = [r for r in pool if r.kind in ("main", "any") and oh_ok(r)]
            oh_rows.sort(key=lambda r: (bool(_MAIN_LABEL.search(r.label)), r.tier, r.order))
        if oh_rows:
            result[OFF_HAND] = oh_rows[0].item

    # Ranged / relic: a ranged section first, else a bow/gun/wand/relic that a
    # guide folded into its combined "Weapons" table (pre-raid hunter guides).
    rng = [r for r in pool if inv(r) in item_info.RANGED_TYPES]
    rng.sort(key=lambda r: (r.kind != "ranged", r.tier, r.order))
    if player_class == "Death Knight":
        result[RANGED] = DK_SIGIL
    elif rng:
        result[RANGED] = rng[0].item


def extract_bis_by_slot(
    markup_text: str,
    player_class: str | None = None,
    player_spec: str | None = None,
    warnings: list[str] | None = None,
) -> dict[int, int]:
    """
    Parse [h3 toc="SlotName"] + [table] markup.

    Returns {inv_slot: item_id} with the best item per slot. player_class /
    player_spec steer the hand resolution (dual wield, two-hand preference);
    skipped items are described in `warnings` when a list is passed.
    """
    warn = warnings.append if warnings is not None else (lambda _msg: None)
    parts = re.split(r'\[h3 [^\]]*toc="([^"]+)"[^\]]*\]', markup_text)
    result: dict[int, int] = {}
    hand_pool: list[_Row] = []
    unknown: set[int] = set()
    order = 0

    for i in range(1, len(parts) - 1, 2):
        heading = parts[i].strip()
        armor_slots = _ARMOR_HEADINGS.get(heading.lower())
        hand_kind = None if armor_slots else _hand_section_kind(heading)
        if armor_slots is None and hand_kind is None:
            continue

        rows: list[_Row] = []
        for item, label, o in _section_rows(parts[i + 1], order):
            info = item_info.get(item)
            if info is None:
                unknown.add(item)
                continue
            if player_class and not info.usable_by(player_class):
                continue  # class-restricted (e.g. another class's tier piece)
            rows.append(_Row(item, label, _rank_tier(label), o, hand_kind or ""))
        order += 1000  # keep sections apart in document order

        if hand_kind:
            hand_pool.extend(rows)
            continue

        picks: list[int] = []
        for r in _ranked(rows):
            if r.item in picks:
                continue
            info = item_info.get(r.item)
            if not item_info.fits_slot(info, armor_slots[0]):
                warn(f"[{heading}] {r.item} ({info.name}) doesn't fit the slot, skipped")
                continue
            picks.append(r.item)
        for j, slot in enumerate(armor_slots):
            if j < len(picks) and slot not in result:
                result[slot] = picks[j]

    _resolve_hands(hand_pool, player_class, player_spec, result)

    for item in sorted(unknown):
        warn(f"item {item} is not in the 3.3.5a item_template, skipped")
    return result


def suffix_to_label(suffix: str) -> str:
    rest = re.sub(r"^\d+", "", suffix)
    tokens = re.findall(r"PvE|PvP|P\d+|S\d+|T\d+|BiS|[A-Z][a-z]+\d*", rest)
    return " ".join(tokens) if tokens else suffix


def render_sql(
    player_class: str,
    player_spec: str,
    suffix: str,
    talent_override: str,
    slot_items: dict[int, int],
    category: str = "",
    category_order: int = 0,
) -> str:
    full_spec = f"{player_spec}{suffix}"
    spec_label = suffix_to_label(suffix)
    icon = SPEC_ICONS.get((player_class, player_spec), "inv_misc_questionmark")
    gossip_text = (
        f"|cff00ff00|TInterface\\\\icons\\\\{icon}:30|t|r Use {player_spec} {spec_label}"
    )

    lines: list[str] = []
    lines.append("-- Auto-generated TBC BiS gear")
    lines.append(f"-- class={player_class}, spec={full_spec}")
    lines.append("")
    lines.append(
        "SET @ACTION = COALESCE("
        "(SELECT MAX(`gossipAction`) + 1 FROM `mod_npc_talent_template_index`), 0);"
    )
    lines.append("SET @MINLEVEL = COALESCE(@MINLEVEL, 70);")
    lines.append("SET @MAXLEVEL = COALESCE(@MAXLEVEL, 79);")
    lines.append("SET @RACEMASK_ALL = COALESCE(@RACEMASK_ALL, 1791);")
    lines.append("")
    lines.append(
        "/*!40000 ALTER TABLE `mod_npc_talent_template_index` DISABLE KEYS */;"
    )
    lines.append(
        "INSERT INTO `mod_npc_talent_template_index` "
        "(`playerClass`, `playerSpec`, `gossipAction`, `gossipText`, `mask`, "
        "`minLevel`, `maxLevel`, `glyphOverride`, `talentOverride`, `category`, "
        "`categoryOrder`) VALUES"
    )
    lines.append(
        f"('{player_class}', '{full_spec}', @ACTION+000, '{gossip_text}', "
        f"7, @MINLEVEL, @MAXLEVEL, '{talent_override}', '{talent_override}', '{category}', {category_order}),"
    )
    lines.append(
        f"('{player_class}', '{full_spec}', @ACTION+001, "
        f"'{gossip_text} (Talents and Glyphs only)', "
        f"6, @MINLEVEL, @MAXLEVEL, '{talent_override}', '{talent_override}', '{category}', {category_order});"
    )
    lines.append(
        "/*!40000 ALTER TABLE `mod_npc_talent_template_index` ENABLE KEYS */;"
    )
    lines.append("")

    # Level-70 TBC-era enchants (SpellItemEnchantment ids valid in 3.3.5a),
    # per class/spec, covering every enchantable slot. See era_enchants.py.
    enchants = era_enchants.enchants_for(70, player_class, player_spec)

    gear_rows: list[str] = []
    for inv_slot in sorted(slot_items):
        if inv_slot not in INV_SLOT_TO_POS:
            continue
        pos = INV_SLOT_TO_POS[inv_slot]
        item_id = slot_items[inv_slot]
        enchant = enchants.get(pos, 0)
        info = item_info.get(item_id)
        if info and info.inventory_type == item_info.INV_HOLDABLE:
            enchant = 0  # held-in-off-hand items can't be enchanted
        # Colour-matched TBC gems for the item's sockets (see era_enchants.py).
        s1, s2, s3 = era_enchants.gems_for(70, player_class, player_spec, item_id)
        gear_rows.append(
            f"('{player_class}', '{full_spec}', @RACEMASK_ALL, "
            f"{pos}, {item_id}, {enchant}, {s1}, {s2}, {s3}, 0, 0)"
        )

    if gear_rows:
        lines.append(
            "/*!40000 ALTER TABLE `mod_npc_talent_template_gear` DISABLE KEYS */;"
        )
        lines.append(
            "INSERT INTO `mod_npc_talent_template_gear` "
            "(`playerClass`, `playerSpec`, `playerRaceMask`, `pos`, `itemEntry`, "
            "`enchant`, `socket1`, `socket2`, `socket3`, `bonusEnchant`, "
            "`prismaticEnchant`) VALUES"
        )
        for i, row in enumerate(gear_rows):
            sep = "," if i < len(gear_rows) - 1 else ";"
            lines.append(f"{row}{sep}")
        lines.append(
            "/*!40000 ALTER TABLE `mod_npc_talent_template_gear` ENABLE KEYS */;"
        )

    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract one Wowhead TBC BiS guide page to SQL."
    )
    parser.add_argument("--url", required=True, help="tbc.wowhead.com guide URL")
    parser.add_argument(
        "--player-class", required=True, help='Player class (e.g. "Druid")'
    )
    parser.add_argument(
        "--player-spec", required=True,
        help='Player spec name for SQL (e.g. "Cat", "Bear", "Balance")',
    )
    parser.add_argument(
        "--suffix", default="70PvEP3BiS",
        help="playerSpec suffix (default: 70PvEP3BiS)",
    )
    parser.add_argument(
        "--talent-suffix", default=None,
        help="Talent/glyph override full spec name (default: {player-spec}70PvE)",
    )
    parser.add_argument(
        "--category", default="",
        help="Gossip sub-menu category label (default: empty = root menu)",
    )
    parser.add_argument(
        "--category-order", type=int, default=0,
        help="Sort key of the category in the gossip menu (lower = higher up, default: 0)",
    )
    parser.add_argument(
        "--out", default="out/tbc_generated.sql", help="Output SQL file"
    )
    args = parser.parse_args()

    talent_override = args.talent_suffix or f"{args.player_spec}70PvE"

    page_html = fetch_html(args.url)
    markup = extract_markup_text(page_html)
    warnings: list[str] = []
    slot_items = extract_bis_by_slot(markup, args.player_class, args.player_spec, warnings)
    for w in warnings:
        print(f"  WARN {w}")

    print(f"Extracted {len(slot_items)} gear slots:")
    for inv_slot, item_id in sorted(slot_items.items()):
        print(f"  inv_slot {inv_slot:2d}  pos={INV_SLOT_TO_POS.get(inv_slot, '?'):2}  item={item_id}")

    sql = render_sql(
        player_class=args.player_class,
        player_spec=args.player_spec,
        suffix=args.suffix,
        talent_override=talent_override,
        slot_items=slot_items,
        category=args.category,
        category_order=args.category_order,
    )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(sql, encoding="utf-8")
    print(f"\nWrote SQL: {out_path}")


if __name__ == "__main__":
    main()