#!/usr/bin/env python3
"""
Extract one Wowhead WotLK BiS guide page into mod-npc-talent-template SQL rows.

V1 scope:
- single URL
- index + gear rows
- reuse existing talent/glyph override names from base SQL style
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import urllib.request
from dataclasses import dataclass, replace
from pathlib import Path


INV_SLOT_TO_POS = {
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
    18: 17, # ranged/relic
}


# Alliance item ID → Horde item ID substitution table.
# Applied when building Horde rows for specs whose Wowhead guide only has an
# Alliance gear planner tab (so Horde players get the correct faction items).
# Pairs are matched by: same slot, same ilvl, same AllowableClass, best name overlap.
# Also includes the inverse (Horde → Alliance) for guides written from a Horde POV.
ALLIANCE_TO_HORDE: dict[int, int] = {
    # ── Quel'Delar quest rewards (ilvl 245 normal / 258 heroic) ──────────────
    47092: 47293,  # Boots of the Mourning Widow (norm)    → Sandals of the Mourning Widow
    47097: 47454,  # Boots of the Mourning Widow (heroic)  → Sandals of the Mourning Widow
    47203: 47324,  # Armbands of the Ashen Saint (norm)    → Bindings of the Ashen Saint
    47208: 47485,  # Armbands of the Ashen Saint (heroic)  → Bindings of the Ashen Saint
    47138: 47276,  # Chalice of Searing Light (norm)       → Talisman of Heedless Sins
    47146: 47437,  # Chalice of Searing Light (heroic)     → Talisman of Heedless Sins
    47053: 47309,  # Symbol of Transgression (norm)        → Mystifying Charm
    47064: 47470,  # Symbol of Transgression (heroic)      → Mystifying Charm
    47958: 48032,  # Chalice of Benedictus (norm)          → Lightbane Focus
    # ── Quel'Delar quest rewards (ilvl 272, back slot) ───────────────────────
    47552: 47551,  # Jaina's Radiance                      → Aethas' Intensity
    47553: 47554,  # Bolvar's Devotion                     → Lady Liadrin's Conviction
    47545: 47546,  # Vereesa's Dexterity                   → Sylvanas' Cunning
    47549: 47550,  # Magni's Resolution                    → Cairne's Endurance
    # ── Trial of the Crusader trinkets (ilvl 258 heroic) ─────────────────────
    47131: 47464,  # Death's Verdict                       → Death's Choice
    47059: 47432,  # Solace of the Defeated                → Solace of the Fallen
    47088: 47432,  # Satrina's Impeding Scarab             → Solace of the Fallen
    47188: 47477,  # Reign of the Unliving                 → Reign of the Dead
    # ── Trial of the Crusader misc drops (ilvl 258 heroic) ───────────────────
    46965: 47412,  # Breastplate of Cruel Intent           → Cuirass of Cruel Intent
    46966: 47413,  # Band of the Violent Temperment        → Ring of the Violent Temperament
    46968: 47415,  # Chestplate of the Towering Monstrosity→ Hauberk of the Towering Monstrosity
    46975: 47420,  # Leggings of the Broken Beast          → Legwraps of the Broken Beast
    46977: 47417,  # Shawl of the Refreshing Winds         → Drape of the Refreshing Winds
    46993: 47425,  # Flowing Vestments of Ascent           → Flowing Robes of Ascent
    46995: 47428,  # Talonstrike                           → Death's Head Crossbow
    47002: 47429,  # Bloodbath Belt                        → Bloodbath Girdle
    47003: 47430,  # Dawnbreaker Greaves                   → Dawnbreaker Sabatons
    47004: 47431,  # Cuirass of Calamitous Fate            → Vest of Calamitous Fate
    47060: 47433,  # Charge of the Demon Lord              → Charge of the Eredar
    47061: 47434,  # Legguards of Feverish Dedication      → Legplates of Feverish Dedication
    47066: 47438,  # Bracers of the Autumn Willow          → Bindings of the Autumn Willow
    47074: 47442,  # Bracers of the Untold Massacre        → Bracers of the Silent Massacre
    47075: 47443,  # Ring of Callous Aggression            → Band of Callous Aggression
    47076: 47444,  # Girdle of Bloodied Scars              → Belt of Bloodied Scars
    47077: 47445,  # Treads of the Icewalker               → Icewalker Treads
    47078: 47446,  # Justicebringer                        → Dual-blade Butcher
    47111: 47459,  # Bracers of the Shieldmaiden           → Armguards of the Shieldmaiden
    47112: 47460,  # Belt of the Merciless Killer          → Belt of the Pitiless Killer
    47130: 47463,  # Lupine Longstaff                      → Twin's Pact
    47133: 47468,  # The Arbiter's Muse                    → Cry of the Val'kyr
    47145: 47469,  # Cord of Pale Thorns                   → Belt of Pale Thorns
    47153: 47472,  # Belt of Deathly Dominion              → Waistguard of Deathly Dominion
    47154: 47473,  # Greaves of the 7th Legion             → Greaves of the Saronite Citadel
    47157: 47476,  # Signet of the Traitor King            → Band of the Traitor King
    47189: 47478,  # Leggings of the Deepening Void        → Breeches of the Deepening Void
    47191: 47480,  # Legguards of the Lurking Threat       → Leggings of the Lurking Threat
    47206: 47483,  # Misery's End                          → Suffering's End
    47224: 47439,  # Ring of the Darkmender                → Circle of the Darkmender
    47239: 47491,  # Archon Glaive                         → Hellion Glaive
    47240: 47492,  # Gloves of Bitter Reprisal             → Gauntlets of Bitter Reprisal
    47506: 47513,  # Silverwing Defender                   → Orgrim's Deflector
    47515: 47516,  # Decimation                            → Fleshrender
    # ── Trial of the Champion 5-man quest items ──────────────────────────────
    47698: 47699,  # Shoulderplates of Enduring Order      → Shoulderguards of Enduring Order
    47915: 47988,  # Collar of Ceaseless Torment           → Collar of Unending Torment
    47922: 47995,  # Rod of Imprisoned Souls               → Scepter of Imprisoned Souls
    47930: 48003,  # Amulet of Binding Elements            → Pendant of Binding Elements
    47934: 48007,  # Planestalker Signet                   → Planestalker Band
    47945: 48017,  # Gloves of the Silver Assassin         → Sunreaver Assassin's Gloves
    47955: 48027,  # Loop of the Twin Val'kyr              → Band of the Twin Val'kyr
    # ── Crafted items (same name, different faction ID) ──────────────────────
    47585: 47586,  # Bejeweled Wizard's Bracers (A)        → Bejeweled Wizard's Bracers (H)
    47587: 47588,  # Royal Moonshroud Bracers (A)          → Royal Moonshroud Bracers (H)
    47603: 47604,  # Merlin's Robe (A)                     → Merlin's Robe (H)
    # ── Tier 9 — Priest (Velen's → Zabra's) ─────────────────────────────────
    47983: 48066,  # Velen's Gloves of Triumph (245)       → Zabra's Gloves of Triumph
    47985: 48064,  # Velen's Leggings of Triumph (245)     → Zabra's Leggings of Triumph
    47984: 48065,  # Velen's Cowl of Triumph (245)         → Zabra's Cowl of Triumph
    47986: 48063,  # Velen's Robe of Triumph (245)         → Zabra's Robe of Triumph
    47987: 48062,  # Velen's Shoulderpads of Triumph (245) → Zabra's Shoulderpads of Triumph
    48029: 48061,  # Velen's Shoulderpads of Triumph (258) → Zabra's Shoulderpads of Triumph
    48031: 48060,  # Velen's Robe of Triumph (258)         → Zabra's Robe of Triumph
    48033: 48059,  # Velen's Leggings of Triumph (258)     → Zabra's Leggings of Triumph
    48035: 48058,  # Velen's Cowl of Triumph (258)         → Zabra's Cowl of Triumph
    48037: 48057,  # Velen's Gloves of Triumph (258)       → Zabra's Gloves of Triumph
    48077: 48087,  # Velen's Handwraps of Triumph (245)    → Zabra's Handwraps of Triumph
    48078: 48088,  # Velen's Circlet of Triumph (245)      → Zabra's Circlet of Triumph
    48079: 48089,  # Velen's Pants of Triumph (245)        → Zabra's Pants of Triumph
    48080: 48090,  # Velen's Raiments of Triumph (245)     → Zabra's Raiments of Triumph
    48081: 48092,  # Velen's Mantle of Triumph (245)       → Zabra's Mantle of Triumph (245)
    48082: 48091,  # Velen's Mantle of Triumph (258)       → Zabra's Mantle of Triumph (258)
    48083: 48090,  # Velen's Raiments of Triumph (258)     → Zabra's Raiments of Triumph
    48084: 48089,  # Velen's Pants of Triumph (258)        → Zabra's Pants of Triumph
    48085: 48095,  # Velen's Circlet of Triumph (258)      → Zabra's Circlet of Triumph
    48086: 48096,  # Velen's Handwraps of Triumph (258)    → Zabra's Handwraps of Triumph
    # ── Tier 9 — Druid (Malfurion's → Runetotem's) ───────────────────────────
    48133: 48152,  # Malfurion's Handguards of Triumph (245) → Runetotem's Handguards
    48134: 48151,  # Malfurion's Headpiece of Triumph (245)  → Runetotem's Headpiece
    48135: 48150,  # Malfurion's Leggings of Triumph (245)   → Runetotem's Leggings
    48137: 48148,  # Malfurion's Spaulders of Triumph (245)  → Runetotem's Spaulders
    48139: 48146,  # Malfurion's Robe of Triumph (258)       → Runetotem's Robe
    48140: 48145,  # Malfurion's Leggings of Triumph (258)   → Runetotem's Leggings
    48141: 48144,  # Malfurion's Headpiece of Triumph (258)  → Runetotem's Headpiece
    48142: 48143,  # Malfurion's Handguards of Triumph (258) → Runetotem's Handguards
    48138: 48147,  # Malfurion's Spaulders of Triumph (258)  → Runetotem's Spaulders
    48163: 48173,  # Malfurion's Gloves of Triumph (245)     → Runetotem's Gloves
    48164: 48174,  # Malfurion's Cover of Triumph (245)      → Runetotem's Cover
    48165: 48175,  # Malfurion's Trousers of Triumph (245)   → Runetotem's Trousers
    48166: 48176,  # Malfurion's Vestments of Triumph (245)  → Runetotem's Vestments
    48167: 48178,  # Malfurion's Mantle of Triumph (245)     → Runetotem's Mantle (245)
    48168: 48177,  # Malfurion's Mantle of Triumph (258)     → Runetotem's Mantle (258)
    48169: 48176,  # Malfurion's Vestments of Triumph (258)  → Runetotem's Vestments
    48170: 48175,  # Malfurion's Trousers of Triumph (258)   → Runetotem's Trousers
    48171: 48174,  # Malfurion's Cover of Triumph (258)      → Runetotem's Cover
    48172: 48173,  # Malfurion's Gloves of Triumph (258)     → Runetotem's Gloves
    48203: 48202,  # Malfurion's Handgrips of Triumph (258)  → Runetotem's Handgrips
    48204: 48201,  # Malfurion's Headguard of Triumph (258)  → Runetotem's Headguard
    48205: 48200,  # Malfurion's Legguards of Triumph (258)  → Runetotem's Legguards
    48206: 48199,  # Malfurion's Raiments of Triumph (258)   → Runetotem's Raiments
    48207: 48198,  # Malfurion's Shoulderpads of Triumph (258)→ Runetotem's Shoulderpads
    48208: 48197,  # Malfurion's Shoulderpads of Triumph (245)→ Runetotem's Shoulderpads
    48209: 48196,  # Malfurion's Raiments of Triumph (245)   → Runetotem's Raiments
    48210: 48195,  # Malfurion's Legguards of Triumph (245)  → Runetotem's Legguards
    48211: 48194,  # Malfurion's Headguard of Triumph (245)  → Runetotem's Headguard
    48212: 48193,  # Malfurion's Handgrips of Triumph (245)  → Runetotem's Handgrips
    # ── Tier 9 — Warrior (Wrynn's → Hellscream's) ────────────────────────────
    48376: 48391,  # Wrynn's Battleplate of Triumph (245)    → Hellscream's Battleplate
    48377: 48392,  # Wrynn's Gauntlets of Triumph (245)      → Hellscream's Gauntlets
    48378: 48393,  # Wrynn's Helmet of Triumph (245)         → Hellscream's Helmet
    48379: 48394,  # Wrynn's Legplates of Triumph (245)      → Hellscream's Legplates
    48380: 48395,  # Wrynn's Shoulderplates of Triumph (245) → Hellscream's Shoulderplates
    48381: 48400,  # Wrynn's Shoulderplates of Triumph (258)→ Hellscream's Shoulderplates
    48382: 48399,  # Wrynn's Legplates of Triumph (258)     → Hellscream's Legplates
    48383: 48398,  # Wrynn's Helmet of Triumph (258)        → Hellscream's Helmet
    48385: 48396,  # Wrynn's Battleplate of Triumph (258)   → Hellscream's Battleplate
    48430: 48463,  # Wrynn's Greathelm of Triumph (245)      → Hellscream's Greathelm
    48433: 48468,  # Wrynn's Greathelm of Triumph (258)      → Hellscream's Greathelm
    48446: 48464,  # Wrynn's Legguards of Triumph (245)      → Hellscream's Legguards
    48447: 48469,  # Wrynn's Legguards of Triumph (258)      → Hellscream's Legguards
    48450: 48461,  # Wrynn's Breastplate of Triumph (245)    → Hellscream's Breastplate
    48451: 48466,  # Wrynn's Breastplate of Triumph (258)    → Hellscream's Breastplate
    48452: 48462,  # Wrynn's Handguards of Triumph (245)     → Hellscream's Handguards
    48453: 48467,  # Wrynn's Handguards of Triumph (258)     → Hellscream's Handguards
    48454: 48465,  # Wrynn's Pauldrons of Triumph (245)      → Hellscream's Pauldrons
    48455: 48470,  # Wrynn's Pauldrons of Triumph (258)      → Hellscream's Pauldrons
}

# Inverse map: Horde-only items → Alliance-only items (for guides written from a Horde POV).
HORDE_TO_ALLIANCE: dict[int, int] = {v: k for k, v in ALLIANCE_TO_HORDE.items()}


def apply_faction_swap(
    slots: dict[int, "SlotRecord"], to_horde: bool
) -> dict[int, "SlotRecord"]:
    """Replace faction-locked items with the correct faction's version."""
    result: dict[int, SlotRecord] = {}
    swap = ALLIANCE_TO_HORDE if to_horde else HORDE_TO_ALLIANCE
    for inv_slot, rec in slots.items():
        new_id = swap.get(rec.item, rec.item)
        result[inv_slot] = replace(rec, item=new_id)
    return result


