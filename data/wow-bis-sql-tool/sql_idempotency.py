"""Shared helper: build an idempotency DELETE block for generated template SQL.

The `mod_npc_talent_template_*` tables have no unique key, and the module's SQL
updater applies each file once by hash — so a re-applied file (content changed,
renamed, or re-imported) would silently append duplicate rows. Prepending a
DELETE of exactly this file's `playerSpec` values (from exactly the tables it
writes) makes (re-)application idempotent.

Two entry points:
- ``idempotency_lines(tables, specs)`` — build from explicit inputs.
- ``idempotency_lines_from_sql(sql)`` — parse tables + specs out of already-built
  INSERT SQL, so the block always matches what the generator actually emits.

Both return a list of SQL lines (no surrounding blank lines — the caller places
those), or ``[]`` when there is nothing to delete.
"""
from __future__ import annotations

import re

# Emit DELETEs in a stable, readable order regardless of INSERT order in the file.
_TABLE_ORDER = ["index", "gear", "talents", "glyphs"]

_INSERT_RE = re.compile(r"INSERT INTO `mod_npc_talent_template_(\w+)`")
# A data value-row: ('<class>', '<playerSpec>', ...   -> capture the 2nd column.
_ROW_RE = re.compile(r"^\(\s*'[^']*'\s*,\s*'([^']*)'", re.MULTILINE)

_COMMENT = [
    "-- Idempotency: remove this file's templates first so re-application cannot duplicate rows",
    "-- (the index table has no unique key; without this a re-applied file would append copies).",
]


def _dedupe(items):
    seen = set()
    out = []
    for it in items:
        if it not in seen:
            seen.add(it)
            out.append(it)
    return out


def idempotency_lines(tables, specs):
    """Build the DELETE block for the given tables and playerSpecs."""
    specs = _dedupe(specs)
    tables = [t for t in _TABLE_ORDER if t in set(tables)]
    if not tables or not specs:
        return []

    in_list = ", ".join("'" + s.replace("'", "''") + "'" for s in specs)
    lines = list(_COMMENT)
    lines += [
        f"DELETE FROM `mod_npc_talent_template_{t}` WHERE `playerSpec` IN ({in_list});"
        for t in tables
    ]
    return lines


def idempotency_lines_from_sql(sql):
    """Build the DELETE block by parsing tables + specs out of INSERT SQL."""
    tables = _dedupe(m.group(1) for m in _INSERT_RE.finditer(sql))
    specs = _dedupe(m.group(1) for m in _ROW_RE.finditer(sql))
    return idempotency_lines(tables, specs)
