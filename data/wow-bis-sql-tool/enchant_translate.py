#!/usr/bin/env python3
"""
Translate Wowhead enchant references into SpellItemEnchantment IDs using the
authoritative DBC dumps shipped with AzerothCore.

Ground-truth sources (all in data/sql/base/db_world/):
  - spell_dbc.sql               : Spell.dbc. An enchant spell has an effect of
                                  type ENCHANT_ITEM(53)/ENCHANT_ITEM_TEMPORARY(54)/
                                  ENCHANT_ITEM_PRISMATIC(156); that effect's
                                  EffectMiscValue is the SpellItemEnchantment ID.
  - spellitemenchantment_dbc.sql: SpellItemEnchantment.dbc — names, for labels.
  - item_template (db_world)    : for [item=N] enchant consumables (scrolls,
                                  arcanums, librams), resolve the item's on-use/
                                  equip spell, then that spell -> enchantment.

The gear template's `enchant` column (mod_npc_talent_template_gear) is consumed
by ApplyBonus -> Item::SetEnchantment, which takes a SpellItemEnchantment ID.

This module is import-friendly: build_translator() returns an object with
  .spell_to_ench(spell_id)  -> enchant_id | None
  .item_to_ench(item_id)    -> enchant_id | None
  .ench_name(enchant_id)    -> str | None
"""
from __future__ import annotations

import re
from pathlib import Path

# AzerothCore world-DB SQL base dir (…/Azerothcore_playerbots/data/sql/base/db_world)
# enchant_translate.py is at: <repo>/modules/mod-npc-talent-template/data/wow-bis-sql-tool/
_REPO_ROOT = Path(__file__).resolve().parents[4]
_DBWORLD = _REPO_ROOT / "data" / "sql" / "base" / "db_world"

SPELL_DBC_SQL = _DBWORLD / "spell_dbc.sql"
SIE_DBC_SQL = _DBWORLD / "spellitemenchantment_dbc.sql"
ITEM_TEMPLATE_SQL = _DBWORLD / "item_template.sql"

# Spell effect types that enchant an item (Effect_N column values)
_ENCHANT_EFFECTS = {53, 54, 156}  # ENCHANT_ITEM, _TEMPORARY, _PRISMATIC


def _parse_create_columns(sql_text: str, table: str) -> list[str]:
    """Return ordered column names from a CREATE TABLE `table` ( ... ) block."""
    m = re.search(rf"CREATE TABLE `{table}` \((.*?)\n\) ", sql_text, re.S)
    if not m:
        raise ValueError(f"CREATE TABLE `{table}` not found")
    cols: list[str] = []
    for line in m.group(1).splitlines():
        cm = re.match(r"\s*`([^`]+)`\s+\S", line)
        if cm:
            cols.append(cm.group(1))
    return cols


def _iter_value_tuples(sql_text: str):
    """
    Yield each value tuple (as a list of raw string tokens) from all
    `INSERT INTO ... VALUES (...),(...);` statements in sql_text.

    A hand-rolled scanner that respects single-quoted strings with '' and
    backslash escapes, so commas/parens inside SpellName do not break parsing.
    """
    i = 0
    n = len(sql_text)
    needle = "VALUES"
    while True:
        vpos = sql_text.find(needle, i)
        if vpos < 0:
            return
        # Only accept "VALUES" that belongs to an INSERT (cheap guard).
        line_start = sql_text.rfind("\n", 0, vpos)
        if "INSERT" not in sql_text[max(0, line_start):vpos] and \
           "INSERT" not in sql_text[max(0, vpos - 200):vpos]:
            i = vpos + len(needle)
            continue
        i = vpos + len(needle)
        # Parse consecutive ( ... ) tuples until ';'
        while i < n:
            # skip whitespace and commas
            while i < n and sql_text[i] in " \t\r\n,":
                i += 1
            if i >= n or sql_text[i] == ";":
                i += 1
                break
            if sql_text[i] != "(":
                # not a tuple list anymore
                break
            i += 1  # consume '('
            tokens: list[str] = []
            cur = []
            in_str = False
            while i < n:
                c = sql_text[i]
                if in_str:
                    if c == "\\":
                        cur.append(sql_text[i:i + 2])
                        i += 2
                        continue
                    if c == "'":
                        # doubled '' = literal quote
                        if i + 1 < n and sql_text[i + 1] == "'":
                            cur.append("''")
                            i += 2
                            continue
                        in_str = False
                        i += 1
                        continue
                    cur.append(c)
                    i += 1
                    continue
                # not in string
                if c == "'":
                    in_str = True
                    i += 1
                    continue
                if c == ",":
                    tokens.append("".join(cur).strip())
                    cur = []
                    i += 1
                    continue
                if c == ")":
                    tokens.append("".join(cur).strip())
                    cur = []
                    i += 1
                    break
                cur.append(c)
                i += 1
            yield tokens


