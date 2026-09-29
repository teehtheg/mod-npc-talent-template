# Wowhead BiS SQL Tool

Extracts Wowhead BiS guides and emits SQL rows compatible with `mod-npc-talent-template`.

Supported expansions:
- **Classic** (level 60) — `[h4]` section parsing, 12 specs, single end-game BiS list
- **TBC** (level 70) — `[h3 toc]` table parsing, ~17 specs, phases 0–5
- **WotLK** (level 80) — gear-planner hash decoding, all 31 specs, phases 1–4

---

## Deploying generated SQL into the module

Generators write to `out/` with logical names. The module's SQL updater applies
files in ascending **filename** order (compared by filename only), so the data
files must sort *after* the schema-creation file and the `category` / `categoryOrder` migrations.
`deploy_to_base.py` copies `out/` into `../sql/db-characters/base/` with a
numeric-prefix scheme that enforces that order:

```powershell
python .\deploy_to_base.py            # copy out/*.sql -> base/ with prefixes
python .\deploy_to_base.py --dry-run  # preview only
```

| Prefix | Files | Source |
|--------|-------|--------|
| `00_` `01_` `02_` | schema + `category` / `categoryOrder` migrations | hand-maintained |
| `10_` `11_` | S6 / T6 base sets | hand-maintained |
| `20_`–`24_` | classic talents + BiS | generated |
| `30_`–`35_` | tbc BiS (P0–P5) | generated |
| `40_`–`43_` | wotlk BiS | generated |
| `44_` | wotlk PvE talents (`extract_wotlk_talents.py`) | generated |

Run this **after** a fresh regenerate — a full regenerate → deploy cycle
reproduces the committed `base/` files exactly.

## Menu categories

Each batch run writes its builds into one gossip sub-menu: `category` is the label
(`Classic Phase N`, `TBC Pre-Raid` / `TBC Phase N`, `WotLK Phase N`) and
`categoryOrder` its position (`100 + N` Classic, `200 + N` TBC, `300 + N` WotLK; the
hand-maintained sets use 290 = `TBC Tier 6`, 390 = `WotLK PvP S6`). The NPC sorts by
`categoryOrder`, so the menu order no longer depends on which file was applied last.

## Idempotency

Every generated file begins with a `DELETE` block that removes exactly that
file's `playerSpec` rows (from the tables it writes) before the INSERTs. The
`mod_npc_talent_template_*` tables have no unique key and the updater applies
each file once by hash, so without this a changed / renamed / re-imported file
would silently append duplicate rows. The block is emitted by
`sql_idempotency.py` (shared by all generators) — don't hand-edit it into the
output; regenerate instead.

---

## Classic — batch_extract_classic.py (recommended)

Extracts Classic BiS guides for all 12 available specs into one combined SQL file.
Classic guides on `classic.wowhead.com` show end-game (Naxx-era / Season of Mastery)
BiS lists. No phase-specific guide URLs exist — one list per spec.

```powershell
# From wow-bis-sql-tool/
python .\batch_extract_classic.py --out ".\out\classic_pve_bis.sql"
```