@dataclass
class SlotRecord:
    item: int
    enchant: int = 0
    random_enchant: int = 0
    gems: dict[int, int] | None = None


SPEC_ICONS: dict[tuple[str, str], str] = {
    ("Death Knight", "Blood"):        "spell_deathknight_bloodpresence",
    ("Death Knight", "Frost"):        "spell_deathknight_frostpresence",
    ("Death Knight", "Unholy"):       "spell_deathknight_unholypresence",
    ("Druid",        "Balance"):      "spell_nature_starfall",
    ("Druid",        "Feral"):        "ability_druid_catform",
    ("Druid",        "Restoration"):  "spell_nature_healingtouch",
    ("Hunter",       "Beastmastery"): "ability_hunter_beasttaming",
    ("Hunter",       "Marksmanship"): "ability_marksmanship",
    ("Hunter",       "Survival"):     "ability_Hunter_swiftstrike",
    ("Mage",         "Arcane"):       "spell_holy_magicalsentry",
    ("Mage",         "Fire"):         "spell_fire_flamebolt",
    ("Mage",         "Frost"):        "spell_frost_frostbolt02",
    ("Paladin",      "Holy"):         "spell_holy_holybolt",
    ("Paladin",      "Protection"):   "spell_holy_devotionaura",
    ("Paladin",      "Retribution"):  "spell_holy_auraoflight",
    ("Priest",       "Discipline"):   "spell_holy_wordfortitude",
    ("Priest",       "Holy"):         "spell_holy_holybolt",
    ("Priest",       "Shadow"):       "spell_shadow_shadowwordpain",
    ("Rogue",        "Assassination"):"ability_rogue_eviscerate",
    ("Rogue",        "Combat"):       "ability_backstab",
    ("Rogue",        "Subtlety"):     "ability_stealth",
    ("Shaman",       "Elemental"):    "spell_nature_lightning",
    ("Shaman",       "Enhancement"):  "spell_nature_lightningshield",
    ("Shaman",       "Restoration"):  "spell_nature_magicimmunity",
    ("Warlock",      "Affliction"):   "spell_shadow_deathcoil",
    ("Warlock",      "Demonology"):   "spell_shadow_metamorphosis",
    ("Warlock",      "Destruction"):  "spell_shadow_rainoffire",
    ("Warrior",      "Arms"):         "ability_rogue_eviscerate",
    ("Warrior",      "Fury"):         "ability_warrior_innerrage",
    ("Warrior",      "Protection"):   "ability_warrior_defensivestance",
}

