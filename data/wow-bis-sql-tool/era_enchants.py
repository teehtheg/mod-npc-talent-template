#!/usr/bin/env python3
"""
Era-appropriate enchant maps for the level-60 (Classic) and level-70 (TBC) BiS
gear templates, expressed as SpellItemEnchantment IDs valid in the 3.3.5a client.

Why this exists
---------------
AzerothCore runs the WotLK (3.3.5a) client, whose SpellItemEnchantment DBC still
contains every Classic and TBC enchant. The template NPC should hand out sets
that feel like endgame at each level, so level-60 sets use Classic-era enchants
and level-70 sets use TBC-era enchants — but every ID here has been verified to
exist in 3.3.5a (resolved via wotlkdb.com `?enchantment=N`). Level-80 sets get
their enchants from the WotLK gear planner (see extract_wotlk_bis.py) and are not
covered here.

`pos` uses the gear-template convention (INV_SLOT_TO_POS in the extractors):
  0 head  2 shoulder  4 chest  6 legs  7 feet  8 wrist  9 hands
  14 back  15 main-hand  16 off-hand  17 ranged
Neck(1), waist(5), rings(10/11) and trinkets(12/13) are not enchantable and are
intentionally absent (→ enchant 0).

Public API:
  enchants_for(level, player_class, player_spec) -> {pos: sie_id}
"""
from __future__ import annotations

import json
from pathlib import Path

# --- verified SIE ids used below (effect confirmed via wotlkdb ?enchantment=N) --
# 2721 +15SP+14crit  2715 +16SP+6mp5  2717 +26AP+14crit  2716 +16stam+100armor
# 2605/2604 +18SP (Zandalar shoulder)  2606 +30AP  1891 +4 all stats
# 911 Minor Speed  851 +5 Spirit  1887 +7 Agi  1885 +9 Str  1886 +9 Stamina
# 2650 +15SP (bracer)  2322 +19SP (gloves)  2564 +15 Agi (gloves/weapon)
# 1888 +5 all res  1889 +70 armor  849 +3 Agi  2620 +15 nature res
# 2504 +30SP (weapon)  2505 +29SP heal (weapon)  1900 Crusader  2646 +25 Agi (2H)
# 2523 +30 ranged hit (scope)  929 +7 Stamina (shield)  907 +7 Spirit (shield)

# ---------------------------------------------------------------------------
# Level 60 — Classic era
# ---------------------------------------------------------------------------
# Fine-grained role buckets (finer than the extractor's 5-role split so weapon
# and wrist enchants match str vs agi vs caster).
_C60_ROLE = {
    "caster": {0: 2721, 2: 2605, 4: 1891, 6: 2721, 7: 911, 8: 2650, 9: 2322, 14: 1888, 15: 2504},
    "healer": {0: 2715, 2: 2604, 4: 1891, 6: 2715, 7: 911, 8: 2650, 9: 2322, 14: 1888, 15: 2505},
    "melee_str": {0: 2717, 2: 2606, 4: 1891, 6: 2717, 7: 1887, 8: 1885, 9: 2564, 14: 849, 15: 1900},
    "melee_agi": {0: 2717, 2: 2606, 4: 1891, 6: 2717, 7: 1887, 8: 1885, 9: 2564, 14: 849, 15: 2564},
    "hunter": {0: 2717, 2: 2606, 4: 1891, 6: 2717, 7: 1887, 8: 1885, 9: 2564, 14: 849, 15: 2646, 17: 2523},
    "tank": {0: 2716, 2: 2606, 4: 1891, 6: 2716, 7: 1887, 8: 1886, 9: 2564, 14: 1889, 15: 1900},
}
# Classic shield enchant by role (off-hand slot for shield users)
_C60_SHIELD = {"tank": 929, "healer": 907, "caster": 929}

