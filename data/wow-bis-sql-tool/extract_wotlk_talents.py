#!/usr/bin/env python3
"""Generate WotLK PvE talent templates (`{spec}80PvE`) for mod-npc-talent-template.

The WotLK BiS gear guides embed a gear-planner hash that encodes the recommended
talent build *alongside* the gear (a gear planner = gear + talents + glyphs). This
script reuses that same hash — already fetched for the BiS gear — to emit
`mod_npc_talent_template_talents` rows, so the WotLK PvE index rows can reference
`{spec}80PvE` talents instead of the `{spec}80PvP` stand-in.

No separate talent-guide scraping and no manual hashes: the planner's talent bytes
are decoded to a `tree1-tree2-tree3` digit hash, then to talent spell IDs via the
Wowhead WotLK talent tree data (`out/talents_wotlk_raw.js`), reusing
`extract_classic_talents.decode_build`.

Glyphs are intentionally NOT generated here — the `glyph` column needs
GlyphProperties.dbc IDs (client-side), so the index rows keep their `{spec}80PvP`
glyphOverride as a documented interim (see pr-review-tasks.md).

Usage (from wow-bis-sql-tool/):
    python extract_wotlk_talents.py --phase 4            # all specs from the P4 URL cache
    python extract_wotlk_talents.py --phase 4 --out out/wotlk_pve_talents.sql
"""
from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import re
import sys
import time
from pathlib import Path

from sql_idempotency import idempotency_lines


def _load_module(name: str, path: Path):
    """Import a sibling module by path (registered in sys.modules for @dataclass)."""
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def load_wotlk_talent_data(script_dir: Path) -> dict:
    """Parse the WH.setPageData(...) JSON blob out of out/talents_wotlk_raw.js."""
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