**Re-run without re-verifying URLs:**
```powershell
python .\batch_extract_classic.py --skip-discover
```

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--out` | `out/classic_pve_bis.sql` | Output SQL file |
| `--url-file` | `out/classic_pve_urls.txt` | Verified slug cache |
| `--skip-discover` | off | Skip URL probing, use built-in list |
| `--talent-suffix` | `60PvE` | Talent/glyph override suffix fragment |

### Coverage

12 specs confirmed available on `classic.wowhead.com/guides/`:

| Class | Spec | `playerSpec` |
|-------|------|-------------|
| Druid | Cat (Feral DPS) | `Cat60PvEBiS` |
| Druid | Bear (Feral Tank) | `Bear60PvEBiS` |
| Druid | Balance | `Balance60PvEBiS` |
| Hunter | Marksmanship | `Marksmanship60PvEBiS` |
| Mage | Fire | `Fire60PvEBiS` |
| Priest | Shadow | `Shadow60PvEBiS` |
| Rogue | Combat | `Combat60PvEBiS` |
| Shaman | Elemental | `Elemental60PvEBiS` |
| Shaman | Enhancement | `Enhancement60PvEBiS` |
| Warlock | Affliction | `Affliction60PvEBiS` |
| Warrior | Fury | `Fury60PvEBiS` |
| Warrior | Protection | `Protection60PvEBiS` |

Not available: Arms Warrior, Holy/Ret Paladin, Holy/Disc Priest, Resto Druid/Shaman.

Talent/glyph overrides reference `{spec}60PvE` entries in the base Classic SQL.

---

## Classic — extract_classic_bis.py (single URL)

```powershell
python .\extract_classic_bis.py `
  --url "https://classic.wowhead.com/guides/feral-druid-dps-gear-bis-classic-wow" `
  --player-class "Druid" `
  --player-spec "Cat" `
  --suffix "60PvEBiS" `
  --out ".\out\classic_feral_cat_bis.sql"
```

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--url` | *(required)* | `classic.wowhead.com` guide URL |
| `--player-class` | *(required)* | SQL class name (e.g. `"Druid"`) |
| `--player-spec` | *(required)* | SQL spec name (e.g. `"Cat"`, `"Fire"`) |
| `--suffix` | `60PvEBiS` | playerSpec suffix |
| `--talent-suffix` | `{spec}60PvE` | Full talent/glyph override spec name |
| `--category` | *(empty)* | Gossip sub-menu label; empty = root menu |
| `--category-order` | `0` | Sort key of the category in the gossip menu (lower = higher up) |
| `--out` | `out/classic_generated.sql` | Output SQL file |

---

## TBC — batch_extract_tbc.py (recommended)

Discovers TBC BiS guide URLs for all available specs for a given phase, then extracts them
into one combined SQL file.

**Phase numbers:**  0 = Pre-raid, 1 = Kara/Gruul/Mag, 2 = SSC/TK, 3 = BT/Hyjal, 4 = ZA, 5 = SWP

```powershell
# From wow-bis-sql-tool/
python .\batch_extract_tbc.py --phase 3 --out ".\out\tbc_pve_p3_bis.sql"
```

**Re-run extraction without re-discovering URLs:**
```powershell
python .\batch_extract_tbc.py --phase 3 --skip-discover
```

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--phase` | `3` | Phase number (0–5) |
| `--out` | `out/tbc_pve_p{phase}_bis.sql` | Output SQL file |
| `--url-file` | `out/tbc_pve_p{phase}_urls.txt` | URL discovery cache |
| `--skip-discover` | off | Skip URL probing, load from `--url-file` |
| `--talent-suffix` | `70PvE` | Talent/glyph override suffix fragment |

### Coverage

All 27 TBC specs have guides on `tbc.wowhead.com` for every phase 0–5.

