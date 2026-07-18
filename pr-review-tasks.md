# PR Review Tasks — CodeRabbit (Run ID bdeff47e)

Tracking the 7 actionable findings from the CodeRabbit review. **No fixes applied yet** — this is a triage/planning doc.

> Note: Most data findings originate in the **extractor/scraper tooling** (not part of this PR). Where that's the case, the real fix is in the generator + a regenerate, not hand-editing the SQL. Marked with 🛠️ generator below.

---

## 🔴 Critical

- [x] **Protection Warrior P4 gear is Phase 3, not Phase 4** ✅ DONE (generator, regen-safe)
  - Root cause: the Warrior Prot P4 guide wraps its planners in `[tabs name="BiS"]` **container** tags — one per section (`"Phase 4 BiS"`, `"Sindragosa Progression (P3 BiS)"`, `"Lich King Progression (P3 BiS)"`). The extractor's tab regex `\[tab[^\]]*name=` also matched the `[tabs …]` container (all named "BiS"), so the three collapsed under one `"bis"` key and it decoded the **last** one — a P3 progression planner. (Paladin's guide has no P3 tabs, which is why it was correct.)
  - **Fixes in `extract_wotlk_bis.py`:** (1) tab regex now matches only inner `[tab …]`, not the `[tabs …]` container (`\[tab(?=[\s\]])…`); (2) added `_select_primary_tab()` — prefers the `"phase N bis"` tab, then any non-"progression" tab — so per-phase BiS is chosen over progression/alt tabs.
  - **Verified via live re-scrape:** Warrior Prot P4 now decodes the `"phase 4 bis"` tab → head `50640`, chest `51220`, MH `50718` (Phase 4 ICC). Regression subset across 8 P4 specs: all heads are 50xxx/51xxx (Phase 4); varied tab shapes (default / alliance+horde / phase-named / progression) all handled; no P3 leakage.
  - **Realized in committed SQL ✅:** regenerated all four WotLK phases (`batch_extract_wotlk.py --phase 1..4 --skip-discover`) and deployed to `40_`–`43_`. Verified Warrior Prot P4 head is now `50640` (Phase 4). The regen also improved DK Frost/Unholy P4 (same tab-selection fix). Enchants/gems come from the planner, so no inplace step needed for WotLK.

---

## 🟠 Major — schema / migration

- [x] **Add upgrade migration for the new `category` column** ✅ DONE
  - Added `data/sql/db-characters/base/01_mod_npc_talent_template_index_category.sql`.
  - Idempotent guarded migration (checks `INFORMATION_SCHEMA`; uses `PREPARE`/`EXECUTE`, no stored procedure so it only needs `ALTER` privilege). Adds `category VARCHAR(64) NOT NULL DEFAULT '' AFTER maxLevel` only when the table exists and the column does not.
  - Ordered `01_` (right after `00_npc_talent_template.sql` schema, before every data file) so it runs before any INSERT that references `category`. See ordering item below.
  - Kept `category` in the base CREATE TABLE (correct for fresh installs); the migration is a no-op there.
  - **Verified** on the live DB against a throwaway table across all four cases: existing install (column added `varchar(64) NOT NULL DEFAULT ''`), idempotent re-run, fresh (no-op), table-absent (no-op). Confirmed the updater applies module files via the `mysql` client (`ApplyFile`), so `PREPARE`/`EXECUTE` behave as tested.

---

## 🟠 Major — PvE templates referencing PvP talents/glyphs (cross-cutting) 🛠️ generator