def planner_talent_digits(planner: str) -> str | None:
    """Extract the `tree1-tree2-tree3` talent digit hash from a gear-planner string.

    Mirrors the version-aware header parsing in `extract_wotlk_bis.decode_planner_hash`:
    version, [gender if v>4], [level if v>0], then (if v>1) a talent byte count
    followed by packed talent bytes (each byte = 2 nibbles; 0-5 = points, 0xF = tree
    separator).
    """
    b64 = planner.split("/")[-1]
    data = list(base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4)))
    if not data:
        return None
    version = data.pop(0)
    if version > 4 and data:
        data.pop(0)  # gender
    if version > 0 and data:
        data.pop(0)  # level
    if version <= 1 or not data:
        return None
    n_bytes = data.pop(0)
    talent_bytes = data[:n_bytes]
    nibbles: list[int] = []
    for b in talent_bytes:
        nibbles.append((b >> 4) & 0xF)
        nibbles.append(b & 0xF)
    out = ""
    seps = 0
    for v in nibbles:
        if seps >= 3:
            break
        if v == 0xF:
            out += "-"
            seps += 1
        else:
            out += str(v)
    return re.sub(r"-+$", "", out)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate WotLK PvE talent templates")
    parser.add_argument("--phase", type=int, default=4,
                        help="Phase whose BiS guides supply the builds (default 4 = latest).")
    parser.add_argument("--suffix", default="80PvE",
                        help="playerSpec suffix for the generated talent sets (default 80PvE).")
    parser.add_argument("--url-file", default=None,
                        help="URL cache (default out/wotlk_pve_p{phase}_urls.txt).")
    parser.add_argument("--out", default=None,
                        help="Output SQL (default out/wotlk_pve_talents.sql).")
    args = parser.parse_args()

    script_dir = Path(__file__).parent
    out_dir = script_dir / "out"
    out_dir.mkdir(parents=True, exist_ok=True)

    mw = _load_module("extract_wotlk_bis", script_dir / "extract_wotlk_bis.py")
    mc = _load_module("extract_classic_talents", script_dir / "extract_classic_talents.py")
    wdata = load_wotlk_talent_data(script_dir)
    desc_to_tab = {meta["description"]: int(tab) for tab, meta in wdata["trees"].items()}

    url_path = Path(args.url_file) if args.url_file else out_dir / f"wotlk_pve_p{args.phase}_urls.txt"
    if not url_path.exists():
        print(f"ERROR: URL cache not found: {url_path}. Run batch_extract_wotlk.py --phase {args.phase} first.",
              file=sys.stderr)
        sys.exit(1)

    # url cache rows: class\tspec\trole\turl
    entries = []
    for line in url_path.read_text(encoding="utf-8").splitlines():
        parts = line.strip().split("\t")
        if len(parts) == 4:
            entries.append(parts)

    out_path = Path(args.out) if args.out else out_dir / "wotlk_pve_talents.sql"

    rows: list[str] = []       # (playerClass, playerSpec, talentId)
    specs_seen: list[str] = []
    errors: list[str] = []

    print(f"Extracting talents for {len(entries)} specs from phase {args.phase} guides ...")
    for idx, (cls, spec, role, url) in enumerate(entries):
        try:
            markup = mw.extract_print_html_payload(mw.fetch_html(url))
            planners = mw.extract_planner_hashes_from_markup(markup)
            key = mw._select_primary_tab(planners.keys(), args.phase)
            digits = planner_talent_digits(planners[key])
            player_class, spec_base = mw.parse_class_and_spec(url)
            # Druid Feral shares one talent tree across two roles (Cat DPS / Bear Tank)
            # but they need different builds — disambiguate the tank as FeralTank.
            talent_base = "FeralTank" if (player_class == "Druid" and spec_base == "Feral"
                                          and role == "tank") else spec_base
            player_spec = f"{talent_base}{args.suffix}"
            ids = mc.decode_build(player_class, digits, wdata["talents"], desc_to_tab)
            if not ids:
                raise ValueError("no talents decoded")
            key_ps = (player_class, player_spec)
            if key_ps in {tuple(r) for r in specs_seen}:
                # same spec_base already covered (shouldn't happen per phase); skip dup
                pass
            specs_seen.append(player_spec)
            for spell_id in ids:
                rows.append(f"('{player_class}', '{player_spec}', {spell_id})")
            pts = [sum(int(c) for c in seg) for seg in digits.split("-")]
            print(f"  [{idx+1:2d}/{len(entries)}] {player_class}/{spec_base} -> {player_spec}  "
                  f"{len(ids)} talents  pts={pts}")
        except Exception as exc:
            print(f"  [{idx+1:2d}/{len(entries)}] {cls}/{spec}/{role}: ERROR {exc}", file=sys.stderr)
            errors.append(f"{cls}/{spec}/{role}: {exc}")
        time.sleep(0.2)

    # Build the file: header + idempotency DELETE + INSERT.
    header = [
        "-- Auto-generated WotLK PvE talent templates (suffix: 80PvE)",
        f"-- Source: phase {args.phase} BiS gear-planner hashes; {len(specs_seen)} specs",
    ]
    idem = idempotency_lines(["talents"], specs_seen)
    lines = list(header)
    if idem:
        lines += [""] + idem
    lines.append("")
    lines.append("/*!40000 ALTER TABLE `mod_npc_talent_template_talents` DISABLE KEYS */;")
    lines.append("INSERT INTO `mod_npc_talent_template_talents` "
                 "(`playerClass`, `playerSpec`, `talentId`) VALUES")
    for i, row in enumerate(rows):
        lines.append(f"{row}{',' if i < len(rows) - 1 else ';'}")
    lines.append("/*!40000 ALTER TABLE `mod_npc_talent_template_talents` ENABLE KEYS */;")

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nWrote {len(rows)} talent rows for {len(specs_seen)} specs to {out_path}")
    if errors:
        print(f"  {len(errors)} spec(s) had errors:")
        for e in errors:
            print(f"    {e}")


if __name__ == "__main__":
    main()
