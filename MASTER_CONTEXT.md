# [IMPOUND MANAGER — MASTER CONTEXT DOC]
_Last updated: July 13, 2026 (end-of-day) — regenerated after the disposition-pipeline + UPS Phase 2 build._

---

## PROJECT INFO
- **App:** impound-manager.onrender.com
- **GitHub:** timjr4301/impound-manager
- **Render Service ID:** srv-d909ske8bjmc7391ikig ← LIVE PRODUCTION APP (confirmed: bjmc)
- **DB:** PostgreSQL (never SQLite)
- **Default staff password:** BandJ2024!
- **Demo login:** test / BandJDemo!
- **ANTHROPIC_API_KEY** stored in Render environment
- ⚠ DO NOT CONFUSE WITH: bj-impound-manager (srv-d91b7ofavr4c739ege8g) — separate older SUSPENDED service. All work goes to impound-manager / srv-d909ske8bjmc7391ikig.

## DEPLOY PROTOCOL
1. Push to GitHub (auto-deploys to Render).
2. Run `python3 reset_users.py` in the Render Shell after every deploy.
3. **Schema:** the app self-migrates on boot (`run_migrations()` in app.py — `db.create_all()` for new tables + guarded `ALTER TABLE ADD COLUMN` for new columns). **Tonight's build needs NO manual `ALTER TABLE`** — new tables/columns apply automatically on first boot. Manual `psql $DATABASE_URL -c "..."` in the Render Shell is only needed for ad-hoc fixes.

## AI MODELS
- `claude-opus-4-8` → vision/photo tasks only (VIN snap, envelope/BMV scan, damage assessment)
- `claude-sonnet-4-6` → code and logic
- NEVER pin `httpx==0.24.1` (breaks Anthropic SDK). Use `anthropic>=0.40.0`, `httpx>=0.28.0`.

## LABEL CONVENTION
`[IMPOUND MANAGER — CLAUDE CODE]` / `[RENDER SHELL]` / `[RENDER ENV]`

---

## COMPLETED BUILDS (through July 13, 2026)
Foundation, CSV import, role-based permissions (now 10 roles), auto-seed users, possible-release flagging, Opus damage photos, Base44 API, NADA override, unified nav at /hub, envelope scanner, help system, ghost-vehicle alerts, file restart logic, document viewer, VIN photo verification, /vin-lookup, reference search, task backlog snooze, staff feedback, staff guides, /driver VIN-snap, additional charges, owner/lienholder-2 fields, UPS Phase 1 (labels/POD), damage-photo bulk uploader, staff to-do lists, undo-release, status audit tool + bulk release, police-department rates, 5-letter templates.

### ✅ NEW — July 13, 2026 (this session)