**Death Knights** didn't exist in TBC, so no guides exist for them. `DERIVED_SPECS`
builds them from the Warrior guides of the same phase: Blood from Protection (armor)
plus Arms (weapons, since DKs can't use shields and tanked with a two-hander), Frost
from Fury (dual-wield), Unholy from Arms. Items restricted to other classes (e.g. the
Warrior tier set, Warglaives) are skipped for the next-ranked row, only weapon types
a DK can use are kept (axes, maces, swords, polearms), and the ranged slot gets
Sigil of the Dark Rider, the only sigil below level 80. Leather/mail picks from the
Warrior guides are kept (DKs can wear them). Talents and glyphs reuse the
`{Blood,Frost,Unholy}70PvE` templates from the hand-maintained T6 file, which no
longer carries a DK "Tier 6" build (there was no DK tier set).

Discovery tries:
1. The spec's `/tbc/guide/classes/` index page, extracting the phase URL from its
   `[cta-button=...]` links (only while the page still shows TBC content).
2. A direct probe of `tbc.wowhead.com/guides/{spec}-{class}-{role}-{phase-slug}-...`.
3. The consolidated class/role index page (healers, Prot Paladin, Rogues).

Wowhead throttles bursts with HTTP 403. Requests back off and retry; if it keeps
refusing, discovery **stops** (exit code 2) instead of recording the spec as missing.

**Offline URL caches (recommended):** `build_url_caches.py` writes
`out/tbc_pve_p{0..5}_urls.txt` from the known URL patterns without any requests —
including `URL_OVERRIDES` for guides Wowhead has merged (Fury P0 now shares the
combined Arms/Fury pre-raid page). Then extract each phase with `--skip-discover`:

```powershell
python .\build_url_caches.py
foreach ($p in 0..5) { python .\batch_extract_tbc.py --phase $p --skip-discover }
python .\deploy_to_base.py
```

**Phase filter:** Wowhead keeps the pre-raid guides current through later phases
(they now rank Phase 3 arena, honor and raid-pattern crafted gear), so every build
skips items from a later phase than its own and takes the next-ranked row instead.
The phase is the TBC Classic content phase on the item's Wowhead tooltip. Phase 1,
the launch, counts as pre-raid unless every Wowhead source of the item is a phase-1
raid (Karazhan, Gruul, Magtheridon) or world boss. Results are cached in
`out/tbc_item_phases.json`; `ITEM_PHASE` in `extract_tbc_bis.py` dates the Brewfest
trinkets, which have no content phase.

The batch run prints a `WARN` line per skipped item (not in the 3.3.5a
`item_template`, wrong slot type, or from a later phase) and a `MISSING` line per
spec with empty slots — review those before deploying.

### Output spec naming

| Spec | `playerSpec` example (phase 3) |
|------|-------------------------------|
| Cat  | `Cat70PvEP3BiS` |
| Bear | `Bear70PvEP3BiS` |
| Arms | `Arms70PvEP3BiS` |
| Holy (Paladin) | `Holy70PvEP3BiS` |

Talent/glyph overrides reference existing `{spec}70PvE` entries (e.g. the T6 base SQL).
Arms has no `Arms70PvE` talent template, so its talents use `ArmsAxe70PvE` (axe or
polearm main hand) or `ArmsSword70PvE` (anything else).

---

## TBC — extract_tbc_bis.py (single URL)

```powershell
python .\extract_tbc_bis.py `
  --url "https://tbc.wowhead.com/guides/feral-druid-dps-bt-hyjal-phase-3-best-in-slot-gear-burning-crusade" `
  --player-class "Druid" `
  --player-spec "Cat" `
  --suffix "70PvEP3BiS" `
  --out ".\out\tbc_feral_cat_p3_bis.sql"
```

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--url` | *(required)* | `tbc.wowhead.com` guide URL |
| `--player-class` | *(required)* | SQL class name (e.g. `"Druid"`) |
| `--player-spec` | *(required)* | SQL spec name (e.g. `"Cat"`, `"Bear"`) |
| `--suffix` | `70PvEP3BiS` | playerSpec suffix |
| `--talent-suffix` | `{spec}70PvE` | Full talent/glyph override spec name |
| `--category` | *(empty)* | Gossip sub-menu label; empty = root menu |
| `--category-order` | `0` | Sort key of the category in the gossip menu (lower = higher up) |
| `--out` | `out/tbc_generated.sql` | Output SQL file |

---

## WotLK — batch_extract.py (recommended)

Discovers all 31 WotLK spec BiS guide URLs, verifies each has a gear-planner payload,
then extracts them all into one SQL file.

```powershell
# From wow-bis-sql-tool/
python .\batch_extract_wotlk.py --phase 4 --out ".\out\wotlk_pve_p4_bis.sql"
```

The BiS index rows reference `talentOverride={spec}80PvE` (generated by
`extract_wotlk_talents.py`) and `glyphOverride={spec}80PvE` (generated by
`extract_wotlk_glyphs.py`). Override the suffixes with `--talent-suffix` /
`--glyph-suffix` if needed.

### WotLK PvE talents — extract_wotlk_talents.py

Generates `{spec}80PvE` talent templates for all 31 specs. The recommended talent
build is embedded in each BiS guide's **gear-planner hash** (gear + talents +
glyphs), so this reuses the same scrape — no talent-guide pages, no manual hashes:

```powershell
python .\extract_wotlk_talents.py --phase 4    # -> out/wotlk_pve_talents.sql
```

