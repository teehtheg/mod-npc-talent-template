#!/usr/bin/env python3
"""Generate WotLK PvE glyph templates (`{spec}80PvE`) for mod-npc-talent-template.

The `mod_npc_talent_template_glyphs.glyph` column stores a **GlyphProperties.dbc ID**
(the C++ does `sGlyphPropertiesStore.LookupEntry(glyphID)`). Wowhead's data only
exposes glyph `spellId`/`itemId`, so this maps them to GlyphProperties IDs via the
client DBC (`GlyphProperties.dbc`, field: ID -> SpellID), which is parsed here.

Glyph *choices* come from each spec's talent/glyph guide
(`{role}-talent-builds-glyphs-pve`), reading the "Major Glyphs" / "Minor Glyphs"
sections. Selection is best-effort: the first 3 major-type glyphs from the Major
section and the first 3 minor-type from the Minor section (typed via the DBC so a
cross-referenced glyph of the wrong type is skipped). Guides that present glyphs
situationally (no single set) may need manual review — flagged per spec.

Slot convention (from existing mod data): major slots = 0, 3, 5; minor = 1, 2, 4.

Usage (from wow-bis-sql-tool/):
    python extract_wotlk_glyphs.py --url-file out/wotlk_pve_p4_urls.txt \
        --dbc "C:/Azerothcore_playerbots_build/bin/RelWithDebInfo/Data/dbc/GlyphProperties.dbc"
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import struct
import sys
import time
from pathlib import Path

from sql_idempotency import idempotency_lines

# wowhead classId per player-class name
CLASS_ID = {
    "Warrior": 1, "Paladin": 2, "Hunter": 3, "Rogue": 4, "Priest": 5,
    "Death Knight": 6, "Shaman": 7, "Mage": 8, "Warlock": 9, "Druid": 11,
}
MAJOR_SLOTS = [0, 3, 5]
MINOR_SLOTS = [1, 2, 4]


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def load_glyphproperties(dbc_path: Path) -> dict[int, int]:
    """Parse GlyphProperties.dbc -> {SpellID: GlyphProperties ID}."""
    data = dbc_path.read_bytes()
    magic, nrec, nfield, recsize, _str = struct.unpack("<4sIIII", data[:20])
    if magic != b"WDBC":
        raise ValueError(f"{dbc_path} is not a WDBC file")
    spell2id: dict[int, int] = {}
    off = 20
    for _ in range(nrec):
        rid, spell_id, _flags, _icon = struct.unpack("<4I", data[off:off + recsize][:16])
        off += recsize
        spell2id[spell_id] = rid
    return spell2id


def load_glyph_flags(dbc_path: Path) -> dict[int, int]:
    """Parse GlyphProperties.dbc -> {GlyphProperties ID: GlyphSlotFlags} (0=major, 1=minor)."""
    data = dbc_path.read_bytes()
    _m, nrec, _nf, recsize, _s = struct.unpack("<4sIIII", data[:20])
    flags: dict[int, int] = {}
    off = 20
    for _ in range(nrec):
        rid, _spell, gflags, _icon = struct.unpack("<4I", data[off:off + recsize][:16])
        off += recsize
        flags[rid] = gflags
    return flags


def load_wotlk_glyph_data(script_dir: Path) -> dict:
    raw = (script_dir / "out" / "talents_wotlk_raw.js").read_text(encoding="utf-8")
    start = raw.find('",') + 2
    while raw[start] == " ":
        start += 1
    depth = 0
    i = start
    in_str = False
    while i < len(raw):
        c = raw[i]
        if in_str:
            if c == "\\":
                i += 2
                continue
            if c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    break
        i += 1
    return json.loads(raw[start:i + 1])


def glyph_guide_url(cls_slug: str, spec_slug: str, role_slug: str) -> str:
    return (f"https://www.wowhead.com/wotlk/guide/classes/{cls_slug}/{spec_slug}/"
            f"{role_slug}-talent-builds-glyphs-pve")


def _section_items(html: str, heading: str, next_heading: str | None) -> list[int]:
    start = html.find(heading)
    if start < 0:
        return []
    end = html.find(next_heading, start + len(heading)) if next_heading else -1
    seg = html[start: end if end > 0 else start + 4000]
    out: list[int] = []
    for it in re.findall(r"item=(\d+)", seg):
        it = int(it)
        if it not in out:
            out.append(it)
    return out


def pick_glyphs(html: str, item2spell: dict[int, int], spell2gp: dict[int, int],
                gp_flags: dict[int, int]):
    """Return (majors, minors) as lists of GlyphProperties IDs (best-effort, first 3 typed)."""
    def to_gp(item):
        sp = item2spell.get(item)
        return spell2gp.get(sp) if sp else None

    def typed(items, want_flag, limit=3):
        out = []
        for it in items:
            gp = to_gp(it)
            if gp is not None and gp_flags.get(gp) == want_flag and gp not in out:
                out.append(gp)
            if len(out) == limit:
                break
        return out

    major_items = _section_items(html, "Major Glyph", "Minor Glyph")
    minor_items = _section_items(html, "Minor Glyph", None)
    return typed(major_items, 0), typed(minor_items, 1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate WotLK PvE glyph templates")
    parser.add_argument("--dbc", required=True, help="Path to client GlyphProperties.dbc")
    parser.add_argument("--url-file", default=None,
                        help="Spec list (default out/wotlk_pve_p4_urls.txt).")
    parser.add_argument("--suffix", default="80PvE", help="playerSpec suffix (default 80PvE).")
    parser.add_argument("--out", default=None, help="Output SQL (default out/wotlk_pve_glyphs.sql).")
    args = parser.parse_args()

    script_dir = Path(__file__).parent
    out_dir = script_dir / "out"
    mw = _load_module("extract_wotlk_bis", script_dir / "extract_wotlk_bis.py")
    gdata = load_wotlk_glyph_data(script_dir)
    dbc = Path(args.dbc)
    spell2gp = load_glyphproperties(dbc)
    gp_flags = load_glyph_flags(dbc)

    # wowhead glyph itemId -> spellId, keyed by classId
    item2spell_by_class: dict[int, dict[int, int]] = {}
    for g in gdata["glyphs"]:
        item2spell_by_class.setdefault(g["classId"], {})[g["itemId"]] = g["spellId"]

    url_path = Path(args.url_file) if args.url_file else out_dir / "wotlk_pve_p4_urls.txt"
    entries = []
    for line in url_path.read_text(encoding="utf-8").splitlines():
        parts = line.strip().split("\t")
        if len(parts) == 4:
            entries.append(parts)  # cls, spec, role, gear-url

    out_path = Path(args.out) if args.out else out_dir / "wotlk_pve_glyphs.sql"

    rows: list[str] = []
    specs_seen: list[str] = []
    warnings: list[str] = []

    print(f"Extracting glyphs for {len(entries)} specs ...")
    for idx, (cls_slug, spec_slug, role_slug, _gear_url) in enumerate(entries):
        try:
            player_class, spec_base = mw.parse_class_and_spec(_gear_url)
            if player_class == "Druid" and spec_base == "Feral" and role_slug == "tank":
                spec_base = "FeralTank"
            player_spec = f"{spec_base}{args.suffix}"
            cid = CLASS_ID.get(player_class)
            item2spell = item2spell_by_class.get(cid, {})
            html = mw.fetch_html(glyph_guide_url(cls_slug, spec_slug, role_slug)).replace("\\/", "/")
            majors, minors = pick_glyphs(html, item2spell, spell2gp, gp_flags)
            if len(majors) < 3 or len(minors) < 3:
                warnings.append(f"{player_class} {player_spec}: only {len(majors)} major / {len(minors)} minor found")
            # playerSpec names can repeat across classes (e.g. Frost = Mage + DK);
            # emit rows for each — they are distinguished by playerClass.
            specs_seen.append(player_spec)
            for slot, gp in zip(MAJOR_SLOTS, majors):
                rows.append(f"('{player_class}', '{player_spec}', {slot}, {gp})")
            for slot, gp in zip(MINOR_SLOTS, minors):
                rows.append(f"('{player_class}', '{player_spec}', {slot}, {gp})")
            print(f"  [{idx+1:2d}/{len(entries)}] {player_class}/{spec_base} -> {player_spec}  "
                  f"major={majors} minor={minors}")
        except Exception as exc:
            print(f"  [{idx+1:2d}/{len(entries)}] {cls_slug}/{spec_slug}/{role_slug}: ERROR {exc}",
                  file=sys.stderr)
            warnings.append(f"{cls_slug}/{spec_slug}: {exc}")
        time.sleep(0.2)

    lines = [
        "-- Auto-generated WotLK PvE glyph templates (suffix: 80PvE)",
        f"-- Source: {{role}}-talent-builds-glyphs-pve guides; GlyphProperties IDs via client DBC; {len(specs_seen)} specs",
    ]
    idem = idempotency_lines(["glyphs"], specs_seen)
    if idem:
        lines += [""] + idem
    lines.append("")
    lines.append("/*!40000 ALTER TABLE `mod_npc_talent_template_glyphs` DISABLE KEYS */;")
    lines.append("INSERT INTO `mod_npc_talent_template_glyphs` "
                 "(`playerClass`, `playerSpec`, `slot`, `glyph`) VALUES")
    for i, row in enumerate(rows):
        lines.append(f"{row}{',' if i < len(rows) - 1 else ';'}")
    lines.append("/*!40000 ALTER TABLE `mod_npc_talent_template_glyphs` ENABLE KEYS */;")

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nWrote {len(rows)} glyph rows for {len(specs_seen)} specs to {out_path}")
    if warnings:
        print(f"  {len(warnings)} warning(s) — review these specs:")
        for w in warnings:
            print(f"    {w}")


if __name__ == "__main__":
    main()
