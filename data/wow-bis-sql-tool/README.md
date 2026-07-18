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
files must sort *after* the schema-creation file and the `category` migration.
`deploy_to_base.py` copies `out/` into `../sql/db-characters/base/` with a
numeric-prefix scheme that enforces that order:

```powershell
python .\deploy_to_base.py            # copy out/*.sql -> base/ with prefixes
python .\deploy_to_base.py --dry-run  # preview only
```

| Prefix | Files | Source |
|--------|-------|--------|
| `00_` `01_` | schema + `category` migration | hand-maintained |
| `10_` `11_` | S6 / T6 base sets | hand-maintained |
| `20_`–`24_` | classic talents + BiS | generated |
| `30_`–`33_` | tbc BiS | generated |
| `40_`–`43_` | wotlk BiS | generated |
| `44_` | wotlk PvE talents (`extract_wotlk_talents.py`) | generated |

Run this **after** a fresh regenerate — a full regenerate → deploy cycle
reproduces the committed `base/` files exactly.

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

Not all TBC specs have BiS guides on Wowhead. Discovery uses two strategies:
1. For specs whose `/tbc/guide/classes/` index page still shows TBC content: extracts the
   phase-specific URL from the embedded `[cta-button=...]` links.
2. For other specs: probes `tbc.wowhead.com/guides/{spec}-{class}-{role}-{phase-slug}-...`

Confirmed available for phase 3: Mage (×3), Warrior (×3), Druid (Cat/Bear/Balance),
Shaman (Ele/Enh), Paladin (Holy/Ret), Shadow Priest.  
Not available: Hunters, Rogues, Warlocks, Druid/Shaman/Paladin/Priest healers/tanks.

### Output spec naming

| Spec | `playerSpec` example (phase 3) |
|------|-------------------------------|
| Cat  | `Cat70PvEP3BiS` |
| Bear | `Bear70PvEP3BiS` |
| Arms | `Arms70PvEP3BiS` |
| Holy (Paladin) | `Holy70PvEP3BiS` |

Talent/glyph overrides reference existing `{spec}70PvE` entries (e.g. the T6 base SQL).

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
`extract_wotlk_talents.py`) and `glyphOverride={spec}80PvP` (interim — PvE glyph
IDs aren't generated yet; see below). Override the suffixes with `--talent-suffix`
/ `--glyph-suffix` if needed.

### WotLK PvE talents — extract_wotlk_talents.py

Generates `{spec}80PvE` talent templates for all 31 specs. The recommended talent
build is embedded in each BiS guide's **gear-planner hash** (gear + talents +
glyphs), so this reuses the same scrape — no talent-guide pages, no manual hashes:

```powershell
python .\extract_wotlk_talents.py --phase 4    # -> out/wotlk_pve_talents.sql
```

Glyphs are NOT generated (the `glyph` column needs GlyphProperties.dbc IDs, which
are client-side); the index rows keep `{spec}80PvP` glyphs as a documented interim.

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
- Ranged/relic slot (pos 17): some guides head that section "Ranged Weapons" /
  "Wands" / "Thrown", which the slot map now covers, so hunters get their bow/gun
  (+ scope), casters their wand, rogues their thrown weapon, etc. Repair existing
  output with **`add_missing_ranged_inplace.py`** (re-fetches each spec missing a
  pos-17 row and inserts just that row). It falls back through three strategies:
  the BiS-labeled ranged table, then any item under a ranged-type heading, then —
  for pre-raid/Kara hunter guides that fold the ranged weapon into a combined
  "Weapons" section — the first Bow/Gun/Crossbow item there (looked up by subclass).

- Row-label fallback: `extract_bis_by_slot` picks the row whose first cell starts
  with `BiS`/`Best`; if a slot's table has no such row (some tank/healer trinket &
  weapon tables rank by stat, e.g. "51 Stam + Proc"), it falls back to the table's
  first data row. Repair existing output missing trinkets with
  **`add_missing_trinkets_inplace.py`** (inserts pos 12/13 rows; keyed by class+spec
  since Protection/Holy are shared across classes).

### TBC
- No gear-planner hash — uses `[h3 toc="SlotName"]` + `[table]` markup from `tbc.wowhead.com`.
- Extracts the first row labeled "BiS*" per slot table as the BiS item.
- Rings and Trinkets: two tables per section → fills both slot entries.
- Enchants and gems default to `0` (not available from guide text).
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