- [x] **WotLK PvE sets: talentOverride now `*80PvE` (generator, regen-safe); glyphOverride stays `*80PvP` (documented interim)** ✅ DONE
  - **Talents (fixed):** new `extract_wotlk_talents.py` generates `{spec}80PvE` talent sets by decoding the talent bytes embedded in each spec's **BiS gear-planner hash** (already fetched for gear — so fully automated, phase-correct, no manual hashes, no fragile talent-guide scraping). Validated on Mage Frost (18/0/53), Priest Holy (18/53), Warrior Prot (15/5/51) — all correct deep-tree builds; Warrior Prot matches the hand-checked build (contains Shockwave). Reuses `decode_build` + `talents_wotlk_raw.js`.
  - **Generator wiring:** `render_sql` now takes a separate `glyph_override_suffix`; `batch_extract_wotlk.py` emits `talentOverride={spec}80PvE`, `glyphOverride={spec}80PvP`. `deploy_to_base.py` maps `wotlk_pve_talents.sql → 44_wotlk_pve_talents.sql`.
  - **Glyphs — DONE ✅ (no longer interim):** the `glyph` column needs GlyphProperties.dbc IDs; the DB stub is empty, but the client `GlyphProperties.dbc` is on disk in the server build (`…/Data/dbc/`). New `extract_wotlk_glyphs.py` parses it (ID↔SpellID), reads each spec's `{role}-talent-builds-glyphs-pve` guide, picks the first 3 major-type + 3 minor-type glyphs (typed via the DBC), maps item→spellId→GlyphProperties ID, and assigns slots (major=0,3,5 / minor=1,2,4, per existing-data convention). Emits `45_wotlk_pve_glyphs.sql` (184 rows, 31 specs). `glyphOverride` flipped `80PvP→80PvE` (+ `FeralTank80PvE` for the bear). Spot-checked names (Frost: Eternal Water/Frostbolt/Molten Armor; Prot: Blocking/Vigilance/Devastate — all correct-tier). Cross-ref 31/31, idempotent (184→184). Best-effort selection (guide-order); a couple of specs (Paladin) have only 2 minors — expected (limited pool).
  - **Feral Cat/Bear fix ✅ (CodeRabbit follow-up):** Druid Feral shares one talent tree across Cat (DPS) and Bear (Tank); both were collapsing onto `Feral80PvE` (58 merged talent rows). Now the Bear/tank uses `FeralTank80PvE` — separate talent set + the `Feral…BiSTank` index rows reference it. Fixed in the generator (`extract_wotlk_talents.py`, `render_sql` `talent_spec_base`, `batch_extract_wotlk.py`) and committed SQL. Also applied the optional cat→bear gossip icon: `SPEC_ICONS[("Druid","FeralTank")]="ability_racial_bearform"`, icon lookup uses the talent base; the 8 Feral tank index rows now show the bear-form icon (cat rows unchanged). Re-validated: 31/31 sets referenced⇄provided, idempotent.
  - **Realized in committed SQL ✅:** ran `extract_wotlk_talents.py --phase 4` → generated `44_wotlk_pve_talents.sql` (807 rows, 31 talent sets incl. FeralTank across all 31 specs). Flipped `talentOverride` `80PvP→80PvE` in the four WotLK BiS files (glyphOverride kept `80PvP`). Verified: every referenced talent set is provided (30/30, none missing/orphaned), and the file applies cleanly + idempotently on the live DB (807→807).

<details><summary>original finding</summary>

  - No `*80PvE` talent/glyph data exists; all WotLK BiS index rows reference `*80PvP` (the only 80-level talent data, from the S6 file).
  - **Feasibility investigation (done):**
    - Talent guide URL pattern: `/wotlk/guide/classes/{class}/{spec}/{role}-talent-builds-glyphs-pve`.
    - Build encoding: talent-calc hash `tree1-tree2-tree3_glyphs` (points-per-talent per tree, then a glyph string).
    - **Reusable decoder**: `extract_classic_talents.decode_build()` + `out/talents_wotlk_raw.js` (has `talents` trees, `trees` desc→tab, and a `glyphs` array with `spellId`/`itemId`). POC decoded a warrior build → 26 talent spell IDs successfully.
  - **Correction:** the dedicated per-spec builds DO exist and render fine (user confirmed). The difficulty is *my scraping* isolating the canonical level-80 build, not the source:
    - `.../{spec}/tank-talent-builds-glyphs-pve` → surfaced Arms builds (wrong page/variant).
    - `.../{spec}/tank-talent-builds-pve` → surfaced TBC (61-pt) / classic (51-pt) builds with `/tbc/` calc links (cross-expansion reference content).
    - So the raw HTML carries many cross-expansion/variant talent-calc links; picking the one canonical 71-point WotLK build needs care (or curation).
  - **TALENTS — validated end-to-end ✅:** a WotLK `/wotlk/talent-calc/` hash decodes correctly via `decode_build` + `talents_wotlk_raw.js`. Confirmed with a real 71-point (15/5/51) Prot build → 27 spell IDs ending in `46968` = Shockwave (the lvl-80 Prot capstone). So PvE **talents** can be generated reliably from per-spec WotLK hashes.
  - **GLYPHS — blocked ⚠️:** `mod_npc_talent_template_glyphs.glyph` is a **GlyphProperties.dbc ID** (C++ does `sGlyphPropertiesStore.LookupEntry(glyphID)`), but the wowhead data only exposes glyph `spellId`/`itemId`, and GlyphProperties.dbc isn't in the repo (client-side, loaded at runtime). So glyphs can't be auto-derived here without that mapping.
  - **Path:** build the talent scaffold now (reliable). For glyphs, need either the GlyphProperties→spell mapping (from the client DBC / a static 3.3.5a reference), curation, or keep the existing `80PvP` glyphs as a documented interim while talents become proper PvE.