_GEM_ENCHANT_CACHE: dict[int, int] = {}


def fetch_html(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=45) as resp:
        return resp.read().decode("utf-8", "ignore")


def fetch_gem_enchant_id(gem_item_id: int) -> int:
    """Resolve a gem item ID to its SpellItemEnchantment ID via wotlkdb.com."""
    if gem_item_id == 0:
        return 0
    if gem_item_id in _GEM_ENCHANT_CACHE:
        return _GEM_ENCHANT_CACHE[gem_item_id]
    try:
        html = fetch_html(f"https://wotlkdb.com/?item={gem_item_id}")
        m = re.search(r"[?&]enchantment=(\d+)", html)
        result = int(m.group(1)) if m else 0
    except Exception as e:
        print(f"  Warning: could not resolve gem enchant for item {gem_item_id}: {e}")
        result = 0
    if result == 0:
        print(f"  Warning: no enchantment= link found for gem item {gem_item_id} — socket will be 0")
    _GEM_ENCHANT_CACHE[gem_item_id] = result
    return result


# The gear-planner hash encodes a permanent enchant as its enchant *spell* ID
# (Spell.dbc), e.g. 60691 = spell "Enchant 2H Weapon - Massacre". The gear
# template's `enchant` column, however, is consumed by Item::SetEnchantment and
# must be a SpellItemEnchantment.dbc ID (60691 -> 3827). Without this translation
# the enchant silently fails to apply in-game (no SpellItemEnchantment #60691).
#
# Seed map below was resolved via wotlkdb.com (`?spell=N` -> `enchantment=M`) and
# covers every enchant seen across WotLK phases 1-4. Unknown spell IDs fall back
# to a live wotlkdb lookup, so the cache is self-extending.
_SPELL_ENCHANT_SEED: dict[int, int] = {
    13890: 911,    # Enchant Boots - Minor Speed
    13947: 930,    # Enchant Gloves - Riding Skill
    20008: 1883,   # Enchant Bracer - Greater Intellect
    20014: 1888,   # Enchant Cloak - Greater Resistance
    20025: 1891,   # Enchant Chest - Greater Stats
    22749: 2504,   # Enchant Weapon - Spellpower
    22844: 2544,   # Arcanum of Focus
    24421: 2605,   # Zandalar Signet of Mojo
    27968: 2666,   # Enchant Weapon - Major Intellect
    27984: 2673,   # Enchant Weapon - Mongoose
    30258: 2724,   # Stabilized Eternium Scope
    33990: 1144,   # Enchant Chest - Major Spirit
    34009: 1071,   # Enchant Shield - Major Stamina
    44509: 2381,   # Enchant Chest - Greater Mana Restoration
    44555: 1119,   # Enchant Bracers - Exceptional Intellect
    44575: 3845,   # Enchant Bracers - Greater Assault
    44584: 3244,   # Enchant Boots - Greater Vitality
    44592: 3246,   # Enchant Gloves - Exceptional Spellpower
    47672: 3294,   # Enchant Cloak - Mighty Armor
    47898: 3831,   # Enchant Cloak - Greater Speed
    47900: 3297,   # Enchant Chest - Super Health
    47901: 3232,   # Enchant Boots - Tuskarr's Vitality
    50909: 3330,   # Heavy Borean Armor Kit
    53343: 3370,   # Rune of Razorice
    53344: 3368,   # Rune of the Fallen Crusader
    54736: 3599,   # Personal Electromagnetic Pulse Generator
    54793: 3601,   # Frag Belt
    54999: 3604,   # Hyperspeed Accelerators
    55002: 3605,   # Flexweave Underlay
    55016: 3606,   # Nitro Boosts
    55135: 3608,   # Heartseeker Scope
    55631: 3719,   # Brilliant Spellthread
    55634: 3721,   # Sapphire Spellthread
    55642: 3722,   # Lightweave Embroidery
    56039: 3872,   # Sanctified Spellthread
    57690: 3757,   # Fur Lining - Stamina
    57691: 3758,   # Fur Lining - Spell Power
    59619: 3788,   # Enchant Weapon - Accuracy
    59621: 3789,   # Enchant Weapon - Berserking
    59934: 3808,   # Greater Inscription of the Axe
    59936: 3809,   # Greater Inscription of the Crag
    59937: 3810,   # Greater Inscription of the Storm
    59954: 3817,   # Arcanum of Torment
    59955: 3818,   # Arcanum of the Stalwart Protector
    59960: 3819,   # Arcanum of Blissful Mending
    59970: 3820,   # Arcanum of Burning Mysteries
    60581: 3822,   # Frosthide Leg Armor
    60582: 3823,   # Icescale Leg Armor
    60583: 3327,   # Jormungar Leg Reinforcements
    60623: 3826,   # Enchant Boots - Icewalker
    60653: 1128,   # Enchant Shield - Greater Intellect
    60663: 1099,   # Enchant Cloak - Major Agility
    60668: 1603,   # Enchant Gloves - Crusher
    60691: 3827,   # Enchant 2H Weapon - Massacre
    60692: 3832,   # Enchant Chest - Powerful Stats
    60714: 3834,   # Enchant Weapon - Mighty Spellpower
    60767: 2332,   # Enchant Bracers - Superior Spellpower
    62158: 3847,   # Rune of the Stoneskin Gargoyle
    62201: 3849,   # Titanium Plating
    62256: 3850,   # Enchant Bracers - Major Stamina
    62384: 3852,   # Greater Inscription of the Gladiator
    62948: 3854,   # Enchant Staff - Greater Spellpower
    63765: 3859,   # Springy Arachnoweave
    63770: 3860,   # Reticulated Armor Webbing
    64579: 3870,   # Enchant Weapon - Blood Draining
    67839: 3878,   # Mind Amplification Dish
    70164: 3883,   # Rune of the Nerubian Carapace
}

