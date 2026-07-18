#!/usr/bin/env python3
"""
Extract one Wowhead TBC BiS guide page (tbc.wowhead.com) into mod-npc-talent-template SQL rows.

TBC guides use Wowhead markup with [h3 toc="SlotName"] + [table] structure rather than
gear-planner hashes. Enchants and gems default to 0 (no hash to decode them from).

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
import urllib.request
from pathlib import Path

# Shared era-appropriate enchant map (level-70 TBC). Path-loaded so it works
# whether this module is run directly or imported via importlib by the batch tool.
_ee_spec = importlib.util.spec_from_file_location(
    "era_enchants", Path(__file__).parent / "era_enchants.py"
)
era_enchants = importlib.util.module_from_spec(_ee_spec)
sys.modules.setdefault("era_enchants", era_enchants)
_ee_spec.loader.exec_module(era_enchants)

# Level-70 TBC-era enchants now live in era_enchants.py (per class/spec, every
# enchantable slot, SpellItemEnchantment ids verified against 3.3.5a).
# Gear sockets (socket1/2/3) stay 0 — gems are per-item, not derivable per spec.

# ---------------------------------------------------------------------------

# Wowhead markup h3 toc heading → list of inv_slot numbers.
# For multi-slot sections (Rings, Trinkets) the list has 2 entries; the
# extractor assigns the 1st BiS item to slots[0], 2nd BiS item to slots[1].
TOC_TO_INV_SLOTS: dict[str, list[int]] = {
    "Head": [1],
    "Neck": [2],
    "Shoulder": [3],
    "Shoulders": [3],
    "Back": [15],
    "Chest": [5],
    "Wrist": [9],
    "Wrists": [9],
    "Hand": [10],
    "Hands": [10],
    "Gloves": [10],
    "Waist": [6],
    "Legs": [7],
    "Feet": [8],
    "Ring": [11, 12],
    "Rings": [11, 12],
    "Finger": [11, 12],
    "Fingers": [11, 12],
    "Trinket": [13, 14],
    "Trinkets": [13, 14],
    "Main Hand": [16],
    "Mainhand": [16],
    "Main Hand Weapon": [16],
    "Main Hand Weapons": [16],
    "Main-Hand Weapon": [16],
    "Main-Hand Weapons": [16],
    "Mainhand Weapons": [16],
    "Off Hand": [17],
    "Offhand": [17],
    "Off Hand Weapon": [17],
    "Off Hand Weapons": [17],
    "Off-Hand Weapon": [17],
    "Off-Hand Weapons": [17],
    "Shield": [17],
    # Weapons with only one item → main hand; with two → main + off
    "Weapon": [16, 17],
    "Weapons": [16, 17],
    "1H Weapon": [16],
    "1H Weapons": [16],
    "One Hand Weapons": [16],
    "One-Hand Weapons": [16],
    "One Handed Weapons": [16],
    "One-Handed Weapons": [16],
    "2H Weapon": [16],
    "2H Weapons": [16],
    "Two Hand Weapons": [16],
    "Two-Hand Weapons": [16],
    "Two Handed Weapons": [16],
    "Two-Handed Weapons": [16],
    "Staff": [16],
    "Staves": [16],
    "Polearm": [16],
    "Polearms": [16],
    "Offhands": [17],
    "Shield": [17],
    "Shields": [17],
    # Relics (class-specific ranged slot)
    "Idol": [18],
    "Idols": [18],
    "Totem": [18],
    "Totems": [18],
    "Libram": [18],
    "Librams": [18],
    "Relic": [18],
    "Relics": [18],
    "Ranged": [18],
    "Ranged Weapon": [18],
    "Ranged Weapons": [18],
    "Wand": [18],
    "Wands": [18],
    "Thrown": [18],
    "Thrown Weapon": [18],
    "Thrown Weapons": [18],
    "Bow": [18],
    "Bows": [18],
    "Gun": [18],
    "Guns": [18],
    "Crossbow": [18],
    "Crossbows": [18],
}

# Headings whose main-hand pick is a two-handed weapon: it occupies both hands,
# so any off-hand the guide also lists (some pages carry a dual-wield alternative
# section too) must not be applied on top of it.
_TWO_HANDED_TOC = {
    k.lower() for k in (
        "2H Weapon", "2H Weapons",
        "Two Hand Weapons", "Two-Hand Weapons",
        "Two Handed Weapons", "Two-Handed Weapons",
        "Staff", "Staves", "Polearm", "Polearms",
    )
}

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
}


def fetch_html(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=45) as resp:
        return resp.read().decode("utf-8", "ignore")


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


def extract_bis_by_slot(markup_text: str) -> dict[int, int]:
    """
    Parse [h3 toc="SlotName"] + [table] markup.

    Returns {inv_slot: item_id} with the best BiS item per slot.
    For Rings and Trinkets the first two BiS items fill the two slot entries.
    """
    parts = re.split(r'\[h3 [^\]]*toc="([^"]+)"[^\]]*\]', markup_text)
    result: dict[int, int] = {}
    filled_by: dict[int, str] = {}  # slot -> heading that filled it (for the 2H rule)

    for i in range(1, len(parts) - 1, 2):
        heading = parts[i].strip()  # strip leading/trailing whitespace
        content = parts[i + 1]

        slots = None
        for key, val in TOC_TO_INV_SLOTS.items():
            if key.lower() == heading.lower():
                slots = val
                break
        if slots is None:
            continue

        tables = re.findall(r"\[table[^\]]*\](.*?)\[/table\]", content, re.S)
        bis_items: list[int] = []
        first_items: list[int] = []  # fallback: first data-row item per table
        for table_content in tables:
            # Tolerate a malformed row-closing tag ("[/tr}" appears in some guides).
            rows = re.findall(r"\[tr\](.*?)\[/tr[\]\}]", table_content, re.S)
            table_first: int | None = None
            for row in rows:
                tds = re.findall(r"\[td[^\]]*\](.*?)\[/td\]", row, re.S)
                if len(tds) < 2:
                    continue
                m = re.search(r"\[item=(\d+)", tds[1])
                if not m:
                    continue  # header/label row (no item link)
                item = int(m.group(1))
                if table_first is None:
                    table_first = item
                rank_text = re.sub(r"\[[^\]]+\]", "", tds[0]).strip()
                if re.match(r"^(BiS|Best)", rank_text, re.I):
                    bis_items.append(item)
            if table_first is not None:
                first_items.append(table_first)

        # Some guides (esp. tank/healer trinket & weapon tables) rank rows by stat
        # ("51 Stam + Proc", …) with no BiS/Best label. If nothing was labelled,
        # fall back to the first data row of each table (guides list the top pick first).
        picks = bis_items if bis_items else first_items

        for j, slot in enumerate(slots):
            if j < len(picks) and slot not in result:
                result[slot] = picks[j]
                filled_by[slot] = heading

    # A two-handed main-hand occupies both hands: drop any off-hand that a
    # secondary section may have added (e.g. an Arms guide that also lists a
    # dual-wield alternative under "Off Hand Weapons").
    if filled_by.get(16, "").lower() in _TWO_HANDED_TOC and 17 in result:
        del result[17]
        filled_by.pop(17, None)

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
        "`minLevel`, `maxLevel`, `glyphOverride`, `talentOverride`, `category`) VALUES"
    )
    lines.append(
        f"('{player_class}', '{full_spec}', @ACTION+000, '{gossip_text}', "
        f"7, @MINLEVEL, @MAXLEVEL, '{talent_override}', '{talent_override}', '{category}'),"
    )
    lines.append(
        f"('{player_class}', '{full_spec}', @ACTION+001, "
        f"'{gossip_text} (Talents and Glyphs only)', "
        f"6, @MINLEVEL, @MAXLEVEL, '{talent_override}', '{talent_override}', '{category}');"
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
        "--out", default="out/tbc_generated.sql", help="Output SQL file"
    )
    args = parser.parse_args()

    talent_override = args.talent_suffix or f"{args.player_spec}70PvE"

    page_html = fetch_html(args.url)
    markup = extract_markup_text(page_html)
    slot_items = extract_bis_by_slot(markup)

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
    )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(sql, encoding="utf-8")
    print(f"\nWrote SQL: {out_path}")


if __name__ == "__main__":
    main()