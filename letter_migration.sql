-- letter_migration.sql
-- Run once in Render Shell:
--   psql $DATABASE_URL -f letter_migration.sql
-- Or paste into Render → PostgreSQL → psql tab.

-- certified_letters: UPS tracking fields
ALTER TABLE certified_letters
    ADD COLUMN IF NOT EXISTS scheduled_delivery DATE,
    ADD COLUMN IF NOT EXISTS ups_status         VARCHAR(50),
    ADD COLUMN IF NOT EXISTS updated_at         TIMESTAMP;

-- vehicles: UPS letter stage + flag
ALTER TABLE vehicles
    ADD COLUMN IF NOT EXISTS letter_stage       VARCHAR(50),
    ADD COLUMN IF NOT EXISTS letter_flag        VARCHAR(50),
    ADD COLUMN IF NOT EXISTS letter_flag_detail TEXT;