_SPELL_ENCHANT_CACHE: dict[int, int] = dict(_SPELL_ENCHANT_SEED)


def fetch_spell_enchant_id(spell_id: int) -> int:
    """Resolve an enchant *spell* ID to its SpellItemEnchantment ID.

    The gear-planner hash stores enchants as Spell.dbc IDs; the mod needs
    SpellItemEnchantment.dbc IDs. Seed cache covers all known WotLK enchants;
    anything unseen is resolved live via wotlkdb.com (`?spell=N` -> `enchantment=M`).
    """
    if spell_id == 0:
        return 0
    if spell_id in _SPELL_ENCHANT_CACHE:
        return _SPELL_ENCHANT_CACHE[spell_id]
    try:
        html = fetch_html(f"https://wotlkdb.com/?spell={spell_id}")
        refs = sorted({int(m) for m in re.findall(r"[?&]enchantment=(\d+)", html)})
        result = refs[0] if len(refs) == 1 else 0
        if len(refs) > 1:
            print(f"  Warning: enchant spell {spell_id} resolved to multiple enchantments {refs} — using 0")
    except Exception as e:
        print(f"  Warning: could not resolve enchant spell {spell_id}: {e}")
        result = 0
    if result == 0:
        print(f"  Warning: no SpellItemEnchantment found for enchant spell {spell_id} — enchant will be 0")
    _SPELL_ENCHANT_CACHE[spell_id] = result
    return result