</details>

---

## 🟠 Major — incomplete gear (missing equipment rows) 🛠️ generator

- [x] **TBC P0 Priest templates missing rows** ✅ DONE (stale data; extractor already correct)
  - Verified with a live re-scrape: the **current** TBC extractor produces a complete slot set for Discipline/Holy/Shadow P0 (`missing=[]`). The committed gaps are stale output from an older extractor, so a regenerate fixes them — no extractor change needed for priests.

- [x] **TBC P2 melee weapon rows missing** ✅ DONE (generator, regen-safe)
  - Root cause: guides label weapon sections `"Main Hand Weapons"`/`"Off Hand Weapons"`/`"Two-Handed Weapons"`, none of which were in `TOC_TO_INV_SLOTS` (only `"Main Hand"`, `"Off Hand"`, `"2H Weapons"`) — and TOC matching is exact, so those slots were silently dropped. Enhancement's totem was also lost to a `[/tr}` typo in the source markup.
  - **Fixes in `extract_tbc_bis.py`:** (1) added the missing weapon heading variants; (2) added a **2H rule** (`_TWO_HANDED_TOC` + post-pass) so a two-handed main-hand drops any off-hand a secondary "dual-wield alternative" section added; (3) made the row-closing tag tolerant (`[/tr]` or `[/tr}`).
  - **Verified via live re-scrape:** Arms → 2H `29993`, no off-hand (correct); Fury → dual-wield `28439`+`30082`; Enhancement → `29996`+`28439`+totem `27815`. Regression sweep P0–P3: no off-hand==main-hand dup, 2H specs correctly have no off-hand, no off-hands wrongly dropped.
  - Committed SQL corrects on the next re-scrape + `deploy_to_base.py` (item IDs/enchants/gems come from the full pipeline; not hand-patched).
  - Noticed (pre-existing, out of scope): some hunters miss pos 17 (ranged), several P3 casters miss pos 11 (ring).

---

## 🟠 Major — single-item fix