# ---------------------------------------------------------------------------
# Level 70 — TBC era  (all ids verified req-70/60 and present in 3.3.5a)
# ---------------------------------------------------------------------------
# Head = Glyph/Arcanum (req70); Shoulder = Aldor/Scryer Greater Inscription (req70);
# Legs = spellthread / leg armor; Feet = Cat's Swiftness / Boar's Speed / Vitality;
# weapon default is role-based, overridden per spec for casters (school power) and
# str 2H users (Savagery).
_T70_ROLE = {
    "caster": {0: 3002, 2: 2995, 4: 2661, 6: 2748, 7: 2656, 8: 2650, 9: 2322, 14: 2621, 15: 2671},
    "healer": {0: 3001, 2: 2993, 4: 2661, 6: 2748, 7: 2656, 8: 2650, 9: 2322, 14: 2621, 15: 2505},
    "melee_str": {0: 3003, 2: 2986, 4: 2661, 6: 3012, 7: 2939, 8: 2647, 9: 684, 14: 368, 15: 2673},
    "melee_agi": {0: 3003, 2: 2986, 4: 2661, 6: 3012, 7: 2939, 8: 2647, 9: 2564, 14: 368, 15: 2673},
    "hunter": {0: 3003, 2: 2986, 4: 2661, 6: 3012, 7: 2939, 8: 2647, 9: 2564, 14: 368, 15: 2670, 17: 2724},
    "tank": {0: 2999, 2: 2991, 4: 2661, 6: 3011, 7: 2940, 8: 2648, 9: 2564, 14: 368, 15: 2673},
}
_T70_SHIELD = {"tank": 929, "healer": 907, "caster": 929}
# Caster weapon (pos 15) is school-specific: Sunfire (arcane/fire) vs Soulfrost (shadow/frost)
_T70_CASTER_WEAPON = {
    "Fire": 2671, "Arcane": 2671, "Balance": 2671, "Elemental": 2671, "Destruction": 2671,
    "Frost": 2672, "Shadow": 2672, "Affliction": 2672, "Demonology": 2672,
}
# Two-handed strength users take Savagery (+70 AP) instead of the 1H Mongoose default.
_T70_TWOHAND_STR = {"Arms", "ArmsAxe", "ArmsSword", "Retribution"}

# --- TBC gems (level 70) ---------------------------------------------------
# TBC introduced sockets (Classic gear has none, level-80 gems come from the gear
# planner). Socket color codes from item_template.socketColor_N: 1=Meta, 2=Red,
# 4=Yellow, 8=Blue. Values below are SpellItemEnchantment ids (verified via
# wotlkdb ?enchantment=N), matching each socket's colour so socket bonuses fire.
_T70_GEMS = {
    #            Meta(1)  Red(2)   Yellow(4) Blue(8)
    "caster":    {1: 2828, 2: 2728, 4: 2736, 8: 2740},  # spell haste / +9SP / +8crit / +5SP+6sta
    "healer":    {1: 2835, 2: 2728, 4: 2734, 8: 2740},  # int+mana / +9SP / +8int / +5SP+6sta
    "melee_str": {1: 2834, 2: 2725, 4: 2735, 8: 2731},  # +dmg / +8str / +8crit / +12sta
    "melee_agi": {1: 2829, 2: 2726, 4: 2735, 8: 2731},  # +24AP / +8agi / +8crit / +12sta
    "hunter":    {1: 2829, 2: 2726, 4: 2764, 8: 2731},  # +24AP / +8agi / +8hit / +12sta
    "tank":      {1: 2833, 2: 2725, 4: 2737, 8: 2731},  # def+heal / +8str / +8def / +12sta
}

# item_entry -> [socket colours] for TBC BiS items (exported from item_template).
_SOCKET_COLORS: dict[str, list[int]] = json.loads(
    (Path(__file__).parent / "tbc_socket_colors.json").read_text(encoding="utf-8")
)


def socket_colors_for(item_entry: int) -> list[int]:
    """Socket colours (1/2/4/8) of a TBC item, or [] if it has no sockets."""
    return _SOCKET_COLORS.get(str(item_entry), [])


def gems_for(level: int, player_class: str, player_spec: str, item_entry: int):
    """(socket1, socket2, socket3) gem SIE ids for a TBC item's sockets, colour-
    matched to the spec's role. Returns (0,0,0) for non-TBC or socketless items."""
    if level != 70:
        return (0, 0, 0)
    colors = socket_colors_for(item_entry)
    if not colors:
        return (0, 0, 0)
    table = _T70_GEMS[_role_of(player_class, player_spec)]
    out = [table.get(c, 0) for c in colors[:3]]
    return tuple(out + [0] * (3 - len(out)))