class EnchantTranslator:
    def __init__(self, spell_to_ench, ench_names, item_spells):
        self._spell_to_ench = spell_to_ench   # {spell_id: enchant_id}
        self._ench_names = ench_names          # {enchant_id: name}
        self._item_spells = item_spells        # {item_id: [spell_id, ...]}

    def spell_to_ench(self, spell_id: int) -> int | None:
        return self._spell_to_ench.get(spell_id)

    def item_to_ench(self, item_id: int) -> int | None:
        for sp in self._item_spells.get(item_id, []):
            ench = self._spell_to_ench.get(sp)
            if ench:
                return ench
        return None

    def ref_to_ench(self, kind: str, ref_id: int) -> int | None:
        return self.spell_to_ench(ref_id) if kind == "spell" else self.item_to_ench(ref_id)

    def ench_name(self, enchant_id: int) -> str | None:
        return self._ench_names.get(enchant_id)


def build_translator(load_items: bool = True) -> EnchantTranslator:
    # --- spell_dbc: spell_id -> enchant_id ---------------------------------
    spell_sql = SPELL_DBC_SQL.read_text(encoding="utf-8", errors="ignore")
    cols = _parse_create_columns(spell_sql, "spell_dbc")
    idx = {name: pos for pos, name in enumerate(cols)}
    c_id = idx["ID"]
    eff = [idx[f"Effect_{k}"] for k in (1, 2, 3)]
    misc = [idx[f"EffectMiscValue_{k}"] for k in (1, 2, 3)]

    spell_to_ench: dict[int, int] = {}
    for tok in _iter_value_tuples(spell_sql):
        if len(tok) <= max(c_id, eff[2], misc[2]):
            continue
        try:
            for k in range(3):
                if int(tok[eff[k]]) in _ENCHANT_EFFECTS:
                    ench = int(tok[misc[k]])
                    if ench:
                        spell_to_ench[int(tok[c_id])] = ench
                        break
        except (ValueError, IndexError):
            continue

    # --- spellitemenchantment_dbc: enchant_id -> name ----------------------
    ench_names: dict[int, str] = {}
    sie_sql = SIE_DBC_SQL.read_text(encoding="utf-8", errors="ignore")
    sie_cols = _parse_create_columns(sie_sql, "spellitemenchantment_dbc")
    sie_idx = {name: pos for pos, name in enumerate(sie_cols)}
    sie_id = sie_idx.get("ID", 0)
    # Name column is locale-suffixed; pick the first column containing "Name"
    name_col = next((p for n, p in sie_idx.items() if "Name" in n), None)
    for tok in _iter_value_tuples(sie_sql):
        if name_col is None or len(tok) <= max(sie_id, name_col):
            continue
        try:
            ench_names[int(tok[sie_id])] = tok[name_col].strip()
        except ValueError:
            continue

    # --- item_template: item_id -> [spell ids] -----------------------------
    item_spells: dict[int, list[int]] = {}
    if load_items and ITEM_TEMPLATE_SQL.exists():
        it_sql = ITEM_TEMPLATE_SQL.read_text(encoding="utf-8", errors="ignore")
        it_cols = _parse_create_columns(it_sql, "item_template")
        it_idx = {name: pos for pos, name in enumerate(it_cols)}
        it_entry = it_idx.get("entry", 0)
        spell_cols = [it_idx[f"spellid_{k}"] for k in range(1, 6) if f"spellid_{k}" in it_idx]
        for tok in _iter_value_tuples(it_sql):
            if not spell_cols or len(tok) <= max(it_entry, max(spell_cols)):
                continue
            try:
                eid = int(tok[it_entry])
                sps = [int(tok[p]) for p in spell_cols if tok[p] not in ("0", "")]
                if sps:
                    item_spells[eid] = sps
            except ValueError:
                continue

    return EnchantTranslator(spell_to_ench, ench_names, item_spells)


if __name__ == "__main__":
    # Self-test against well-known classic enchants.
    print("Building translator from DBC dumps...")
    t = build_translator(load_items=True)
    print(f"  spell->ench entries:  {len(t._spell_to_ench)}")
    print(f"  enchant names:        {len(t._ench_names)}")
    print(f"  items with spells:    {len(t._item_spells)}")
    print()
    KNOWN = {
        20025: "Enchant Chest - Greater Stats",
        20034: "Enchant Weapon - Crusader",
        13890: "Enchant Boots - Minor Speed",
        20008: "Enchant Bracer - Intellect",
        22749: "Enchant Weapon - Spell Power",
    }
    for sp, desc in KNOWN.items():
        ench = t.spell_to_ench(sp)
        name = t.ench_name(ench) if ench else None
        print(f"  spell {sp:6d} ({desc:38s}) -> enchant {ench} [{name}]")