- [x] **Bear P4 relic slot duplicates main-hand** ✅ DONE (generator, regen-safe)
  - Root cause: the classic extractor's `_fill_slots` filled the combined "Weapons and Idols" section's relic slot with the *first* BiS item (the weapon `9449`) when the main-hand was already set by a separate "Weapons" section. Fixed to fill **positionally** (`extract_classic_bis.py`), so the relic slot stays empty when the guide BiS-tags no idol. Verified via live re-scrape: Bear P4 relic is now unset (was `9449`).
  - **CodeRabbit's suggested value `23198` is wrong**: that idol is an AQ20 (Phase 5) item, so it doesn't exist in Phase 4 (BWL/ZG) — which is exactly why the P4 guide lists no idol. Correct P4 value is *empty*, not `23198`.
  - Committed SQL: removed the invalid `Bear60PvEP4BiS` pos-17 row from `22_classic_pve_p4_bis.sql` (a weapon can't equip in the relic slot anyway). Matches fixed-generator output.

- [x] **(NEW, discovered) Tank off-hand gets a weapon instead of a shield** ✅ DONE (generator, regen-safe)
  - In the classic extractor, "Off-Hand Weapons" and "Shields" both map to the off-hand slot; whichever the guide listed first won, so a Protection tank got an off-hand weapon (`23456`) where a shield belongs.
  - **Fix (`extract_classic_bis.py`):** a dedicated "Shields" section now force-overrides the off-hand slot (`_SHIELD_TOC` + `force` arg on `_fill_slots`), so a shield wins regardless of section order.
  - **Verified via live re-scrape:** Warrior Protection P4/P5/P6 now get shields (`18825`/`18825`/`23043`); Paladin Protection too; Fury/other dual-wielders unchanged. No regressions.
  - Remaining minor quirk (separate, low priority): Combat rogue P4 has off-hand == main-hand (`18838`) — a dual-wield same-item nuance (no shield involved), not the tank-shield bug.

---

## 🔴 Discovered while fixing #2 (NOT in CodeRabbit's list)

- [x] **Fresh-install ordering bug** ✅ DONE
  - The updater applies `.sql` files in ascending **filename** order (`UpdateFetcher::PathCompare`, filename only). The BiS files create no tables themselves; only `npc_talent_template.sql` creates them. `classic_pve_*.sql` (c) sorted **before** `npc_talent_template.sql` (n) → on a fresh DB the classic inserts ran before the tables existed → hard failure. Latent because it was only tested on installs that already had the tables.
  - **Fixed** by adopting an explicit numeric-prefix scheme across all files:
    `00_` schema → `01_` category migration → `1x_` existing data → `2x_` classic → `3x_` tbc → `4x_` wotlk. Verified with `LC_ALL=C sort` (matches the updater's byte-wise order).

- [x] **🔴 No idempotency + no key on `mod_npc_talent_template_index` → duplicate rows on upgrade** ✅ DONE
  - Root cause: no data file had a `DELETE`; new BiS files append via `@ACTION = MAX(gossipAction)+1`; the table has no primary/unique key. Any re-apply (hash change or rename) silently duplicated rows.
  - **Fixed**: prepended each of the 15 data files with an idempotency block that `DELETE`s exactly that file's `playerSpec` set from exactly the tables it populates, placed before any `SET @ACTION` so action IDs stay stable on re-runs.
  - Safe because: every `playerSpec` is unique to one file (verified), so no file clobbers another; specs shared across classes within a file (e.g. `Frost80PvPS6` = Mage + DK) are deleted and re-inserted as a whole.
  - **Verified** on the live DB (throwaway `zzz_test_*` tables built from the real schema): applying `10_` and `21_` twice left row counts identical (60/1357/839/180 and 94/1617/…), confirming no growth on re-apply.
  - Still open (separate, lower priority): the index table has **no unique key** at all. The `DELETE` blocks make applies idempotent, but a unique key (e.g. on `gossipAction`, or `playerClass`+`gossipText`) would be a stronger guarantee. Also: renaming the two pre-existing files adds 2 dead refs to the `updates` table (within default `CleanDeadRefMaxCount`).

- [x] **Make the fixes survive regeneration (adjust the generator)** ✅ DONE
  - The ordering prefixes and idempotency `DELETE`s live in generated output, so a raw regenerate would have wiped them. Pushed both into the `wow-bis-sql-tool` generator:
    - New shared `sql_idempotency.py` emits the `DELETE` block; wired into `batch_extract_classic.py`, `batch_extract_tbc.py`, `batch_extract_wotlk.py`, and `extract_classic_talents.py` (it parses the emitted INSERTs, so it always matches what's written).
    - New `deploy_to_base.py` copies `out/*.sql` → `base/` with the numeric-prefix mapping; tool README documents the workflow.
  - **Verified** the generator output reproduces the committed `base/` files **byte-for-byte** (classic/tbc/wotlk/talents) by simulating the assembly on the raw `out/` files; scripts compile and imports resolve.
  - Note: `out/` is currently stale (pre-idempotency) — run a fresh regenerate before `deploy_to_base.py`, or deploy would regress `base/`.

---

## Follow-up considerations

- [ ] Since ~5 of 7 findings are generator/data issues, consider a **broader audit pass** of the extractor output (missing slots, phase-mismatch, PvP-vs-PvE overrides) before regenerating — the reviewer only sampled a few files.
- [ ] Confirm whether the fixes should be **regenerated from tooling** (preferred, consistent) vs hand-patched, and reconcile before committing.
- [ ] This file is working notes — gitignore or delete before it lands in the PR.
