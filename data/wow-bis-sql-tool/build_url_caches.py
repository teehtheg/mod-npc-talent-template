#!/usr/bin/env python3
"""
Build TBC URL cache files for phases 1-3 based on known URL patterns,
without making any HTTP requests. Run batch_extract_tbc.py --skip-discover
afterwards to extract.

URL patterns:
  Standard specs: {spec}-{class}-{role}-{phase-slug}-best-in-slot-gear-burning-crusade[-classic-wow]
  Consolidated:   {class}-{role}-{phase-slug}-best-in-slot-gear-burning-crusade[-classic-wow]
  Hunter BM:      beast-mastery-hunter-dps-{phase-slug}-...

Phases 0/1 use the -classic-wow suffix; phases 2/3 do not.
"""
from pathlib import Path

BASE = "https://tbc.wowhead.com/guides"

# (class_slug, spec_slug, role, player_class, player_spec, tbc_url_prefix)
# tbc_url_prefix: the {X} in {X}-{phase-slug}-best-in-slot-gear-burning-crusade
SPECS = [
    ("druid",   "balance",       "dps",    "Druid",   "Balance",       "balance-druid-dps"),
    ("druid",   "feral",         "dps",    "Druid",   "Cat",           "feral-druid-dps"),
    ("druid",   "feral",         "tank",   "Druid",   "Bear",          "feral-druid-tank"),
    ("druid",   "restoration",   "healer", "Druid",   "Restoration",   "druid-healer"),      # consolidated
    ("hunter",  "beast-mastery", "dps",    "Hunter",  "Beastmastery",  "beast-mastery-hunter-dps"),
    ("hunter",  "marksmanship",  "dps",    "Hunter",  "Marksmanship",  "marksmanship-hunter-dps"),
    ("hunter",  "survival",      "dps",    "Hunter",  "Survival",      "survival-hunter-dps"),
    ("mage",    "arcane",        "dps",    "Mage",    "Arcane",        "arcane-mage-dps"),
    ("mage",    "fire",          "dps",    "Mage",    "Fire",          "fire-mage-dps"),
    ("mage",    "frost",         "dps",    "Mage",    "Frost",         "frost-mage-dps"),
    ("paladin", "holy",          "healer", "Paladin", "Holy",          "holy-paladin-healer"),
    ("paladin", "protection",    "tank",   "Paladin", "Protection",    "paladin-tank"),       # consolidated
    ("paladin", "retribution",   "dps",    "Paladin", "Retribution",   "retribution-paladin-dps"),
    ("priest",  "discipline",    "healer", "Priest",  "Discipline",    "priest-healer"),      # consolidated
    ("priest",  "holy",          "healer", "Priest",  "Holy",          "priest-healer"),      # consolidated
    ("priest",  "shadow",        "dps",    "Priest",  "Shadow",        "shadow-priest-dps"),
    ("rogue",   "assassination", "dps",    "Rogue",   "Assassination", "rogue-dps"),          # consolidated
    ("rogue",   "combat",        "dps",    "Rogue",   "Combat",        "rogue-dps"),          # consolidated
    ("shaman",  "elemental",     "dps",    "Shaman",  "Elemental",     "elemental-shaman-dps"),
    ("shaman",  "enhancement",   "dps",    "Shaman",  "Enhancement",   "enhancement-shaman-dps"),
    ("shaman",  "restoration",   "healer", "Shaman",  "Restoration",   "shaman-healer"),      # consolidated
    ("warlock", "affliction",    "dps",    "Warlock", "Affliction",    "affliction-warlock-dps"),
    ("warlock", "demonology",    "dps",    "Warlock", "Demonology",    "demonology-warlock-dps"),
    ("warlock", "destruction",   "dps",    "Warlock", "Destruction",   "destruction-warlock-dps"),
    ("warrior", "arms",          "dps",    "Warrior", "Arms",          "arms-warrior-dps"),
    ("warrior", "fury",          "dps",    "Warrior", "Fury",          "fury-warrior-dps"),
    ("warrior", "protection",    "tank",   "Warrior", "Protection",    "protection-warrior-tank"),
]

PHASES = {
    1: ("karazhan", "-classic-wow"),
    2: ("tk-ssc-phase-2", ""),
    3: ("bt-hyjal-phase-3", ""),
}

out_dir = Path(__file__).parent / "out"
out_dir.mkdir(exist_ok=True)

for phase, (slug, suffix) in PHASES.items():
    lines = []
    for cls, spec, role, pcls, pspec, prefix in SPECS:
        url = f"{BASE}/{prefix}-{slug}-best-in-slot-gear-burning-crusade{suffix}"
        lines.append(f"{cls}\t{spec}\t{role}\t{pcls}\t{pspec}\t{url}")
    cache_path = out_dir / f"tbc_pve_p{phase}_urls.txt"
    cache_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {len(lines)} entries to {cache_path.name}")

print("Done. Run batch_extract_tbc.py --phase N --skip-discover for each phase.")