def extract_print_html_payload(page_html: str) -> str:
    def unescape_payload(raw: str) -> str:
        return (
            raw.replace("\\/", "/")
            .replace('\\"', '"')
            .replace("\\r", "\r")
            .replace("\\n", "\n")
        )

    calls = re.findall(r"WH\.markup\.printHtml\((.*?)\);", page_html, re.S)
    for call_args in calls:
        payload = ""

        # Dynamic page data key path.
        m_key = re.search(r'WH\.getPageData\("([^"]+)"\)', call_args)
        if m_key:
            key = m_key.group(1)
            m_data = re.search(
                r'<script[^>]*id="data\.'
                + re.escape(key)
                + r'"[^>]*>(.*?)</script>',
                page_html,
                re.S,
            )
            if m_data:
                raw_script = m_data.group(1).strip()
                try:
                    payload = json.loads(raw_script)
                except Exception:
                    payload = raw_script.strip('"')
                payload = unescape_payload(payload)
        else:
            # Direct string literal path.
            # Drop the required closing quote so truncated calls (where ); appears
            # inside the guide body causing the findall to stop early) still work.
            m_literal = re.search(r'^\s*"((?:\\.|[^"\\])+)', call_args, re.S)
            if m_literal:
                payload = unescape_payload(m_literal.group(1))

        if payload and "[gear-planner=" in payload:
            return payload

    raise ValueError("Could not find guide payload containing gear-planner data.")