# ---------------------------------------------------------------------------
# spec -> fine role. Keyed by spec name (matches CLASSIC_SPEC_ROLE usage);
# healer/tank spec names are class-agnostic (Holy/Protection/Restoration).
# ---------------------------------------------------------------------------
_SPEC_ROLE = {
    "Balance": "caster", "Cat": "melee_agi", "Bear": "tank", "Restoration": "healer",
    "Marksmanship": "hunter", "Beastmastery": "hunter", "Survival": "hunter",
    "Fire": "caster", "Frost": "caster", "Arcane": "caster",
    "Holy": "healer", "Protection": "tank", "Retribution": "melee_str",
    "Discipline": "healer", "Shadow": "caster",
    "Combat": "melee_agi", "Assassination": "melee_agi", "Subtlety": "melee_agi",
    "Elemental": "caster", "Enhancement": "melee_agi",
    "Affliction": "caster", "Demonology": "caster", "Destruction": "caster",
    "Arms": "melee_str", "ArmsAxe": "melee_str", "ArmsSword": "melee_str",
    "Fury": "melee_str",
    "Blood": "melee_str", "Unholy": "melee_str",
}

# Off-hand handling needs class + spec (Resto druid has no shield; Resto sham does).
_SHIELD_USERS = {
    ("Warrior", "Protection"), ("Paladin", "Protection"), ("Paladin", "Holy"),
    ("Priest", "Holy"), ("Priest", "Discipline"),
    ("Shaman", "Restoration"), ("Shaman", "Elemental"),
}
_DUAL_WIELD = {
    ("Warrior", "Fury"), ("Shaman", "Enhancement"),
    ("Rogue", "Combat"), ("Rogue", "Assassination"), ("Rogue", "Subtlety"),
}


def _role_of(player_class: str, player_spec: str) -> str:
    return _SPEC_ROLE.get(player_spec, "melee_str")


def _classic_enchants(player_class: str, player_spec: str) -> dict[int, int]:
    role = _role_of(player_class, player_spec)
    result = dict(_C60_ROLE[role])
    key = (player_class, player_spec)
    if key in _SHIELD_USERS:
        result[16] = _C60_SHIELD.get(role, 929)
    elif key in _DUAL_WIELD:
        result[16] = result.get(15, 0)  # off-hand weapon mirrors main-hand
    return result


def _tbc_enchants(player_class: str, player_spec: str) -> dict[int, int]:
    role = _role_of(player_class, player_spec)
    result = dict(_T70_ROLE[role])
    # Caster weapon: school-specific power enchant.
    if role == "caster" and player_spec in _T70_CASTER_WEAPON:
        result[15] = _T70_CASTER_WEAPON[player_spec]
    # Two-handed strength melee: Savagery instead of Mongoose.
    if role == "melee_str" and player_spec in _T70_TWOHAND_STR:
        result[15] = 2667
    key = (player_class, player_spec)
    if key in _SHIELD_USERS:
        result[16] = _T70_SHIELD.get(role, 929)
    elif key in _DUAL_WIELD:
        result[16] = result.get(15, 0)  # off-hand weapon mirrors main-hand
    return result


def enchants_for(level: int, player_class: str, player_spec: str) -> dict[int, int]:
    """Return {pos: SpellItemEnchantment_id} for the given build tier.

    level 60 -> Classic-era; level 70 -> TBC-era. Level 80 (WotLK) enchants come
    from the gear planner (extract_wotlk_bis.py) and are not handled here.
    Unknown levels return {} (caller keeps enchant 0).
    """
    if level <= 60:
        return _classic_enchants(player_class, player_spec)
    if level <= 70:
        return _tbc_enchants(player_class, player_spec)
    return {}


if __name__ == "__main__":
    # Quick dump for review.
    POS = {0: "Head", 2: "Shldr", 4: "Chest", 6: "Legs", 7: "Feet", 8: "Wrist",
           9: "Hands", 14: "Back", 15: "MH", 16: "OH", 17: "Rng"}
    for level in (60, 70):
        for spec, role in sorted(_SPEC_ROLE.items(), key=lambda kv: (kv[1], kv[0])):
            cls = {"tank": "Warrior", "healer": "Priest", "caster": "Mage",
                   "hunter": "Hunter", "melee_str": "Warrior", "melee_agi": "Rogue"}[role]
            d = enchants_for(level, cls, spec)
            line = " ".join(f"{POS[p]}={d[p]}" for p in sorted(d))
            print(f"L{level} {spec:13s} [{role:9s}] {line}")
        print()
