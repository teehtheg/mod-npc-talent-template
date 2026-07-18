-- DB: characters
-- Upgrade migration: add the `category` column to `mod_npc_talent_template_index`.
--
-- Fresh installs get the column directly from `00_npc_talent_template.sql` (CREATE TABLE),
-- but existing installs already have the table without it, so `npc_talent_template.cpp`
-- (which now does `SELECT ... category`) would fail. This adds the column for them.
--
-- Files are applied in ascending filename order. The numeric-prefix scheme places this
-- as `01_` so it runs right after `00_npc_talent_template.sql` (table creation) and
-- before every `1x_`/`2x_`/`3x_`/`4x_` data file, all of which INSERT into this table
-- referencing `category`.
--
-- Idempotent by design: only alters when the table exists and the column does not, so it
-- is a safe no-op on fresh installs (where the CREATE TABLE already defines `category`)
-- and regardless of the order in which files are applied.

SET @tbl_exists := (
    SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'mod_npc_talent_template_index'
);

SET @col_exists := (
    SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'mod_npc_talent_template_index'
      AND COLUMN_NAME = 'category'
);

SET @ddl := IF(@tbl_exists = 1 AND @col_exists = 0,
    'ALTER TABLE `mod_npc_talent_template_index` ADD COLUMN `category` VARCHAR(64) NOT NULL DEFAULT '''' AFTER `maxLevel`',
    'DO 0'
);

PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