**UPS Phase 2 — manual bulk tracking refresh** (commit 31894b1)
- "Refresh UPS Tracking" button on the Letters page (`/heather/letters`) sweeps every in-flight certified letter, confirms deliveries (starts Letter 2's 30-day clock), marks RTS, and pulls newly-available signed PODs — one pass, no 6am cron, no Render cost.
- Shows "Last UPS tracking refresh: <time ET> by <user> — X checked · Y delivered · Z returned · N PODs".
- New `ups_poll_log` table; `datetime_et` Jinja filter (renders stored UTC in Eastern).
- The parked 6am auto-poll is intentionally replaced by this manual button.

**Disposition Pipeline — full in-house post-title process** (commits c6b6ad8, 7cbb7b1, acbac13, 12b7510, 5dfd638) — **retires the external Base44 tracker** (hub tile now points in-app; `push_vehicle_to_tina` dead code path abandoned).

Stage ladder (single source of truth = `disposition.py`):
```
Awaiting Title → To Locate → Key Row → Inspection Pool → Needs Repairs
   → Auction Ready → At Auction → Sold        (SELL track)
              ↘ Junk — Pending → Junked        (JUNK track)         + Hold
```
- **Board** at `/tina/pipeline` — drag cards down their track; dragging into a lane sets Sell/Junk; terminal stages (Sold/Junked) route through the invoice form so outcome data is captured. Directed transitions in `disposition.TRANSITIONS`.
- **Terminal capture:** Sold → auctioneer, lot #, date, price, buyer; Junked → yard, weight, price/ton. Status stays `RELEASED` (umbrella — audit/released-tab/API unaffected); precise outcome in `disposition_outcome` (SOLD | JUNKED | RELEASED_TO_OWNER).
- **Disposition Report** `/tina/disposition-report` — where every post-title car stands by stage + Sold/Junked outcomes + gross proceeds.

**Field Ops — mobile crew screens** (`/field`, dark big-button theme like /driver)
- **Driver Find & Assess** (`/field/find`, `/field/assess/<id>`) — role dispatcher/tina. Locate title cars; record **catalytic converter Yes/No + optional photo**, junk/auction call, drop area. Auction → Key Row (SELL); Junk → Junk — Pending (JUNK, awaiting Tina's Ohio Steel sign-off).
- **Key Row** (`/field/keys`) — **Robert the key maker**, new `robert` role, lands here on login. Mark Key Made (type, cost, where the key went incl. service-holder #) → Inspection Pool; or "Can't make a key" → Junk — Pending.
- **Inspection Pool** (`/field/inspect`) — service + night crew. **Claim** a car (who/when/where moved — kills duplicate looks), Release, Take-over; diagnose Auction-ready / Junk / Needs-repairs. Needs-repairs fires an in-app **Wally alert** to Jim/Tina who Approve (→ Auction Ready) or Deny (→ Junk) from Tina's dashboard "Repairs — Awaiting Your OK".

**Chain of custody** — `custody_events` table logs every car move, key move, stage change, converter check, key make, inspection, and repair decision. "Where's the car / where's the key / who touched it" is answerable at any time (car location + key location live on the vehicle).

**Auction events + reconciliation**
- `/tina/auctions` — schedule 1st/3rd-Saturday auctions (single add or quick-generate next 6), online (Peacock) vs live (Fifth Ave), assign auction-ready cars (→ At Auction, venue/date stamped), mark advertised, delete.
- **Flyer reminder** — events within 7 days not advertised show "Post now" on the auctions page + a banner on Tina's dashboard. (Computed on view; no cron.)
- `/tina/junk-reconciliation` — every junked car with its documented converter status + who/when/photo + tallies, to check Ohio Steel's "no converter" deductions against our own record.

---

## PARKED — DO NOT BUILD YET
- 🅿 UPS Phase 2 auto-poll (6am digest) — **superseded** by the manual Refresh button (this session). Only revisit if a true unattended digest is wanted.
- 🅿 Build 14: VinAudit — waiting on `VINAUDIT_API_KEY` in Render.
- 🅿 PPI Sales tracker (John Payne) — deferred.
- 🅿 Base44 rebuild — **DONE** this session (in-house disposition pipeline). External Base44 retired.

## BUILD QUEUE (open items, not started)
- ⬜ Release compliance hard-stop gate (block release unless required letters sent OR title filed)
- ⬜ Daily release list for Lawrence (printable end-of-shift book reconciliation)
- ⬜ Build E: General Documents Upload (partially present — `vehicle_general_documents`)
- ⬜ Build A: Envelope Tab + image attachment (Matched/Unmatched/Cleared tabs)
- ⬜ Build Q: QR scanner tab on /driver (jsQR, reads Towbook QR codes)
- ⬜ Possible follow-ups on tonight's work: auction-event edit page; per-load Ohio Steel batch grouping; push/SMS on repair alerts; a "repairs in progress" sub-state between approve and auction-ready.

---

## KEY OHIO COMPLIANCE RULES
- 60 days from `impound_date` + 30 days after Letter 2 before title eligibility.
- Letter 2 clock anchored to proof of delivery of Letter 1 (`task_engine.letter_delivery_date`).
- Electronic POD (UPS POD or scanned DELIVERED envelope) satisfies certified-mail requirement.
- NADA wholesale value must be less than total fees owed.
- PUCO maximums: tow $129, storage $17/day. B&J BMV vendor #: 25-186078.
- BMV 4202 = private property; BMV 4205 = police. PO Box → compliance flag. Out-of-state → court process.
- `impound_date` is the permanent 60-day clock — NEVER use as a restart source (`restart_date` re-anchors letters only).
- Vehicle class (light/medium/heavy) drives fees; defaults to light.
- Every notice goes to every party (owner1/owner2/lienholder1/lienholder2).

## KEY STAFF & ROLES
- **Heather** (role heather): intake, letters, envelope scanning, BMV searches. Now also runs the UPS Refresh button.
- **Tina** (role tina): titles, NADA, the whole disposition pipeline/board/auctions/reconciliation, invoices.
- **Robert** (role **robert** — NEW): key maker. Narrow access — logs in and lands on the Key Row screen only. Username `robert`, pw BandJ2024!. `is_key_maker` = robert/tina/tim/jim.
- **Miguel** (role dispatcher): primary driver on the /driver + /field Find/Assess screens.
- **Wheel-lift drivers / night crew** (role dispatcher): Find/Assess + Inspection Pool claim/diagnose.
- **Service** (Jim Welch, Brittany Buckey): inspection pool techs (give them dispatcher-level accounts). Black numbered key holder = `SERVICE_HOLDER` key location.
- **Jim / Tina Weller** (roles jim / tina): repair approve/deny deciders (gated to tina/tim/jim).
- **Lawrence** (lawrence): third-shift, large-text UI. **Jim** (jim): co-owner, purple overrides. **Wally / Tim Sr.** (username wally, role tim).
- **John Payne**: PPI/apartment salesperson (parked tracker).

## TWO LOCATIONS
- **4301 E 5th Ave** (main): police impounds, service dept, inspection pool, key rack, key row, **online auction row** (Peacock cars staged here).
- **3865 E 5th Ave**: PPI impounds, Lot A current, Lot B auctioneer, Lot C junk/Ohio Steel. **Fifth Ave Auto Sales** rents this lot for **live auctions**.
- Auctions: **1st & 3rd Saturday** of each month. Advertise ≥1 week ahead (flyer reminder enforces this).

## IMPORTANT TABLE NOTES
- `damage_photos` → driver damage-report wizard (blueprints/damage_docs.py) — DO NOT touch.
- `vehicle_damage_photos` → bulk upload feature — separate table.
- **NEW tables this session:** `ups_poll_log`, `custody_events`, `auction_events`.
- **NEW Vehicle columns:** `tina_stage_at`, `disposition_outcome`; auction (`auctioneer`, `auction_lot`, `auction_date`, `auction_venue`, `auction_event_id`); converter (`converter_present`, `converter_checked_by/at`, `converter_photo`, `converter_notes`); custody (`custody_location*`, `key_location*`); key (`key_made`, `key_type`, `key_cost`, `key_made_by/at`); inspection (`inspection_claimed_by/at`, `inspection_done`, `inspection_diagnosis`, `inspection_notes`, `inspected_by/at`); repair (`repair_estimate`, `repair_notes`, `repair_approved`, `repair_decided_by/at`).
- `tina_stage` legacy values (QUEUED/TITLE_WORK/ROUTED_* and the interim AUCTION_PREP/JUNK_PREP/TITLE_FILED) are auto-remapped to the new ladder on boot (`disposition.LEGACY_STAGE_MAP`).

## KEY CODE MAP (this session)
- `disposition.py` — stage ladder, transitions, legacy remap, key/diagnosis/venue enums (SINGLE SOURCE OF TRUTH).
- `pipeline_ops.py` — shared `move_stage`, `record_custody`, `set_car_location`, `set_key_location`, `post_alert` (Wally thread).
- `blueprints/field_ops.py` — /field driver find/assess, key row, inspection pool.
- `blueprints/tina.py` — board, disposition report, set-disposition, create-invoice (terminal capture), repair approve/deny, auctions, junk reconciliation.
- Templates: `templates/field_ops/*` (mobile), `templates/tina/{pipeline,disposition_report,auctions,junk_reconciliation}.html`.

## TOOLS & RESOURCES
- App: impound-manager.onrender.com | GitHub: timjr4301/impound-manager | Render: srv-d909ske8bjmc7391ikig
- Default pw: BandJ2024! | Demo: test/BandJDemo!
- Render Shell: bash only; SQL via `psql $DATABASE_URL -c "..."`
- UPS: account 81Y7X1, shipper 4301 E 5th Ave Columbus OH 43219 (production endpoint, signature-required labels)
- **Peacock Auto Auction** — online auction house (venue ONLINE). **Fifth Ave Auto Sales** — live auction (venue LIVE, 3865 lot). **Ohio Steel** — junk/scrap buyer (~$500/car w/ converters).
- Ohio BMV portal: https://services.dps.ohio.gov/AbstractAccounts/User/Home
- Towbook: CSV export main data pipeline; 2-row header skip.

## APPROACH & PATTERNS
- Two-environment workflow: planning chat for design; Claude Code for execution.
- Queue-driven: one build at a time, verify before advancing. Every build tonight shipped with an integration test (see scratchpad test_*.py: ups_sweep, field, dispo, keys, inspect, auction).
- Common confusion: Tim sometimes pastes Claude Code recap output into the planning chat — ignore those blocks.
