-- bmv_migration.sql
-- Run once in Render Shell:
--   psql $DATABASE_URL -f bmv_migration.sql

-- New owner fields on vehicles (granular address for certified mail)
ALTER TABLE vehicles
    ADD COLUMN IF NOT EXISTS owner_city    VARCHAR(100),
    ADD COLUMN IF NOT EXISTS owner_state   VARCHAR(10),
    ADD COLUMN IF NOT EXISTS owner_zip     VARCHAR(15),
    ADD COLUMN IF NOT EXISTS po_box_flag   BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS title_number  VARCHAR(50);

-- Lienholder fields (already in model, safe to re-run due to IF NOT EXISTS)
ALTER TABLE vehicles
    ADD COLUMN IF NOT EXISTS lienholder_name    VARCHAR(200),
    ADD COLUMN IF NOT EXISTS lienholder_address VARCHAR(300);

-- BMV scan history table
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