def extract_planner_hashes_from_markup(markup: str) -> dict[str, str]:
    """
    Returns mapping {tab_name_lower: planner_hash}
    Example hash: warlock/gnome/BgBQ...
    """
    result: dict[str, str] = {}
    tab_pattern = re.compile(
        r'\[tab[^\]]*name="([^"]+)"[^\]]*\](.*?)\[/tab\]', re.S | re.I
    )
    planner_pattern = re.compile(r"\[gear-planner=([^\]]+)\]", re.I)

    for tab_name, tab_body in tab_pattern.findall(markup):
        m = planner_pattern.search(tab_body)
        if not m:
            continue
        result[tab_name.strip().lower()] = m.group(1).strip()

    # Fallback if no tab blocks were found.
    if not result:
        m = planner_pattern.search(markup)
        if m:
            result["default"] = m.group(1).strip()

    if not result:
        raise ValueError("No [gear-planner=...] payload found in guide markup.")
    return result


def decode_planner_hash(planner_hash: str) -> dict[int, SlotRecord]:
    """
    Decodes Wowhead planner path hash based on GearPlannerWrath.js parse format.
    """
    parts = planner_hash.split("/")
    if len(parts) < 3:
        raise ValueError(f"Invalid planner hash: {planner_hash}")
    encoded = parts[-1]

    pad = "=" * ((4 - len(encoded) % 4) % 4)
    payload = base64.b64decode(encoded.replace("-", "+").replace("_", "/") + pad)
    data = list(payload)

    if not data:
        raise ValueError("Planner payload is empty.")

    version = data.pop(0)
    if version > 6:
        raise ValueError(f"Unsupported planner payload version: {version}")

    # version > 4 includes gender
    if version > 4 and data:
        data.pop(0)
    # version > 0 includes level
    if version > 0 and data:
        data.pop(0)

    # version > 1 includes packed talent hash
    if version > 1 and data:
        talent_byte_count = data.pop(0)
        for _ in range(talent_byte_count):
            if data:
                data.pop(0)
        if version >= 4 and data:
            glyph_suffix_len = data.pop(0)
            for _ in range(glyph_suffix_len):
                if data:
                    data.pop(0)

    ENCHANT_FLAG = 128
    RANDOM_ENCHANT_FLAG = 64

    out: dict[int, SlotRecord] = {}
    while len(data) >= 3:
        slot_flags = data.pop(0)
        gem_count = 0
        item = 0

        if version >= 3:
            first_item_byte = data.pop(0)
            gem_count = (first_item_byte & 224) >> 5
            item |= (first_item_byte & 31) << 16

        item |= data.pop(0) << 8
        item |= data.pop(0)

        has_enchant = (slot_flags & ENCHANT_FLAG) > 0
        has_random = (slot_flags & RANDOM_ENCHANT_FLAG) > 0
        inv_slot = slot_flags & ~ENCHANT_FLAG & ~RANDOM_ENCHANT_FLAG

        rec = SlotRecord(item=item, gems={})

        if has_enchant:
            enchant = 0
            if version >= 6 and data:
                enchant |= data.pop(0) << 16
            if len(data) >= 2:
                enchant |= data.pop(0) << 8
                enchant |= data.pop(0)
            rec.enchant = enchant

        if has_random and len(data) >= 2:
            rand = (data.pop(0) << 8) | data.pop(0)
            if (rand & 32768) > 0:
                rand -= 65536
            rec.random_enchant = rand

        for _ in range(gem_count):
            if len(data) < 3:
                break
            gem_prefix = data.pop(0)
            gem_index = (gem_prefix & 224) >> 5
            gem_id = ((gem_prefix & 31) << 16) | (data.pop(0) << 8) | data.pop(0)
            rec.gems[gem_index] = gem_id

        out[inv_slot] = rec

    return out


