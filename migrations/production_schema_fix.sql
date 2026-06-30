-- production_schema_fix.sql
-- Run once in Render Shell:
--   psql $DATABASE_URL -f migrations/production_schema_fix.sql
-- Or paste into Render -> PostgreSQL -> psql tab.
-- Safe to re-run (IF NOT EXISTS on every column).
--
-- Consolidates bmv_migration.sql + letter_migration.sql (neither had been
-- run on production as of 2026-06-30, confirmed by:
--   psycopg2.errors.UndefinedColumn: column vehicles.owner_city does not exist
-- ) plus columns that exist in models.py but had no migration file at all.

-- vehicles: owner address detail + BMV/title fields
ALTER TABLE vehicles
    ADD COLUMN IF NOT EXISTS owner_city    VARCHAR(100),
    ADD COLUMN IF NOT EXISTS owner_state   VARCHAR(10),
    ADD COLUMN IF NOT EXISTS owner_zip     VARCHAR(15),
    ADD COLUMN IF NOT EXISTS po_box_flag   BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS title_number  VARCHAR(50);

-- vehicles: lienholder fields
ALTER TABLE vehicles
    ADD COLUMN IF NOT EXISTS lienholder_name    VARCHAR(200),
    ADD COLUMN IF NOT EXISTS lienholder_address VARCHAR(300);

-- vehicles: UPS letter stage + flag
ALTER TABLE vehicles
    ADD COLUMN IF NOT EXISTS letter_stage       VARCHAR(50),
    ADD COLUMN IF NOT EXISTS letter_flag        VARCHAR(50),
    ADD COLUMN IF NOT EXISTS letter_flag_detail TEXT;

-- vehicles: document confirmation flags + release flag + base44 link
-- (not covered by bmv_migration.sql or letter_migration.sql)
ALTER TABLE vehicles
    ADD COLUMN IF NOT EXISTS lka_document_confirmed BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS title_search_confirmed BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS ups_delivery_confirmed BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS return_receipt_filed   BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS possible_release       BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS base44_id              VARCHAR(100);

-- bmv_scan_history table
CREATE TABLE IF NOT EXISTS bmv_scan_history (
    id               SERIAL PRIMARY KEY,
    vehicle_id       INTEGER REFERENCES vehicles(id) ON DELETE CASCADE,
    scan_type        VARCHAR(50),
    lka_data         TEXT,
    title_data       TEXT,
    comparison_flags TEXT,
    scanned_by       VARCHAR(100),
    scanned_at       TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_bmv_scan_vehicle ON bmv_scan_history(vehicle_id);

-- certified_letters: UPS tracking fields
ALTER TABLE certified_letters
    ADD COLUMN IF NOT EXISTS scheduled_delivery DATE,
    ADD COLUMN IF NOT EXISTS ups_status         VARCHAR(50),
    ADD COLUMN IF NOT EXISTS updated_at         TIMESTAMP;