### WotLK PvE glyphs — extract_wotlk_glyphs.py

Generates `{spec}80PvE` glyph templates. The `glyph` column needs GlyphProperties
DBC IDs, so this parses the client `GlyphProperties.dbc` (ID↔SpellID), reads each
spec's `{role}-talent-builds-glyphs-pve` guide, picks the first 3 major + 3 minor
glyphs (typed via the DBC), and assigns slots (major = 0/3/5, minor = 1/2/4):

```powershell
python .\extract_wotlk_glyphs.py --dbc "C:\path\to\Data\dbc\GlyphProperties.dbc"
# -> out/wotlk_pve_glyphs.sql  (deploy -> 45_wotlk_pve_glyphs.sql)
```

Selection is best-effort (guide order); review specs the tool warns about.

**Re-run extraction without re-probing URLs** (saves ~3 min):
```powershell
python .\batch_extract_wotlk.py --phase 4 --skip-discover
```

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--expansion` | `wotlk` | Expansion slug |
| `--phase` | `4` | Phase number (1–4 for WotLK) |
| `--mode` | `pve` | `pve` or `pvp` |
| `--out` | `out/{expansion}_{mode}_p{phase}_bis.sql` | Output SQL file |
| `--url-file` | `out/{expansion}_{mode}_p{phase}_urls.txt` | URL discovery cache |
| `--skip-discover` | off | Skip URL probing, load from `--url-file` |
| `--talent-suffix` | `80PvP` | Talent/glyph override spec suffix |

### Output spec naming

| Role | Example `playerSpec` |
|------|----------------------|
| DPS | `Destruction80PvEP4BiS` |
| Tank | `Blood80PvEP4BiSTank` |
| Healer | `Holy80PvEP4BiSHeal` |

---

## WotLK — extract_wowhead_bis.py (single URL)

```powershell
python .\extract_wotlk_bis.py `
  --url "https://www.wowhead.com/wotlk/guide/classes/warlock/destruction/dps-bis-gear-pve-phase-4" `
  --out ".\out\warlock_destruction_pve_p4_bis.sql"
```

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--url` | *(required)* | Wowhead guide URL |
| `--suffix` | `80PvEP4BiS` | playerSpec suffix |
| `--talent-override-suffix` | `80PvP` | Talent/glyph override spec suffix |
| `--category` | *(empty)* | Gossip sub-menu label; empty = root menu |
| `--category-order` | `0` | Sort key of the category in the gossip menu (lower = higher up) |
| `--out` | `out/generated_spec.sql` | Output SQL file |

---

## Implementation notes

### WotLK
- Uses gear-planner binary hash (GearPlannerWrath.js format) embedded in guide pages.
- Gem item IDs resolved to SpellItemEnchantment IDs via `wotlkdb.com` (~1 request per unique gem).
- **Permanent enchant**: the hash encodes the enchant's *spell* ID (Spell.dbc, e.g.
  `60691` = "Enchant 2H Weapon - Massacre"), but the `enchant` column is consumed by
  `Item::SetEnchantment` and must be a **SpellItemEnchantment ID** (`60691` → `3827`).
  `fetch_spell_enchant_id()` translates it (seed map of all known WotLK enchants +
  live `wotlkdb.com` `?spell=N` → `enchantment=M` fallback). Without this the enchant
  silently never applies in-game. (The local `spellitemenchantment_dbc.sql` is an empty
  schema stub — AzerothCore loads DBC from client files at runtime — so it can't be
  used for translation.)
- Eternal Belt Buckle (waist slot, inv_slot 6): 3rd gem → `prismaticEnchant`; `socket3 = 0`.
- `bonusEnchant` always `0` (fires in-game automatically).

#### Repairing older output — `translate_enchants_inplace.py`
Output generated before the enchant translation existed carries raw spell IDs in the
`enchant` column. To fix those files without a full re-scrape (freezing items/gems):

```powershell
python .\translate_enchants_inplace.py            # all out/wotlk_pve_p*_bis.sql
python .\translate_enchants_inplace.py --dry-run  # report only
```