def parse_class_and_spec(url: str) -> tuple[str, str]:
    m = re.search(r"/classes/([^/]+)/([^/]+)/", url)
    if not m:
        raise ValueError("Could not infer class/spec from URL.")
    class_slug = m.group(1).strip().lower()
    spec_slug = m.group(2).strip().lower()

    class_map = {
        "death-knight": "Death Knight",
        "druid": "Druid",
        "hunter": "Hunter",
        "mage": "Mage",
        "paladin": "Paladin",
        "priest": "Priest",
        "rogue": "Rogue",
        "shaman": "Shaman",
        "warlock": "Warlock",
        "warrior": "Warrior",
    }
    spec_map = {
        "affliction": "Affliction",
        "demonology": "Demonology",
        "destruction": "Destruction",
        "balance": "Balance",
        "feral": "Feral",
        "restoration": "Restoration",
        "marksmanship": "Marksmanship",
        "beast-mastery": "Beastmastery",
        "beastmastery": "Beastmastery",
        "survival": "Survival",
        "arcane": "Arcane",
        "fire": "Fire",
        "frost": "Frost",
        "holy": "Holy",
        "protection": "Protection",
        "retribution": "Retribution",
        "discipline": "Discipline",
        "shadow": "Shadow",
        "assassination": "Assassination",
        "combat": "Combat",
        "subtlety": "Subtlety",
        "elemental": "Elemental",
        "enhancement": "Enhancement",
        "arms": "Arms",
        "fury": "Fury",
        "blood": "Blood",
        "unholy": "Unholy",
    }

    if class_slug not in class_map:
        raise ValueError(f"Unknown class slug: {class_slug}")
    if spec_slug not in spec_map:
        raise ValueError(f"Unknown spec slug: {spec_slug}")
    return class_map[class_slug], spec_map[spec_slug]


def suffix_to_label(suffix: str) -> str:
    """
    Converts suffix like '80PvEP4BiS' -> 'PvE P4 BiS'
    """
    rest = re.sub(r"^\d+", "", suffix)
    token_pattern = re.compile(r"(PvE|PvP|P\d+|S\d+|BiS|[A-Z][a-z]*\d*)")
    tokens = token_pattern.findall(rest)
    if not tokens:
        return suffix
    return " ".join(tokens)


def to_sql_gear_rows(
    player_class: str,
    player_spec: str,
    race_mask: str,
    decoded_slots: dict[int, SlotRecord],
) -> list[str]:
    rows: list[str] = []
    for inv_slot in sorted(decoded_slots):
        if inv_slot not in INV_SLOT_TO_POS:
            continue
        pos = INV_SLOT_TO_POS[inv_slot]
        rec = decoded_slots[inv_slot]
        gems = rec.gems or {}

        # inv_slot 6 = waist: any 3rd gem is from an Eternal Belt Buckle (prismatic socket).
        is_waist = (inv_slot == 6)

        gem0 = gems.get(0, 0)
        gem1 = gems.get(1, 0)
        gem2 = gems.get(2, 0)

        if is_waist:
            socket1 = fetch_gem_enchant_id(gem0)
            socket2 = fetch_gem_enchant_id(gem1)
            socket3 = 0
            prismatic = fetch_gem_enchant_id(gem2)
        else:
            socket1 = fetch_gem_enchant_id(gem0)
            socket2 = fetch_gem_enchant_id(gem1)
            socket3 = fetch_gem_enchant_id(gem2)
            prismatic = 0

        # rec.enchant is an enchant *spell* ID from the gear-planner hash; the
        # `enchant` column needs a SpellItemEnchantment ID (see fetch_spell_enchant_id).
        enchant = fetch_spell_enchant_id(rec.enchant)

        rows.append(
            f"('{player_class}', '{player_spec}', {race_mask}, {pos}, {rec.item}, "
            f"{enchant}, {socket1}, {socket2}, {socket3}, 0, {prismatic})"
        )
    return rows


