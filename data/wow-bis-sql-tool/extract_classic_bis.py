#!/usr/bin/env python3
"""
Extract one Wowhead Classic BiS guide page (classic.wowhead.com) into mod-npc-talent-template SQL rows.

Classic guides use [h4]Slot for Class... headers above [table] blocks.
Rank labels vary: "Best", "BiS", "Best Overall", "Best Mitigation" — all start with Best or BiS.
Some tank guides have no rank labels; extraction falls back to the first item in the table.

Usage (PowerShell from wow-bis-sql-tool/):
  python extract_classic_bis.py \\
    --url "https://classic.wowhead.com/guides/feral-druid-dps-gear-bis-classic-wow" \\
    --player-class "Druid" \\
    --player-spec "Cat" \\
    --suffix "60PvEBiS" \\
    --talent-suffix "Cat60PvE" \\
    --out ".\\out\\classic_feral_cat_bis.sql"
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import sys
import urllib.request
from pathlib import Path

# Shared era-appropriate enchant map (level-60 Classic). Path-loaded so it works
# whether this module is run directly or imported via importlib by the batch tool.
_ee_spec = importlib.util.spec_from_file_location(
    "era_enchants", Path(__file__).parent / "era_enchants.py"
)
era_enchants = importlib.util.module_from_spec(_ee_spec)
sys.modules.setdefault("era_enchants", era_enchants)
_ee_spec.loader.exec_module(era_enchants)


# Level-60 Classic-era enchants now live in era_enchants.py (per class/spec,
# every enchantable slot, SpellItemEnchantment ids verified against 3.3.5a).

# Classic h4 slot key → list of inv_slot numbers (same inv_slot values as TBC/WotLK).
# The slot key is the text BEFORE " for " in the [h4] header (markup tags stripped).
CLASSIC_H4_TO_INV_SLOTS: dict[str, list[int]] = {
    # Body armor
    "Head": [1],
    "Helm": [1],
    "Neck": [2],
    "Shoulders": [3],
    "Shoulder": [3],
    "Back": [15],
    "Chest": [5],
    "Wrist": [9],
    "Wrists": [9],
    "Bracers": [9],
    "Hands": [10],
    "Hand": [10],
    "Waist": [6],
    "Belt": [6],
    "Legs": [7],
    "Feet": [8],
    "Boots": [8],
    # Jewelry
    "Ring": [11, 12],
    "Rings": [11, 12],
    "Finger": [11, 12],
    "Fingers": [11, 12],
    "Trinket": [13, 14],
    "Trinkets": [13, 14],
    # Weapons — main hand
    "Weapon": [16],
    "Weapons": [16],           # Feral Druid (1 weapon slot, staff/2H)
    "Main Hand": [16],
    "Main Hands": [16],
    "Main-Hand": [16],
    "Mainhand": [16],
    "Main Hand Weapon": [16],
    "Main Hand Weapons": [16],
    "Main-Hand Weapon": [16],
    "Main-Hand Weapons": [16],
    "Mainhand Weapons": [16],
    "Main Hand Swords": [16],
    "Main Hand Daggers": [16],
    "One Hand": [16],
    "One-Hand": [16],
    "One Handed": [16],
    "One-Handed": [16],
    "One Hand Weapons": [16],
    "One-Hand Weapons": [16],
    "One Handed Weapons": [16],
    "One-Handed Weapons": [16],
    "1-Handed Weapons": [16],
    "1H Weapon": [16],
    "1H Weapons": [16],
    "Two Hand": [16],
    "Two-Hand": [16],
    "Two Handed": [16],
    "Two-Handed": [16],
    "Two Hand Weapons": [16],
    "Two-Hand Weapons": [16],
    "Two Handed Weapons": [16],
    "Two-Handed Weapons": [16],
    "2-Handed Weapons": [16],
    "2H Weapon": [16],
    "2H Weapons": [16],
    "Staff": [16],
    "Staves": [16],
    # Dual-wield / shared headers (covers Sham Enh, Rogue, Hunter melee)
    "Dual Wield Weapons": [16],
    "Duel Wield Weapons": [16],  # Wowhead typo seen in P5 guides
    "One Hand / Duel Wield Weapons": [16],
    "One Hand / Dual Wield Weapons": [16],
    # Weapons — off hand
    "Off Hand": [17],
    "Off-Hand": [17],
    "Off Hands": [17],
    "Off-Hands": [17],
    "Off hands": [17],
    "Offhand": [17],
    "Off Hand Weapon": [17],
    "Off Hand Weapons": [17],
    "Off-Hand Weapon": [17],
    "Off-Hand Weapons": [17],
    "Off-Hand Items": [17],
    "Off-Hand Swords": [17],
    "Off-Hand Daggers": [17],
    "Off-Hands and Shields": [17],
    "Offhands and Shields": [17],
    "Shield/Offhand": [17],
    "Shield / Offhand": [17],
    "Offhand Weapons": [17],
    "Offhands": [17],
    "Shield": [17],
    "Shields": [17],
    # Combined sections (fill MH and the secondary slot from the same table set)
    "Weapons and Idols": [16, 18],
    "Weapons and Off hands": [16, 17],
    "Weapons and Off-Hands": [16, 17],
    "Weapons and Offhands": [16, 17],
    # Ranged / relic
    "Ranged": [18],
    "Ranged Weapons": [18],
    "Guns and Bows": [18],
    "Wand": [18],
    "Wands": [18],
    "Relic": [18],
    "Relics": [18],
    "Idol": [18],
    "Idols": [18],
    "Totem": [18],
    "Totems": [18],
    "Libram": [18],
    "Librams": [18],
}

# inv_slot (1-based) → pos (0-based column in mod_npc_talent_template_gear)
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
    ("Warrior", "Fury"):          "ability_warrior_innerrage",
    ("Warrior", "Protection"):    "ability_warrior_defensivestance",
}

# Header words that indicate an intro/example/group [h3/h4] section, not a gear slot.
# Matched via .startswith() on the lowercased slot key.
_SKIP_H4_PREFIXES = (
    "best in slot example",
    "raid best in slot example",
    "about the author",
    "p4 bis",
    "p3 bis",
    "p5 bis",
    "p6 bis",
    "true",
    "no ranking",
    "protection warrior",
    "phase",
    # Parent-category headers that group sub-sections (e.g. Paladin Tank P6 page).
    "tier set",
    "tier 3",
    "body armor",
    "jewelry",
    "weapons and shields",
    "weapons and offhands",
    "weapons and off-hands",
    # Resistance gear sections — not BiS slots.
    "nature resistance",
    "frost resistance",
    "fire resistance",
    "shadow resistance",
    "arcane resistance",
    "holy resistance",
    "resistance gear",
    # Stat/concept commentary sections (have tables but no gear).
    "hit cap",
    "weapon skill",
    "set bonus",
    "set bonuses",
    "spell penetration",
    # PvP gearset notes (Wowhead inserts these alongside PvE).
    "hunter pvp set",
    "absolute bis phase",
    "note on",
    # Alternate-set variants (e.g. "Restoration Druid - Best In Slot (5/8 T2)").
    "restoration druid - best",
    "restoration shaman - best",
)


def fetch_html(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=45) as resp:
        return resp.read().decode("utf-8", "ignore")


def extract_markup_text(page_html: str) -> str:
    """Decode the WH.markup.printHtml payload from a Classic guide page."""
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


def _slot_key_from_h4(raw_header: str) -> str:
    """Strip markup tags and extract the slot key (before ' for ')."""
    # Strip all [tag] and [/tag] markup
    clean = re.sub(r"\[[^\]]*\]", "", raw_header).strip()
    # Slot key is before " for " if present, otherwise the whole string
    return re.split(r"\s+for\s+", clean, maxsplit=1)[0].strip()


def _first_item_in_row(row_html: str) -> int | None:
    """Return the first [item=N] found anywhere in the row."""
    m = re.search(r"\[item=(\d+)", row_html)
    return int(m.group(1)) if m else None


_HEADER_WORDS = frozenset({"rank", "priority", "item", "source", "location"})


def _extract_bis_items(tables: list[str], n: int) -> list[int]:
    """
    Collect up to n BiS item IDs from a list of [table] content strings.
    Pass 1: rows with 'Best*' or 'BiS*' rank labels.
    Pass 2 (fallback): first non-header row item if no ranked rows found.
    """
    bis_items: list[int] = []

    for table_content in tables:
        if len(bis_items) >= n:
            break
        rows = re.findall(r"\[tr\](.*?)\[/tr\]", table_content, re.S)
        for row in rows:
            if len(bis_items) >= n:
                break
            tds = re.findall(r"\[td[^\]]*\](.*?)\[/td\]", row, re.S)
            if len(tds) < 2:
                continue
            rank_text = re.sub(r"\[[^\]]*\]", "", tds[0]).strip()
            if not re.match(r"^(Best|BiS)", rank_text, re.I):
                continue
            item_m = re.search(r"\[item=(\d+)", tds[1])
            if item_m:
                bis_items.append(int(item_m.group(1)))

    if not bis_items:
        for table_content in tables:
            rows = re.findall(r"\[tr\](.*?)\[/tr\]", table_content, re.S)
            for row in rows:
                tds = re.findall(r"\[td[^\]]*\](.*?)\[/td\]", row, re.S)
                if not tds:
                    continue
                rank_text = re.sub(r"\[[^\]]*\]", "", tds[0]).strip().lower()
                if rank_text in _HEADER_WORDS:
                    continue
                item_id = _first_item_in_row(row)
                if item_id:
                    bis_items.append(item_id)
                    break
            if bis_items:
                break

    return bis_items


def _fill_slots(
    result: dict[int, int],
    slots: list[int],
    tables: list[str],
    force: bool = False,
) -> None:
    """Fill empty slot entries in result from tables, positionally.

    The i-th BiS item fills the i-th slot, so an already-filled leading slot
    (e.g. main-hand set by a separate "Weapons" section) does not cause a later
    slot (e.g. relic/off-hand from a combined "Weapons and Idols" section) to be
    filled with the wrong item. If the table BiS-tags fewer items than slots, the
    trailing slots are left empty rather than reusing an earlier item — a relic
    slot with no idol in the guide stays empty instead of duplicating the weapon.

    `force=True` overwrites an already-filled slot. Used for a dedicated "Shields"
    section so a shield wins the off-hand slot over an "Off-Hand Weapons" section
    that a tank guide also lists (whichever appeared first would otherwise win).
    """
    if not force and all(slot in result for slot in slots):
        return
    items = _extract_bis_items(tables, len(slots))
    for i, slot in enumerate(slots):
        if slot in result and not force:
            continue
        if i < len(items) and items[i] is not None:
            result[slot] = items[i]


# Dedicated shield headings: a shield wins the off-hand slot over an
# "Off-Hand Weapons" section that tank/1H guides also list.
_SHIELD_TOC = {"shield", "shields"}


def _handle_section(
    result: dict[int, int],
    slot_key: str,
    tables: list[str],
    source_tag: str,
) -> None:
    """Apply skip rules + dict lookup; warn on unmatched headers that have tables."""
    if slot_key.lower().startswith(_SKIP_H4_PREFIXES):
        return
    slots = CLASSIC_H4_TO_INV_SLOTS.get(slot_key)
    if slots is None:
        if tables:
            print(
                f"  [warn] unmatched {source_tag} header: {slot_key!r} "
                f"({len(tables)} table(s)) — add to CLASSIC_H4_TO_INV_SLOTS "
                f"or _SKIP_H4_PREFIXES",
                file=sys.stderr,
            )
        return
    _fill_slots(result, slots, tables, force=slot_key.strip().lower() in _SHIELD_TOC)


def extract_bis_by_slot(markup_text: str) -> dict[int, int]:
    """
    Parse Classic guide markup into {inv_slot: item_id}.

    Three passes:
    1. [h4]Slot for Class[/h4] + tables — used by most specs for all slots.
    2. [h3 toc="..."]Title for Class[/h3] + tables — used by some specs for
       weapon slots (e.g. mage has h3 sections for Weapons/Offhands/Wands).
    3. [h3]Slot[/h3] + tables — plain h3 (e.g. Naxx-era guides).

    Unmatched headers with at least one table are logged to stderr so that
    future Wowhead reorganizations surface as warnings rather than silent gaps.

    For Rings and Trinkets the first two Best/BiS items fill both slot entries.
    Falls back to the first table row when no ranked row exists (tank guides).
    """
    result: dict[int, int] = {}

    # --- Pass 1: [h4] sections ---
    parts = re.split(r"\[h4[^\]]*\]", markup_text)
    for part in parts[1:]:
        m = re.match(r"^(.*?)\[/h4\]", part, re.S)
        if not m:
            continue
        slot_key = _slot_key_from_h4(m.group(1).strip())
        tables = re.findall(r"\[table[^\]]*\](.*?)\[/table\]", part[m.end():], re.S)
        _handle_section(result, slot_key, tables, "h4")

    # --- Pass 2: [h3 toc=...] sections — handles both quoted and unquoted toc values.
    # Quoted:   [h3 toc="One-Hand Weapons"]
    # Unquoted: [h3 toc=Neck]  or  [h3 toc= Bracers]  (space before value)
    h3_parts = re.split(r'\[h3 [^\]]*toc=(?:"[^"]*"|\s*[^"\s\]]+)[^\]]*\]', markup_text)
    for h3_part in h3_parts[1:]:
        h3_end = h3_part.find("[/h3]")
        if h3_end < 0:
            continue
        slot_key = _slot_key_from_h4(h3_part[:h3_end])
        after_h3 = h3_part[h3_end + 5:]
        tables = re.findall(r"\[table[^\]]*\](.*?)\[/table\]", after_h3, re.S)
        _handle_section(result, slot_key, tables, "h3-toc")

    # --- Pass 3: plain [h3] sections (no toc= attribute) ---
    # Used by some newer Classic guide pages (e.g. Hunter, Rogue, Paladin P2).
    h3plain_parts = re.split(r"\[h3\]", markup_text)
    for part in h3plain_parts[1:]:
        h3_end = part.find("[/h3]")
        if h3_end < 0:
            continue
        slot_key = _slot_key_from_h4(part[:h3_end])
        after_h3 = part[h3_end + 5:]
        tables = re.findall(r"\[table[^\]]*\](.*?)\[/table\]", after_h3, re.S)
        _handle_section(result, slot_key, tables, "h3")

    return result


def suffix_to_label(suffix: str) -> str:
    rest = re.sub(r"^\d+", "", suffix)
    tokens = re.findall(r"PvE|PvP|BiS|[A-Z][a-z]+\d*", rest)
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
    lines.append("-- Auto-generated Classic BiS gear")
    lines.append(f"-- class={player_class}, spec={full_spec}")
    lines.append("")
    lines.append(
        "SET @ACTION = COALESCE("
        "(SELECT MAX(`gossipAction`) + 1 FROM `mod_npc_talent_template_index`), 0);"
    )
    lines.append("SET @MINLEVEL = COALESCE(@MINLEVEL, 60);")
    lines.append("SET @MAXLEVEL = COALESCE(@MAXLEVEL, 69);")
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

    # Level-60 Classic-era enchants (SpellItemEnchantment ids valid in 3.3.5a),
    # per class/spec, covering every enchantable slot. See era_enchants.py.
    enchants = era_enchants.enchants_for(60, player_class, player_spec)

    gear_rows: list[str] = []
    for inv_slot in sorted(slot_items):
        if inv_slot not in INV_SLOT_TO_POS:
            continue
        pos = INV_SLOT_TO_POS[inv_slot]
        item_id = slot_items[inv_slot]
        enchant = enchants.get(pos, 0)
        gear_rows.append(
            f"('{player_class}', '{full_spec}', @RACEMASK_ALL, "
            f"{pos}, {item_id}, {enchant}, 0, 0, 0, 0, 0)"
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
        description="Extract one Wowhead Classic BiS guide page to SQL."
    )
    parser.add_argument("--url", required=True, help="classic.wowhead.com guide URL")
    parser.add_argument(
        "--player-class", required=True, help='Player class (e.g. "Druid")'
    )
    parser.add_argument(
        "--player-spec", required=True,
        help='Player spec name for SQL (e.g. "Cat", "Bear", "Fire")',
    )
    parser.add_argument(
        "--suffix", default="60PvEBiS",
        help="playerSpec suffix (default: 60PvEBiS)",
    )
    parser.add_argument(
        "--talent-suffix", default=None,
        help="Talent/glyph override full spec name (default: {player-spec}60PvE)",
    )
    parser.add_argument(
        "--category", default="",
        help="Gossip sub-menu category label (default: empty = root menu)",
    )
    parser.add_argument(
        "--out", default="out/classic_generated.sql", help="Output SQL file"
    )
    args = parser.parse_args()

    talent_override = args.talent_suffix or f"{args.player_spec}60PvE"

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