Rewrites ONLY the enchant column (values ≥ 10000 = spell IDs → SIE IDs; smaller values
and `0` left untouched), reusing the generator's seed map as the single source of truth.

### Enchants for level 60 / 70 (Classic / TBC)
- Level-60 and level-70 builds get **era-appropriate** enchants (Classic-era for 60,
  TBC-era for 70) as `SpellItemEnchantment` ids, curated per class/spec in
  **`era_enchants.py`** and verified to exist in the 3.3.5a client (via wotlkdb
  `?enchantment=N`). Every enchantable slot is filled; neck/waist/rings/trinkets
  stay 0. Level 80 is not covered here (WotLK enchants come from the gear planner).
- The Classic/TBC generators call `era_enchants.enchants_for(level, class, spec)`.
- To enchant already-generated Classic/TBC SQL without re-scraping (freeze items),
  run **`apply_era_enchants_inplace.py`** — rewrites only the enchant column.
- **TBC gems**: TBC gear has sockets (Classic has none; level-80 gems come from the
  gear planner). `era_enchants.gems_for()` colour-matches a role-appropriate gem to
  each socket (colours in `tbc_socket_colors.json`, exported from item_template) so
  socket bonuses fire. The TBC generator fills sockets; repair existing output with
  **`apply_tbc_gems_inplace.py`** (rewrites only socket1/2/3).
- `add_missing_ranged_inplace.py` / `add_missing_trinkets_inplace.py` were one-off
  repairs for output generated before the extractor handled ranged sections and
  unlabelled trinket tables. The extractor now covers both (see TBC below), so a
  regenerate makes them unnecessary.

### TBC
- No gear-planner hash — uses `[h3 toc="SlotName"]` + `[table]` markup from `tbc.wowhead.com`.
- Row ranking per section: rows labelled `BiS`/`Best…` first, then the other rows in
  page order, rows labelled `PvP` last. Rings and trinkets take the two best distinct
  items, so a section with a single "BiS" row still fills both slots.
- Every item is checked against `item_template` (`item_info.py`, which parses the core
  repo's `data/sql/base/db_world/item_template.sql` once and caches
  `out/item_info_cache.json`). Items missing from the 3.3.5a client (e.g. TBC
  Anniversary ids > 200000) or with the wrong InventoryType for the slot are skipped.
- Weapons are resolved by **item type**, not by heading: every weapon / off-hand /
  shield / ranged / relic section goes into one pool ("Off-Hands", "Shields &
  Offhands", "Melee Weapons", … are matched by keyword). Main hand = best
  main-hand-capable item (Arms/Ret/Feral prefer a two-hander, Fury/Enhancement/Rogues
  a one-hander); a two-hander leaves the off-hand empty; otherwise the off-hand comes
  from a dedicated off-hand/shield section first, then from a combined "Weapons"
  table — one-handers only for dual-wield specs. So a "Weapons" table whose second
  row is a PvP mace can no longer take the shield's place. Ranged/relic prefers a
  ranged section, else a bow/gun/wand folded into "Weapons".
- Enchants and gems come from `era_enchants.py`; held-in-off-hand items get no enchant.
  Socket colours come from `tbc_socket_colors.json`, falling back to `item_template`
  for items not in it (ZA/Sunwell gear).
- Uses `@RACEMASK_ALL` (same gear for all races, unlike WotLK).

### Classic
- No gear-planner hash or phase-specific URLs — uses `[h4]Slot for Class...` section headers
  from `classic.wowhead.com/guides/`.
- Most specs use `[h4]` for all slots; mage and similar use `[h3 toc="Weapons"]` sections
  with slot names embedded in the content title (e.g. "Offhands for Mage...").
- Rank labels: "Best", "BiS", "Best Overall", "Best Mitigation". Falls back to first row
  when no ranked label exists (tank guides).
- Rings and Trinkets: first two ranked items from the section fill both slots.
- Enchants and gems default to `0`.
- Uses `@RACEMASK_ALL` and level 60/69.

### Both TBC and Classic
- Each spec block gets `SET @ACTION = COALESCE(MAX(...)+1, 0)` for auto-incrementing
  `gossipAction` IDs when SQL is imported sequentially.