def render_sql(
    player_class: str,
    spec_base: str,
    planners: dict[str, str],
    suffix: str,
    talent_override_suffix: str,
    category: str = "",
) -> str:
    player_spec = f"{spec_base}{suffix}"
    icon = SPEC_ICONS.get((player_class, spec_base), "inv_misc_questionmark")
    spec_label = suffix_to_label(suffix)
    gossip_text = f"|cff00ff00|TInterface\\\\icons\\\\{icon}:30|t|r Use {spec_base} {spec_label}"
    talent_spec = f"{spec_base}{talent_override_suffix}"

    decoded_by_tab = {tab: decode_planner_hash(h) for tab, h in planners.items()}

    has_alliance_tab = "alliance" in decoded_by_tab
    has_horde_tab = "horde" in decoded_by_tab

    if has_alliance_tab:
        alliance_slots = decoded_by_tab["alliance"]
    elif "default" in decoded_by_tab:
        alliance_slots = decoded_by_tab["default"]
    else:
        first_key = next(iter(decoded_by_tab))
        alliance_slots = decoded_by_tab[first_key]

    if has_horde_tab:
        horde_slots = decoded_by_tab["horde"]
    else:
        # Guide only has Alliance data — swap faction-locked items for Horde.
        horde_slots = apply_faction_swap(alliance_slots, to_horde=True)

    # If only a Horde tab exists, derive Alliance by swapping in the other direction.
    if has_horde_tab and not has_alliance_tab:
        alliance_slots = apply_faction_swap(horde_slots, to_horde=False)

    # Human matches Alliance setup unless explicitly separated.
    human_slots = alliance_slots

    rows = []
    rows.extend(to_sql_gear_rows(player_class, player_spec, "@RACEMASK_HUMAN", human_slots))
    rows.extend(to_sql_gear_rows(player_class, player_spec, "@RACEMASK_A", alliance_slots))
    rows.extend(to_sql_gear_rows(player_class, player_spec, "@RACEMASK_H", horde_slots))

    lines: list[str] = []
    lines.append(f"-- Auto-generated from Wowhead guide")
    lines.append(f"-- class={player_class}, spec={player_spec}")
    lines.append("")
    lines.append(
        "SET @ACTION = COALESCE((SELECT MAX(`gossipAction`) + 1 FROM `mod_npc_talent_template_index`), 0);"
    )
    lines.append("SET @MINLEVEL = COALESCE(@MINLEVEL, 80);")
    lines.append("SET @MAXLEVEL = COALESCE(@MAXLEVEL, 80);")
    lines.append("SET @RACEMASK_HUMAN = COALESCE(@RACEMASK_HUMAN, 1);")
    lines.append("SET @RACEMASK_A = COALESCE(@RACEMASK_A, 1100);")
    lines.append("SET @RACEMASK_H = COALESCE(@RACEMASK_H, 690);")
    lines.append("")
    lines.append("/*!40000 ALTER TABLE `mod_npc_talent_template_index` DISABLE KEYS */;")
    lines.append(
        "INSERT INTO `mod_npc_talent_template_index` "
        "(`playerClass`, `playerSpec`, `gossipAction`, `gossipText`, `mask`, `minLevel`, `maxLevel`, `glyphOverride`, `talentOverride`, `category`) VALUES"
    )
    lines.append(
        f"('{player_class}', '{player_spec}', @ACTION+000, "
        f"'{gossip_text}', 7, @MINLEVEL, @MAXLEVEL, '{talent_spec}', '{talent_spec}', '{category}'),"
    )
    lines.append(
        f"('{player_class}', '{player_spec}', @ACTION+001, "
        f"'{gossip_text} (Talents and Glyphs only)', 6, @MINLEVEL, @MAXLEVEL, '{talent_spec}', '{talent_spec}', '{category}');"
    )
    lines.append("/*!40000 ALTER TABLE `mod_npc_talent_template_index` ENABLE KEYS */;")
    lines.append("")
    lines.append("/*!40000 ALTER TABLE `mod_npc_talent_template_gear` DISABLE KEYS */;")
    lines.append(
        "INSERT INTO `mod_npc_talent_template_gear` "
        "(`playerClass`, `playerSpec`, `playerRaceMask`, `pos`, `itemEntry`, `enchant`, `socket1`, `socket2`, `socket3`, `bonusEnchant`, `prismaticEnchant`) VALUES"
    )
    for i, row in enumerate(rows):
        suffix_char = "," if i < len(rows) - 1 else ";"
        lines.append(f"{row}{suffix_char}")
    lines.append("/*!40000 ALTER TABLE `mod_npc_talent_template_gear` ENABLE KEYS */;")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract one Wowhead BiS page to SQL.")
    parser.add_argument("--url", required=True, help="Wowhead guide URL")
    parser.add_argument(
        "--suffix",
        default="80PvEP4BiS",
        help="Output playerSpec suffix (default: 80PvEP4BiS)",
    )
    parser.add_argument(
        "--talent-override-suffix",
        default="80PvP",
        help="Talent/glyph override suffix (default: 80PvP)",
    )
    parser.add_argument(
        "--category",
        default="",
        help="Gossip sub-menu category label (default: empty = root menu)",
    )
    parser.add_argument(
        "--out",
        default="out/generated_spec.sql",
        help="Output SQL file path",
    )
    args = parser.parse_args()

    page_html = fetch_html(args.url)
    markup = extract_print_html_payload(page_html)
    planners = extract_planner_hashes_from_markup(markup)

    player_class, spec_base = parse_class_and_spec(args.url)

    sql = render_sql(
        player_class=player_class,
        spec_base=spec_base,
        planners=planners,
        suffix=args.suffix,
        talent_override_suffix=args.talent_override_suffix,
        category=args.category,
    )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(sql, encoding="utf-8")
    print(f"Wrote SQL: {out_path}")


if __name__ == "__main__":
    main()
