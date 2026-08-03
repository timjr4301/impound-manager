# [IMPOUND MANAGER — MASTER CONTEXT DOC]
_Last updated: August 2, 2026 (late evening session) — **the towbook_import.py root cause IS NOW FIXED.**
New Towbook-synced vehicles get a real Letter 1 record automatically on import — the gap that caused the
whole 08/02 overnight backfill can't recur. Also shipped the same night: two more "not a real impound"
guards (transport/relocation calls, Goose/PVG Brokerage container storage), a working CSV-upload tool that
cross-checks Towbook's own letter-sent field against what IM thinks (652 real rows checked), Tina given full
Heather-coverage permissions, a manual letter-pipeline hold for grandfathered junk (boats, old trailers) that
stops it cluttering every queue, PO Box → USPS letter sending (UPS can't deliver there), 2nd-address delivery
tracking for the "notify every address we found" policy, and several smaller UX fixes — **plus a real billing
bug fix** (POLICE, and some PPI, letters were printing "TOTAL BALANCE OWED: $0.00" — `total_owed` read raw
override columns instead of the `effective_tow_rate`/`effective_storage_rate` fallback properties) and a
2-attempt UPS Ship API 400 fix (real bug: field name is `Packaging` not `PackagingType` — **unverified as of
end of session, Tim had not yet retried**). Staging force-pushed to match production (was 32 commits behind).
Full detail in "NEW — August 2, 2026 (late evening session)" below. **Open for next session — Tim's stated
priority: verify the whole multi-party letter system end to end** — confirm the system correctly identifies
every party on a call who needs a letter (owner, 2nd address, lienholder, and the not-yet-built impound-slip
owner for POLICE) and that there's a clean, clear way to see at a glance which parties on a given vehicle have
been sent letters and which haven't (builds on tonight's 2nd-recipient tracking work). Also open: confirm the
UPS fix actually worked, the impound-slip-vs-BMV-owner comparison feature (spec'd, not built), full USPS
API/AutoDataDirect certified-mail integration (spec'd, not built), and the 159-vehicle Towbook/IM
letter-status mismatch list Tim is working through by hand.

_Previous entry: August 1, 2026 — training-video tooling session (separate track). Built the 10-vehicle
training baseline (`seed_training_baseline.py`) + a Marvel/DC intake-practice kit (`marvel_impound_test_kit/`),
plus a staging-only one-click Training Data Reset admin page. Dogfooding that content surfaced and fixed two
real production gaps: Daily Intake's BMV-document batch had no finish summary, and a Towbook CSV import
never showed which vehicles were actually new. Confirmed the old "Claude Code push 403s" limitation is gone._

_Earlier: July 30, 2026 (evening) — daily-workflow batch, PRs #14 + #18–#21 all merged + deployed same day: possible-release auto-clear on reappearance (CSV + API paths), BMV ↗ quick-link, Today task view, police-letter blank-owner fix (structured owner fields in BMV Done), photo multi-file/ZIP upload, LKA/Title PDF auto-read on vehicle-page upload, Date Change Log report + restart-crash fix. Towbook API access: spec sent to Gabe, awaiting decision. Earlier same day: sent+30 remnant cleanup (PR #13). Parked: image backup (waiting on IT), relo-trans categorization. Open: owner-info backfill for pre-07/30 POLICE vehicles._

> ⚠ **CURRENT COMPLIANCE RULE (corrected 2026-07-29):** the 2nd notice letter is due **30 days after Letter 1 is SENT**, NOT after delivery. This reverses the earlier delivery-anchored design (de71135, commit f3cca7d fixes it). Some build entries below still describe the old delivery-anchored behavior — that's history; the sent-anchored rule under "KEY OHIO COMPLIANCE RULES" is what's live. **Do not revert to delivery-anchoring.**

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

### ✅ NEW — July 13, 2026 (queue-clearing session — PR #1)

An audit against the codebase found most of the old BUILD QUEUE was already shipped (audit bulk release, release hard-stop gate, Build A/C/E, Build B's letter/police-dept system). These items were what remained:

**Daily Release List for Lawrence** — printable, large-text page at `/release-list` listing every vehicle that reached RELEASED on a given day (date picker + prev/next; the third shift crosses midnight) with a book-reconciliation footer. Restricted to third-shift + management (`can_see_release_list` = lawrence/lori/tim/brady/jim). Backed by new `Vehicle.released_at`/`released_by`, stamped at **every** release path (customer pickup, Tina sale/junk, audit bulk, Towbook sync) and cleared on Undo Release.

**Vehicle class → PPI storage fees** (completes Build B) — `vehicle_class` (light/medium/heavy, defaults light) on the intake/edit forms and the ticket detail. Drives the PPI daily storage rate that feeds **both** the amount owed and the notice-letter copy: **light $22 / medium $37 / heavy $82 per day**. Class only seeds the default — the rate stays editable per ticket (blank PPI storage auto-fills from class server-side + a live form suggestion; an entered value always wins; a custom rate is preserved when class changes). `effective_tow_rate`/`effective_storage_rate` return the actual per-vehicle rate when set so the letter matches the bill. POLICE untouched (department rates; `rate_pending` intact). The ticket **Financial** panel now shows the exact fees that print on the letter. One-time correction of existing PPI tickets: `python3 backfill_ppi_storage.py [--apply]` (dry-run by default; active PPI only; leaves POLICE/released alone).

**QR Scan mode on `/driver`** (Build Q) — a mode toggle adds a live QR scanner (vendored `static/js/jsQR.min.js`, no CDN at scan time) that reads the Towbook windshield QR, decodes client-side, and matches active vehicles by VIN → stock → plate via `/driver/match-qr` (tolerant of delimited or URL-form payloads). A match reuses the existing confirm → zone → GPS-save flow; the camera stream is released on leave.

**Top-nav overhaul into 4 sections** — flat per-role nav replaced by four auto-hiding dropdowns (Morning Workflow, Letters & Titles, Field Ops, Management) + a persistent utility bar (Search, + New, VIN Snap, To-Dos, Chat). Built per-user in `app.build_top_nav` (injected as `nav_sections`); per-link access mirrors the old nav, empty sections drop out, missing endpoints are skipped via BuildError guard. Also surfaces Robert's **Key Row** link, which the old flat nav never exposed.

### ✅ Follow-ups — July 13, 2026 (PR #2, PR #3)

**Manual valuation-lookup buttons** (PR #2) — a "Compare value on" row in the ticket's Financial panel with **KBB / J.D. Power / NADAguides / Black Book** buttons + a Copy VIN button. These sites have no reliable prefill-by-URL, so clicking a source copies the VIN to the clipboard and opens the site in a new tab (paste into its VIN box). Only shown when the vehicle has a VIN. Source URLs are one Jinja list in `templates/vehicles/detail.html` — easy to swap. **Black Book** points at the marketing site for now; swap in the exact B&J subscriber-portal login URL when available.

**Gunicorn memory tuning** (PR #3) — `render.yaml` start command changed to `--threads 4 -w 1 --max-requests 200 --max-requests-jitter 30` to reduce the recurring 512MB OOM restarts. ⚠ **This is only half the fix** — see OPS note below.

### ✅ NEW — July 26, 2026 (sequential gate + UPS automation + letter guide)

**Sequential task gate (commit d5f8e3a)** — REVERSES the July-7 day-1 dual-unlock. **Letter 1 (Task 2) can no longer be sent until BMV Search (Task 1) is marked complete**; Letter 2 (Task 3) stays blocked until Letter 1 is sent AND its 30-day post-DELIVERY window opens. Enforced server-side in BOTH send paths (`letters_mark_sent` POST + `letters_create_ups_label`) via new `Vehicle.letter_send_block_reason()` — no role bypass, not just a hidden button. New `Vehicle.bmv_search_complete` helper + new `Vehicle.task_2_letter_completed_at` column (audit/display stamp only — Task 3 stays DELIVERY-anchored, NOT stamped off this column). `task_engine` now shows Task 2 as locked-behind-Task-1 with a red **LATE** flag once past day 5 unsent; `models.next_action_label` says "Complete BMV Search first". `mark_sent.html` shows a lock banner + disabled buttons. Confirmed with Tim (AskUserQuestion): (1) keep the delivery-anchored Letter-2 clock, (2) gate applies to ALL impound types incl. PPI (whose Letter 1 previously went out day 1). Key insight: these "tasks" are computed state, not checkboxes — "Task 2 complete" = Letter 1's `sent_date` set, so the gate attaches to the send action.

**UPS delivery auto-poll (commit 1c59b00)** — the long-parked "Phase 2 auto-poll" is now BUILT. New `ups_poll.py` = single source of truth for the per-letter refresh, POD pull, and in-flight sweep (moved verbatim out of app.py's closures; app.py now has thin wrappers so the manual buttons and the auto-poll can't drift). New APScheduler job `ups_delivery_poll` runs **every 3 hrs, 8am–8pm ET** — records delivery/RTS, pulls signed PODs, and **starts Letter 2's 30-day clock automatically the moment UPS confirms delivery, no clicking.** The manual "Refresh UPS Tracking" button still works. ⚠ This adds another recurring background job — watch it against the 512MB OOM note below (the **1 GB instance** is still the real fix).

**"Test UPS Connection" button (commit 1c59b00)** — new `/admin/ups-test` (heather/owners) checks, in plain English: credentials present → UPS OAuth login → optional live tracking lookup. Button sits next to "Refresh UPS Tracking" on the Letters page. This is the quickest way to confirm UPS is really working (labels bill to account 81Y7X1 and appear on ups.com; delivery data flows back).

**Letter Workflow Guide (commit 388fad9)** — the `/guides/letter-workflow` route already existed and was `@login_required`; this shipped its standalone template (`templates/guides/letter-workflow-guide.html`) + the Guides-menu nav link, which had been sitting uncommitted. Same standalone pattern as the Heather/Tina guides (no base wrapper, no dynamic vars).

**Deploy notes (July 26):** pushing to `timjr4301/impound-manager` **fails from Claude Code's environment** (git creds authenticate as `tim-wallaceandrew` → 403) — Tim pushes from his own machine. His machine ALSO defaults to `tim-wallaceandrew` active; the fix is `gh auth switch --user timjr4301` then `gh auth setup-git`, then push. On deploy the `task_2_letter_completed_at` column self-healed on boot (manual ALTER reported "already exists, skipping"); `reset_users.py` run (users incl. `robert`).

### ✅ NEW — July 28–29, 2026 (VIN reclassify + release reconciliation + July-outage letter cleanup)

**Find Trucks — VIN reclassification (commit 36837a1)** — new Tim-only `/admin/reclassify` page + `vin_decode.py` (NHTSA vPIC decoder, keyless, `DecodeVINValuesBatch`). Scans active-PPI VINs, maps GVWR weight class → light/medium/heavy, and surfaces ONLY the trucks it flags (the ~90% light cars are auto-confirmed and hidden). Apply writes BOTH `vehicle_class` AND `daily_storage_rate` (changing class alone doesn't change the bill — `effective_storage_rate` returns the stored rate when set). Also a **"Detect from VIN"** button on the new/edit vehicle forms (`GET /vin/detect-class`). Nav: Management → "Find Trucks (VIN scan)". No app-UPS/AI dependency (NHTSA is a plain data API). Design steer: Tim rejected a manual bulk-classify list — wants auto-detect + review-the-exceptions for backlogs.

**Release reconciliation** — uploaded `released cars since may 1st.csv` to `/audit`, bulk-released the **18** still-active bulk-eligible cars (Released-with-Payment / to-Insurance). Left the 2 "Other/Review" rows (one is a **relo-trans** — a transport car staged in the lot for another transport truck, NOT an impound) and 1 Title-Obtained→Tina car.

**July-outage 1st-letter reconciliation (the big one).** During a mid-July site outage Heather mailed 1st letters "the old way" — UPS labels made **directly on ups.com, outside the app** — so those letters had no record in IM. Reconciled from Tim's **UPS outbound export** (`outbound_072826_*.csv`; 6 wks 06/15–07/27, 621 invoices) via three one-time scripts (all dry-run gated, run in the Render Shell):
- **`import_ups_letter_dates.py` (commit c99549b)** — 621 invoice→earliest-manifest-date pairs baked in. Matches vehicles by `invoice_number` (populated by `towbook_import.py:191`). **CREATES** a `letter_number=1` record marked sent (real UPS date, due = impound+5 PPI / +10 POLICE) — because **`towbook_import.py` never creates letter records**, so Towbook-synced cars have NO letters at all (an update-only first pass found 0; that's the key gotcha). **Result: 56 created, 0 filled.** Touches ONLY letter 1, never 2nd letters; audit VehicleNote each.
- **`attach_ups_tracking.py` (commit fd22b6e)** — attaches the UPS tracking number (from the same export) to sent letter-1s missing it, so they become eligible for the existing `ups_poll.py` in-flight sweep (`_in_flight_letters`: tracking not null + ACTIVE non-ghost + `return_to_sender=False` + delivery null). **Result: 68 attached** (56 new + 12 older; 4 old cars had no export match).
- Then **"Refresh UPS Tracking"** on the Letters page → UPS delivery pulled → **61 delivered = 61 2nd-letter clocks started** (delivery-anchored Task 3, 30 days; commit de71135). A wave of now-due 2nd letters lands on Heather's board — expected, correct.
- **`mark_letter1_sent_reconcile.py` (commit fe49fbe)** — unused fallback: dates 1st letters day-after-impound (Fri/Sat/Sun→Monday) for cars with no UPS match.

**"2nd letters moving forward" is fully automatic now:** any letter sent IN the app gets a tracking number → the 3-hourly auto-poll pulls delivery/RTS → the 2nd-letter timer starts, no clicks. The outage backlog only needed hand-holding because those labels were created outside the app. Key data facts confirmed this session: `Vehicle.invoice_number` exists (models.py:160); the app's UPS API (`ups_api.py::_parse_package`) returns the DELIVERED date + status but NOT the send/manifest date, so the CSV was the only send-date source; `generate_letter1_backfill.py` creates *unsent* missing letter-1 rows.

**Process notes (recurred all session):** `python3` scripts run ONLY in the Render **web** Shell (dashboard → Shell tab) — running them in local PowerShell gives "Python was not found." Always confirm the script ended with **"Committed - N …"**, not "Re-run with --apply" (the `--apply` got skipped once, so a step silently did nothing). Give one command at a time, labeled PowerShell-vs-Render.

**OPEN / not yet verified:** the **fresh Towbook-synced car → 1st-letter queue** handoff. Since Towbook sync creates NO letter record, confirm new daily-upload cars actually surface in Heather's letter queue and don't quietly fall through the way the outage batch did. Also: the ~61 due 2nd letters are Heather's to work; the 4 no-UPS-match old cars still lack tracking.

### ✅ NEW — July 30, 2026 (sent+30 remnant cleanup — follows the 2026-07-29 rule correction)

**Stored-data backfill (boot migration in `run_migrations`)** — PPI `letter_number=2` rows created before commit f3cca7d, or imported verbatim from Towbook's "SECOND LETTER Due Date" column by `towbook_letter_backfill.py`, could carry a stored `due_date` disagreeing with the corrected rule (live example: vehicle 5354 / stock 28559544 — Letter 1 sent 06/11, stored due 07/10 vs computed 07/11, so Heather's dashboard and the detail page/audit disagreed by a day). The backfill sets `due_date = letter1.sent_date + 30` on non-superseded PPI letter-2 rows whose Letter 1 is sent, only when it differs; idempotent; logs `[letter2_backfill] … N row(s) changed` on boot. **POLICE chains need no equivalent:** their 2nd notices (letter_number 4/6) are only created by `letter_triggers.py`, whose formula has always been trigger `sent_date + 30`; anomalous POLICE letter-2 rows (Towbook import — POLICE's real 2nd owner notice is letter_number 4) are deliberately left untouched.

**Last delivery-anchored logic/wording swept out** — the vehicle-detail **Task 3 card was still computing `task3_open` off `delivery_confirmed_date + 10`** (pre-de71135 remnant) with an "Awaiting delivery confirmation" wait state; now `l1.sent_date + 30` with subtitle "(30d after Letter 1 sent)". Stale comments fixed in `app.py` (`_finalize_letter_sent`, mark-sent gate), `task_engine.py` module docstring, `models.py` (`task_2_letter_completed_at`); removed the now-unused `task_engine.letter_delivery_date` helper; corrected letter-workflow-guide test item 6 (UPS Refresh: delivery sets POD only, due date does NOT shift). Delivery tracking itself (POD records, Awaiting Delivery tabs, auto-poll) is untouched — delivery just never gates or times Letter 2. Verified locally end-to-end: backfill scope (PPI-only, superseded/POLICE untouched), all readers agree on 07/11 for the 5354 replica, detail page renders sent-anchored and ignores early delivery.

### ✅ NEW — August 2, 2026 (late evening session — root cause fixed + 10 commits)

**The root cause is fixed.** `towbook_import.py` (the daily CSV pipeline — almost every vehicle) and
`towbook_api.py` (the dormant Towbook-API path, same fix mirrored so it can't reintroduce the gap the day
Towbook grants API access) now create a real `letter_number=1` `CertifiedLetter` row on every new-vehicle
insert, exactly the same way the manual "Add Vehicle" form (`app.py: vehicles_new`) already did — due date
`impound_date + PPI/POLICE_LETTER1_DAYS`, correct `letter_kind`/`recipient_type`, `letter_triggers.on_vehicle_created`
called for lienholder parity. This is the actual fix for the item flagged "NOT fixed" in the 08/02 overnight
entry below — new Towbook-synced cars can no longer silently skip the letter queue.

**Two "not a real impound" guards added the same night**, both confirmed against real examples before being
coded (not guessed): (1) **Transport/relocation calls** — Call Reason `TRANSPORT`/`RELOCATE` (e.g. Stock
28787363/28985258, Account "Salvato Auctions") — B&J is paid to hold the vehicle for a broker until another
transporter picks it up, not actually impounding it. (2) **Goose / PVG Brokerage Inc.** — pays B&J to store
shipping containers, same category. Deliberately a **named-account list**, not a keyword match on "storage"
or "broker" — real private-property accounts like "EXTRA SPACE STORAGE" and "PRESTIGE STORAGE MANAGEMENT"
have real impounded vehicles needing real letters; a keyword match would have wrongly skipped those.

**1st Letter Sent Cross-Check (`/audit`)** — new upload card, same trusted drag-and-drop pattern as the
existing Towbook Release Cross-Reference. Answers: does Towbook's own "1st Letter Sent" column agree with
what IM itself thinks is sent? First attempt was a Render Shell paste-a-CSV script
(`compare_letter1_status.py`) — abandoned, large pastes weren't registering reliably in the browser
terminal (`cat > file` came back empty). The real, working version lives in `blueprints/audit.py`
(`letter1_check`/`letter1_clear` routes), auto-detects either Towbook export layout.
Run against a real 652-row export: **463 agree, 15 Towbook-blank-but-IM-sent (Towbook's just behind, normal),
159 Towbook-filled-but-IM-not-sent, 15 unmatched.** ⚠ Strong evidence the 159 aren't real missing letters —
sample rows showed "1st Letter Sent" timestamps *hours* after impound, too fast to be a real mailing
confirmation; more likely a task-due timestamp, not a send confirmation. Tim is working through the 159 by
hand with real Towbook evidence per vehicle (Files tab — View_Print Label.pdf / Proof of Delivery.docx),
confirmed some are real (e.g. Goose/PVG containers, a Salvato transport Camry) — **not done, in progress.**

**Companion breakdown script** — `overdue_letters_breakdown.py` (read-only, groups the overdue-letters
backlog by account/type/age, flags broker-sounding account names) — built, never actually run; superseded in
practice by the CSV cross-check above.

**Tina given full Heather-coverage permissions** — confirmed with Tim: Daily Intake (`_DAILY_INTAKE_ROLES`
in `blueprints/heather.py`) and Undo Release (`User.can_unrelease` in `models.py`) both explicitly excluded
`tina` by original design, not oversight — opened up now so Tina can actually cover Heather's whole job when
Heather is out. Everything else (dashboard, edit vehicles, generate/send letters, verify possible-release)
already included Tina.

**Manual letter-pipeline hold ("boats, grandfathered junk")** — new `Vehicle.letter_hold` +
`letter_hold_reason/by/at`. A "Hold Letters" button (vehicle header) pauses a vehicle everywhere it'd
otherwise show as overdue/urgent — `task_engine.compute_task` early-return (mirrors the existing
`possible_release` pattern but GREEN not RED), excluded from `/audit`'s `_active_not_ghost()` base population.
**Deliberately manual, not auto-detected** — "boat" shows up as wildly inconsistent text across
Vehicle/Make/Model (Pontoon, Sea Ray, Bombardier Jetski, THOMPSON SEA MT...), too unreliable to keyword-match
for something this consequential. Does NOT claim any letter requirement was satisfied — just stops the
nagging. Confirmed real examples: 2007 Bentley Pontoon (2418 days in storage), a 2000 Stoughton 53ft trailer.
**Two real bugs found testing this live, both fixed same session:** (1) `CertifiedLetter.is_overdue`/
`is_due_today` didn't know about the hold, so the Certified Letter Timeline still showed red/overdue under an
active hold — fixed at the model level so it cascades everywhere those properties are read. (2) The Task 2
card computes its own red/yellow/overdue styling **inline** from `days_since_letter_clock_start`, completely
independent of `is_overdue` — missed by fix #1, patched separately. **Watch for more spots like this if the
hold ever looks like it's not fully suppressing something** — the pattern is "search for anywhere overdue
styling is computed inline instead of through the model property."

**PO Box → USPS letter sending** — UPS cannot deliver to PO boxes; `Vehicle.po_box_flag` already existed
(set by the LKA scanner) but was **completely invisible in the UI** before tonight — the "critical" SOP
warning it generates ("must pull tow lien + delivery report or BMV rejects the packet") was only ever
returned as JSON, never rendered anywhere. Now: a clear banner on the vehicle page itself, and both Mark Sent
paths (standalone page + inline Task Pipeline modal) switch to a USPS-specific flow when `po_box_flag` is
set — hides "Create UPS Label" (would fail/be wrong for a PO box), relabels the tracking field for Certified
Mail, and requires a new `po_box_sop_confirmed` checkbox before submission so the SOP requirement is an
actual record, not just a warning nobody has to acknowledge. New `CertifiedLetter.mail_method` ('usps'/null)
drives a "Sent via USPS" badge instead of the normal UPS-tracked display. **What this does NOT do:** actually
create USPS postage or auto-track delivery — Certified Mail still gets bought at the counter (or via a free
USPS Click-N-Ship account) and tracked by hand on usps.com. Tim wants real one-click electronic certified
mail like AutoDataDirect apparently offers — **worth checking with ADD directly (the app already has an
"Import from ADD123" button, so B&J has some existing relationship) before building a fresh integration with
a different vendor.** Real USPS API auto-tracking (parity with the UPS auto-poll) is a separate, comparably-
sized project to the original UPS integration — not started.

**2nd-address delivery/return tracking** — Jim's policy: when a PPI owner's title address and LKA address
genuinely differ, notify both (real example: 2017 Nissan Rogue, VIN 5N1AT2MT6HC865220, owner Edward Karaba —
title address 4 years stale in Dublin, LKA address 2 months old in Columbus). The "2nd owner" mechanism
already existed (built for joint title owners) and turned out to work identically for "same owner, 2nd
address" with zero data-model change needed — but had **no delivery/RTS tracking at all** for the 2nd
recipient, only a POD image pull. Traced `title_eligible_date` (the PPI 60-day clock) first to confirm it
only ever reads the primary recipient — confirmed safe to extend. New `delivery_confirmed_date_2`/
`return_to_sender_2`/`returned_date_2` (purely informational, never read by the compliance clock), a
`letters_confirm_delivery` recipient=2 branch, a new lightweight `letters_mark_returned_2` route (no outcome
choice, no round restart — unlike the primary's `letters_returned_to_sender`, which is much deeper: it
directly drives `title_eligible_date` and already explicitly punts on lienholder returns). Auto-poll
(`ups_poll.py`) extended to check the 2nd tracking number's delivery/RTS status too, not just pull its POD.

**Smaller fixes, same session:**
- **BMV Done redirect** — `heather.bmv_complete` always bounced to the dashboard regardless of where the
  form was submitted from; now returns to `request.referrer` (same-origin only) so marking BMV done from a
  vehicle's own page stays on that page instead of losing your place.
- **UPS Ship API error surfacing** — `create_label()` called `raise_for_status()` before ever reading the
  response body, so every failure showed as an opaque "400 Client Error: Bad Request" with no indication of
  what UPS actually objected to. Now parses the real error body (same shape `void_shipment()` already parsed
  correctly). **Not yet verified against a real failure** — Tim hasn't retried the 2008 Pontiac G5 that
  originally hit this and reported back what it actually says.
- **Envelopes tab** — added a "Scan Returned Mail" button (previously no path from the review page to the
  actual scanner) and a non-destructive "Clear All Unmatched — start fresh" bulk action (same `discarded`
  flag as discarding one at a time, just in bulk).
- **Today's Tasks Overdue tab** — was sorted oldest-due-date-first, burying recently-overdue (probably real)
  items under 400+-day-old stale backlog; now newest-first.

**Process note:** three real examples were confirmed live with Tim before any guard got coded (transport
calls, Goose/PVG, the boat/trailer hold) — each time using a real screenshot/document, not a guess. Keep
doing this; it's what caught the Goose/PVG case (would have been missed by a Call-Reason-only guard) and
avoided over-broadening the storage-account exclusion into a compliance risk.

**Real billing bug found and fixed same night, on a live printed letter (2008 Chrysler 300, POLICE, owner
Richard Prentiss)** — `Vehicle.total_owed`/`total_storage_owed` read the raw `tow_fee`/`daily_storage_rate`
columns directly instead of the `effective_tow_rate`/`effective_storage_rate` properties that already
correctly implement "per-vehicle override wins, else fall back to the class default (PPI) or the requesting
department's rate (POLICE)." Since POLICE never sets those raw override columns at all (it's always been
department-rate-driven), **every POLICE Notice of Lien letter printed "TOTAL BALANCE OWED: $0.00,"** and any
PPI vehicle relying on the default rate (no manual override entered) had the same silent gap. Fixed to use
the `effective_*` properties — same source of truth the Financial panel already used. Also found and fixed:
the Notice of Lien (POLICE) letter template had **no vehicle valuation line at all** — `vehicle.nada_value`
was already shown on the First/Second Notice template but never on this one; added as a 5th column on the
existing vehicle-info table.

**UPS Ship API 400 — two attempts, real lesson in the process.** After the error-surfacing fix above revealed
the real UPS error ("Missing or invalid Package PackagingType Code"), the *first* fix (wrap `Package` in a
list) was wrong — Tim retried, identical error. Second attempt went straight to UPS's own official spec repo
(`github.com/UPS-API/api-documentation`, `Shipping.yaml`) instead of guessing again: the real bug is the field
name — it's `Packaging`, not `PackagingType`, always has been — and the array-wrap from attempt 1 was itself
wrong for a single package (UPS's own examples show the array form is only for multi-piece/LTL shipments).
UPS's error text named "PackagingType," which matched our (wrong) field name and gave false confidence during
the first attempt — the error text turned out to be generic/templated, not derived from the actual payload
keys sent. **Tim had not yet retried this fix as of end of session — next session, check whether it actually
worked.** See [[feedback-impound-manager-strict-rules]] "Tenth occurrence" for the process lesson.

**Staging synced to match production** — was 32 commits behind (further back than tonight, included the
WP-6-9 promotion and training-tooling work too). Force-pushed `origin/main` onto `origin/staging` after
confirming staging had no exclusive/unpromoted work of its own (its 13 "exclusive" commits were the
pre-cherry-pick originals of the same WP-6-9/training work already on main under different hashes — nothing
lost in substance). Confirmed via `/version` on both hosts.

### ✅ NEW — August 2, 2026 overnight (WP-6–9 promoted to production + Task 2 gate bug + Letter 1 backfill)

**WP-6 through WP-9 promoted to production**, after Tim ran his own UAT pass on staging (walked through inline
Send Now on a real F-150, inline File Title on a real Camry, Heather's narrowed nav, and a live UPS Postage
void test). Cherry-picked the 5 WP commits (`1f90ba2` item 3/WP-6-session-1, `95d389e` WP-6-session-2,
`c287367` WP-7, `a1df938` WP-8, `0cdfaad` WP-9) onto `main`, verified via `/version`, `reset_users.py` run.
All four are now live: inline Send Now / File Title buttons, decluttered vehicle detail page (accordions),
Heather's narrower menu, the UPS Postage page, POLICE one-letter rule.

**Real defect found and fixed immediately after, on production (commits `ada8b33`, `83a81bb`):** the new
Task 2 — 1st Notice Letter card mis-gated Letter 1. Day 5 (`TASK2_OPEN_DAYS`) has always been the letter's
**DUE-BY** deadline, not a minimum wait — `task_engine.py` and `Vehicle.letter_send_block_reason` only ever
required BMV Search to be done. But the WP-6/WP-7 rewrite of the Task 2 card hid its Send Now button and
said "Opens day 5" until 5 days had actually passed, contradicting the real rule (letter must go out **within**
the first 5 days) and the rest of the app. Fixed: button now shows and text reads "Send by [date]" as soon
as BMV Search is done. Also added: Send Now works even before Letter 1 has been generated (opens the letter
picker instead of requiring a separate trip to the top "Generate Letters" button first — matches how Task 3
already behaves). Verified on staging first, then promoted the same way. Audited all 718 active vehicles
afterward: only 1 (the F-150) was ever actually caught in the broken window — bug was live under an hour.

**Separately found while auditing: 39 vehicles with a genuinely overdue, never-generated Letter 1** (unrelated
to the bug above — these were already past day 5 the whole time, so they'd have worked fine even before the
fix). Dating back to 07/06/2026. Gave Tim the full VIN list to cross-check against Towbook. First one checked
(2007 Chevrolet Silverado, invoice #728501) turned out to have actually been mailed by Tim directly on
ups.com on 07/15/2026 (proof: UPS label PDF) — never recorded in the app at all.

**Root cause behind both of the above, confirmed: `_resolve_letter()` in app.py refuses to create
letter_number=1 through the Generate Letters hub — "intake owns it" — and `towbook_import.py` (the daily
CSV pipeline, i.e. almost every vehicle) never creates it either.** This is the exact gap flagged as an open,
unverified item after the July 28–29 session ("verify fresh Towbook-synced car → 1st-letter queue handoff").
Confirmed tonight it's real and ongoing. Ran the already-built `generate_letter1_backfill.py` in the
**production** Render Shell: **210 vehicles had zero certified_letters rows** — all backfilled with an
unsent Letter 1 at the correct due date (impound_date + 5, not today). This fixed the existing backlog but
**does NOT stop new Towbook imports from having the same gap tomorrow** — `towbook_import.py` still needs
to actually create the Letter 1 row on import. **This is the top open item for next session.**

Recorded the Silverado's real Letter 1 as sent via the safe manual-entry path (`/letters/<id>/mark-sent`,
NOT "Create UPS Label & Mark Sent" — that would have bought a real second label): sent date 07/15/2026,
tracking `1Z81Y7X14216659365` from the real label. Confirmed Letter 2 auto-scheduled correctly off the
07/15 sent date (08/14/2026), not off today's date.

**⚠ Side effect surfaced by the backfill, not yet acted on:** the dashboard's Overdue Letters count jumped
from near-zero to **626** — vehicles going back to 2019 that were sitting ACTIVE with no letter row at all
(so invisible in every queue) and are now surfacing with the backfilled due date. This is almost certainly
old status hygiene (vehicles actually released/junked/sold years ago and never closed out in the system),
not real letters that need to go out today. Needs a cleanup pass cross-checked against a current Towbook
export (same pattern as the release reconciliation a few weeks back) — **not urgent, don't triage at 1am,
but don't ignore it either.**

### ✅ NEW — August 1, 2026 (training-video tooling + 2 production fixes found via dogfooding)

**Video 1 training baseline — `seed_training_baseline.py` (repo root).** Idempotent seed of 10 vehicles
(`stock_number` `TRAIN-01`..`TRAIN-10`), each frozen at a different chapter of one clean, optimal-path
story (new intake → BMV search → Letter 1 → 30-day wait → Letter 2 → Tina's sell/junk tracks → pending
pickup → released) — deliberately zero anomalies, so it teaches "what right looks like" first. Safe to
re-run any time (only ever touches `TRAIN-%` rows). Companion one-click **Training Data Reset** page at
`/admin/training-reset` (`blueprints/admin.py`, Tim/Jim-only) — hard-gated on `IS_STAGING` so it cannot
appear or run on production, no matter what.

**Video 2 practice kit — `marvel_impound_test_kit/`.** A second, separate set of 10 fictional vehicles
(`MV-2026-101`..`110`, Marvel/DC-themed — Stark, Rogers, Romanoff, Odinson, Banner, T'Challa, Danvers,
Kent, Prince, Wayne), deliberately independent of TRAIN-01..10 so this exercise never disturbs video 1.
Generator script `build_kit.py` in that folder produces 5 progressive daily Towbook CSV exports (2 new
vehicles/day, cumulative full-lot snapshot, realistic day-over-day balance growth — tested end-to-end
against the real `_do_import()` route) and 10 sample LKA/Title-Abstract BMV PDFs, deliberately mixed
(clean pairs, one pair with a deliberate name/address mismatch to demo the discrepancy-flag UX, several
single "still waiting on the other document" vehicles, a few with nothing uploaded yet).

**Two real defects found and fixed while building the above (both now live on PRODUCTION):**
1. **Daily Intake BMV Documents batch had no finish confirmation** (`templates/heather/daily_intake.html`)
   — after dropping in a stack of LKA/Title PDFs, results just appended as a growing row list with nothing
   tallying them up. Added a summary banner ("Done — N processed, X still waiting on the other document,
   Y no match...") at the top once a batch finishes.
2. **Towbook CSV import never showed which vehicles were new** (`towbook_import.py` + same template) —
   only ever returned a bare "N new, M updated" count. Now returns a `new_vehicles` list (id/stock/plate/
   year/make/model/impound_type) and the Daily Intake page renders it as a clickable list straight into
   each new vehicle, so Heather doesn't have to separately go check the BMV Search Queue to see what
   actually needs her.

**⚠ Confirmed: `git push` from Claude Code's environment now works directly** — the long-standing "403,
must push from Tim's own machine" limitation (2026-07-26 note) no longer applies. Don't re-litigate this
each session; just push.

**⚠ Two-branch deploy model, read before pushing anything:** `origin staging` deploys to
`impound-manager-staging` (srv-d9m7oorm8hqs73a491d0); `origin main` deploys to production
(srv-d909ske8bjmc7391ikig). Pushing local `main` wholesale to `origin main` would ship *everything* sitting
on local main, including work only meant for staging. For production, isolate exactly the reviewed commits
(cherry-pick onto a throwaway branch built off `origin/main`, confirm the diff-stat touches only the
intended files, push that branch to `main`) rather than pushing local main directly. Verify any deploy via
the no-login `/version` endpoint (`commit`, `commit_short`, `deployed_at`, `is_staging`) on the right host.

**Staging has a real safety mechanism worth knowing about: `IS_STAGING` env var** (set `true` only on the
staging service). Gates: (1) `ups_api.create_label()`/`void_shipment()` — fabricates a `1ZFAKE`-prefixed
tracking number and placeholder label instead of ever calling the real (production, billed) UPS Ship API;
(2) the Training Data Reset admin route above. Never weaken this gate.

**Separate, NOT-done-here project exists in parallel: the WP-0..9 "Remediation" effort** (started
2026-07-31, its own doc set — `BUILD — Impound Manager Remediation — Spec — 2026-07-31.md`,
`DEFECT-LEDGER.md`, `COMPLIANCE-TRUTH.md`, `Impound Manager Remediation — Tracker.html` is the
authoritative status table for that project, more current than this doc on that topic). Per that tracker
as of today: all 9 work packages are code-complete and verified; WP-0–WP-5 (+ item 3) are already on
production; **WP-6–WP-9 are staging-only, explicitly waiting on Tim's own UAT pass before promotion** —
next session's stated priority. Items 5 and 6 remain deliberately OPEN/gated pending Tim's confirmation.

### ✅ NEW — July 30, 2026 PM (daily-workflow batch — PRs #14, #18–#21, all merged + deployed same day)

**Background:** a date-windowed Towbook export on 07/29 false-flagged ~551 cars as Possible Release; export recipe corrected to a full current-lot snapshot, making reappearance-in-CSV a reliable "still on lot" signal.

**Possible-release auto-clear (PR #14 CSV; PRs #16/#17 API path)** — a flagged vehicle reappearing in a Towbook pull auto-clears its flag: `tina_sync.auto_clear_possible_release(vehicle, source=...)` (no-commit helper — rides the import's single commit), system VehicleNote, `possible_release_cleared` count in the import/sync JSON + blue badge on Daily Intake. PR #16 also fixed API-synced vehicles never getting `towbook_seen=True` (were permanently ineligible for flagging).

**BMV ↗ quick-link (PR #18)** — button on Heather-dashboard BMV queue rows + vehicle Task 1 card: copies the VIN (plate fallback) to clipboard and opens the Ohio BMV Last Known Address search (`services.dps.ohio.gov/AbstractAccounts/User/LastKnownAddress`) in a new tab — the $5 portal can't be URL-prefilled, so copy+open is the ceiling. `openBmvSearch()` in base.html. Gotcha: Flask `|tojson` inside an onclick needs a SINGLE-quoted HTML attribute (it emits double quotes).

**Today view (PR #18)** — `/heather/today` ("Today" in Morning Workflow nav): one flat date-sorted work list, tabs Overdue / Today / Upcoming-7d. Towbook's Impound-Tasks presentation WITHOUT manual checkboxes — rows derive from vehicle/letter state and clear themselves (Towbook's own list showed 1,205 rotted overdue tasks). Types: BMV search (due day 3), unsent letters (due_date), tracking follow-up (sent+7 unconfirmed), Verify Possible Release, No Record review.

**Police letters printed blank owner — FIXED (PR #19)** — root cause: BMV Done modal stored owner info only in `bmv_search_notes` free text, but `print/letter.html` reads `vehicle.owner_name/address/city/state/zip` (PPI got those from the LKA scanner; POLICE went through the typed modal → Notice of Lien printed "[REGISTERED OWNER NAME]"). Both BMV Done modals (dashboard queue + vehicle detail) now have structured Owner name/Street/City/State/Zip inputs writing the real columns; blank inputs never erase existing values. ⚠ OPEN: POLICE vehicles BMV-done before 07/30 still have owner info trapped in notes — one-time backfill offered, not built; interim fix per car = Replace-upload the LKA (auto-read fills it) or Edit.

**Photo upload multi-file + ZIP (PR #19)** — vehicle-page photo box now accepts any mix of images and ZIPs in one submit (was one file per submit); junk ZIP entries skipped, added/skipped counts flashed. Bulk page accepts RENAMED ZIPs via the typed Call Number (previously only `call_XXXXXX_files.zip` matched).

**LKA/Title auto-read on vehicle-page upload (PR #20)** — the vehicle Documents upload now runs the same AI extractor as Daily Intake, server-side: the stored PDF goes straight to the Claude API as a document block (claude-opus-4-8, same prompts — `bmv_document_scanner.extract_from_document()`). Fills empty owner/lienholder/title#/mileage; NEVER overwrites; VIN-mismatch guard refuses to fill (wrong owner on a letter = legal exposure); extraction failure never blocks the upload; result reported in flash + VehicleNote. ⚠ KEY LESSON (cost an hour of confusion): **deploys never backfill — the reader runs only at upload time.** For already-filed docs, click Replace and re-upload to trigger it.

**Date Change Log + restart crash fix (PR #21)** — Tina's ask: `/reports/date-changes` (nav Letters & Titles → Date Change Log; tim/tina/heather/jim) audits every letter-clock date change — manual Restart Letter Clock uses AND automatic RTS round-restarts — from the audit notes both flows already write (history included back to day one). NOTE: Heather ALREADY had Restart Letter Clock access (is_heather on button + route, reason required) — no permission change was needed or made; impound_date stays locked for everyone (60-day title clock) by design. Also fixed a real 500: restarting the clock on a vehicle with zero letter rows crashed (`CertifiedLetter` must be constructed with `vehicle=vehicle`, not bare FK — `.label` reads `self.vehicle` before flush).

**Towbook API access negotiation** — Gabe Al-Gharabally (Customer Delivery Mgr, gabe.a@towbook.com) responded to Tim's API request with spec questions; Tim replied 07/30: read-only daily HTTPS/JSON pull, filter = impound calls currently on the lot (or updated last 90d), field list matching `towbook_import.py` exactly. If granted, set `TOWBOOK_API_TOKEN` + `TOWBOOK_COMPANY_ID` in Render env — the 5AM sync switches on with zero code changes.

**Also:** HANDOFF_FOR_JIM.md security lock-down checklist (2FA, unique passwords, SECRET_KEY, shared-default-password warning) committed direct to main (`4a9e8f4`) — was sitting uncommitted on Tim's machine.

## ✅ OPS — MEMORY / OUT-OF-MEMORY (RESOLVED July 28, 2026)
The old **"Ran out of memory (over 512MB)"** auto-restarts are **RESOLVED** — the service is now on Render **Standard (2 GB RAM)** (confirmed July 28; not the 512 MB Free/Starter tier). **Do NOT push a further upgrade** — 2 GB is ample for this app + the background jobs (boot recalc, Towbook sync 5AM, urgency recalc 6AM, UPS auto-poll every 3 hrs). If an OOM ever recurs *on 2 GB*, that's a real memory leak to chase (most likely the base64 image blobs), not a plan-size issue.
- **Longer-term (parked, blocked on IT):** move the base64 image blobs (envelope scans, damage photos, UPS labels/PODs, general docs) out of Postgres — biggest memory driver. Retention design is decided — **nightly off-site backup + a monthly manual purge that keeps the legal-evidence images (UPS PODs + damage photos)** — but **blocked on Tim's IT dept choosing a storage destination.** Purge must refuse to delete anything not already backed up.

---

### ✅ July 13, 2026 (earlier — disposition pipeline + UPS Phase 2)

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
- ✅ UPS Phase 2 auto-poll — **DONE July 26** (scheduled `ups_delivery_poll`, every 3 hrs 8am–8pm ET; starts Letter-2 clock automatically). A true unattended **email/SMS delivery digest** is still NOT built — only revisit if a push summary is wanted on top of the auto-poll.
- 🅿 Build 14: VinAudit — waiting on `VINAUDIT_API_KEY` in Render.
- 🅿 PPI Sales tracker (John Payne) — deferred.
- 🅿 Base44 rebuild — **DONE** this session (in-house disposition pipeline). External Base44 retired.

## BUILD QUEUE

### ✅ Recently completed (verify then clear)
- ✅ **Find Trucks — VIN reclassification** — DONE July 28, commit 36837a1 (`/admin/reclassify` + `vin_decode.py`, NHTSA GVWR → light/medium/heavy; "Detect from VIN" button on intake/edit). This is the "Easier truck reclassification" open item — closed.
- ✅ **Release reconciliation** — DONE July 28. 18 still-active bulk-eligible cars marked Released via `/audit` CSV cross-reference.
- ✅ **July-outage 1st-letter reconciliation** — DONE July 28–29, commits fe49fbe / c99549b / fd22b6e. `import_ups_letter_dates.py` (56 letters created from real UPS send dates) → `attach_ups_tracking.py` (68 tracking attached) → Refresh UPS Tracking (61 delivered → 61 2nd-letter timers started). Fallback `mark_letter1_sent_reconcile.py`. See the July 28–29 completed-builds section for the full detail + gotchas.
- ✅ **512 MB OOM restarts** — RESOLVED July 28 (now on Render Standard 2 GB). See OPS note.
- ✅ **Sequential task gate (Letter 1 locked behind BMV Search)** — DONE July 26, commit d5f8e3a. Reverses the July-7 dual-unlock; server-side, all impound types.
- ✅ **UPS delivery auto-poll** — DONE July 26, commit 1c59b00 (`ups_delivery_poll`, `ups_poll.py`).
- ✅ **"Test UPS Connection" button** — DONE July 26, commit 1c59b00 (`/admin/ups-test`).
- ✅ **Letter Workflow Guide** — DONE July 26, commit 388fad9 (`/guides/letter-workflow` + nav link).
- ✅ Release compliance hard-stop gate — DONE (`Vehicle.release_to_customer_blocked_reason`, enforced in `/vehicles/<id>/release`).
- ✅ Daily release list for Lawrence — DONE (`/release-list`, PR #1).
- ✅ Build E: General Documents Upload — DONE (`vehicle_general_documents`, detail-page section).
- ✅ Build A: Envelope Tab + image attachment — DONE (`/envelopes` Matched/Unmatched/Cleared + dashboard badge).
- ✅ Build C: Staff Guide VIN-Snap sections — DONE (both guides).
- ✅ Build B: 5-letter templates + police-dept rates + vehicle class — DONE (class-based PPI storage fees, PR #1).
- ✅ Build Q: QR scanner on /driver — DONE (PR #1).
- ✅ Top-nav 4-section overhaul — DONE (PR #1).
- ✅ **WP-6 through WP-9 UAT + promote to production** — DONE August 2, 2026 overnight. Tim UAT'd all four on
  staging, promoted to production, plus a real Task 2 gating bug found and fixed same session. See the
  "August 2, 2026 overnight" entry above for full detail.
- ✅ **PRIORITY — `towbook_import.py` never creates a Letter 1 record** — DONE August 2, 2026 late evening.
  Root cause actually fixed (not just backfilled) in both `towbook_import.py` and `towbook_api.py`, plus two
  "not a real impound" guards (transport/relocation, Goose/PVG Brokerage). See the "late evening session"
  entry above for full detail.

### ⬜ Open / not started (next-session queue)
- ⬜ **PRIORITY (Tim's stated goal for next session) — verify the whole multi-party letter system end to end.**
  Confirm the system correctly identifies every party on a call who needs a letter — owner, 2nd address
  (title vs. LKA), lienholder, and eventually the impound-slip owner for POLICE — and that there's a clean,
  clear way to see at a glance which parties on a given vehicle have been sent letters vs. still pending.
  Tonight built the underlying data (2nd-recipient delivery/RTS tracking, per-recipient status blocks on the
  Certified Letter Timeline) but it has NOT been exercised on a real multi-party vehicle yet. Start here: pick
  a real vehicle with an actual 2nd address or lienholder, walk the full send→track→confirm flow, and see if
  it actually reads clearly or needs another UI pass (a round-level "2 of 3 sent" summary badge was designed
  but not built — see the "2nd-address delivery/return tracking" section of the late-evening entry above).
- ⬜ **Verify the UPS Ship API 400 fix actually worked** — 2nd attempt (field renamed `PackagingType` →
  `Packaging`, array-wrap from the 1st attempt reverted) is live but **Tim had not retried by end of session**.
  Do this before anything else UPS-related — if it's still broken, that's new information, not the same bug.
- ⬜ **Verify the `total_owed` billing fix** — confirm a real POLICE letter now shows a real dollar amount
  (was $0.00 for a real Chrysler 300/Richard Prentiss letter, root-caused and fixed same night, not yet
  re-verified on a live letter print).
- ⬜ **159-vehicle Towbook/IM letter-status mismatch list** — IN PROGRESS, Tim working through by hand with
  real Towbook evidence per vehicle. Not urgent (evidence suggests most aren't real missing letters — see the
  late-evening entry above), but not done either.
- ⬜ **Impound-slip-vs-BMV-owner comparison** — Jim's policy: for POLICE impounds, check the physical impound
  slip's own Owner field against what BMV search finds; if they're a different person, that person also needs
  a letter. Spec'd in detail (upload the slip photo — already sitting in Towbook, needs saving+uploading here
  — auto-read with the same LKA/Title AI reader, compare, auto-populate the existing "2nd owner" slot if they
  differ, which already triggers a 2nd letter automatically). **Not built.** Open question from Tim, unresolved:
  does this replace Tina's existing manual Towbook workaround (she manually enters "driver" as an individual
  + "owner" separately in Towbook itself to make Towbook's own letter auto-populate work, then Tim has to
  remove the driver entry after so it doesn't pollute "BMV packages") or would that keep happening regardless?
- ⬜ **Full USPS API / electronic certified-mail integration** — tonight's PO Box fix is manual-tracking-number
  only (buy Certified Mail at the counter or via free Click-N-Ship, check usps.com by hand). Tim wants real
  one-click certified mail like AutoDataDirect apparently offers — **check with ADD directly first** (the app
  already has an "Import from ADD123" button, so B&J likely has an existing account relationship) before
  building a fresh integration with a different vendor (Lob.com, SimpleCertifiedMail, USPS's own Web Tools
  API). Comparable in size to the original UPS integration — real project, not a quick add.
- ⬜ **Placeholder-name safeguard** — reject obvious placeholder text ("Impound Slip," "Individual," "Same,"
  etc.) from ever landing in `owner_name`, so it can't print on a real letter/title packet. Tim said hold off —
  still mid-investigation with Tina on where these placeholders actually come from.
- ⬜ **626 "Overdue Letters" cleanup, remainder** — the Towbook Release Cross-Reference (`/audit` Section 1)
  will auto-clear genuinely-released vehicles once Tim uploads a release-history export; the 159-mismatch work
  above is a separate, more manual piece of the same overall backlog.
- ⬜ **38 remaining vehicles from the pre-existing overdue-Letter-1 backlog** — full VIN list given to Tim
  08/02 to cross-check against Towbook (39 found, 1 — the Silverado, invoice #728501 — already resolved:
  it was mailed manually via ups.com 07/15 and is now recorded). For any that Towbook confirms were actually
  mailed outside the app, use the same safe manual "Mark Sent" entry (never "Create UPS Label" for those —
  it would buy a real duplicate label).
- ⬜ **Design / UX pass** — make the app more user-friendly. Not yet started; needs Tim's pick of where to begin (the vehicle ticket / the dashboards / a whole-app consistency polish / the mobile+large-text screens) and what "user-friendly" means to him (declutter / bigger text / consistency / fewer clicks).
- ⬜ **Black Book URL** — the valuation button points at the marketing site; swap in the exact B&J subscriber-portal login URL when Tim provides it.
- ⬜ **VinAudit (Build 14)** — blocked on `VINAUDIT_API_KEY` in Render; build the auto-lookup once the key is set.
- ⬜ **Per-class tow rates** — only storage is class-based; tow is flat $144 (editable per ticket). Awaiting Tim's light/medium/heavy tow numbers if tow should scale too.
- ⬜ **Image backup + monthly purge** — retention design decided (nightly off-site backup; monthly manual purge that KEEPS legal-evidence images = UPS PODs + damage photos; purge refuses to delete anything not already backed up). **BLOCKED on Tim's IT dept picking a storage destination** (on-prem NAS / M365 / Google / S3 / Backblaze — build is destination-agnostic). This is also the long-term fix for the base64-blobs-in-Postgres memory driver.
- ⬜ **Relo-trans cars** — transport cars staged in the lot are NOT impounds but sit in inventory generating letter/storage tasks. Tim researching how to categorize them (likely a "Relo / Transport" tag that keeps them in inventory but out of the impound letter/title pipeline). 2 currently in the audit list left untouched.
- ⬜ Disposition follow-ups: auction-event edit page; per-load Ohio Steel batch grouping; push/SMS on repair alerts; a "repairs in progress" sub-state between approve and auction-ready.
- ⬜ **Owner-info backfill for pre-07/30 POLICE vehicles** — owner info trapped in `bmv_search_notes` free text (the modal didn't write owner columns until PR #19). One-time parse-notes→owner-fields script offered, not built. Interim: Replace-upload the LKA (auto-read fills it) or Edit per car.
- ⬜ **Towbook API decision** — awaiting Gabe/Towbook response to the read-only pull spec. If yes: two Render env vars and the 5AM sync goes live.

**Operational follow-through (not builds):** the ~61 now-due 2nd letters from the outage reconciliation are Heather's to work through (expected wave, not a bug); the 4 old cars with no UPS-export match still lack tracking.

### 🔗 Sibling app — BJ Books (separate repo/service)
`timjr4301/bj-books` → bj-books.onrender.com (Nightly Books). Distinct app, own Render service/memory. Recent work: ECR Z-report parser fixes (bare-`\r` delimiter, DP/PAY field maps, `register_ra` capture — migration 025). Open: the invoice-upload flow bogs down / "gets overloaded" when many PDFs are each read by Claude at once — needs throttling / decoupling upload from AI reading. **Do not confuse with impound-manager or the suspended bj-impound-manager.**

---

## KEY OHIO COMPLIANCE RULES
- **PPI title eligibility: 60 days after Letter 1 is confirmed DELIVERED or confirmed UNDELIVERABLE (return-to-sender)** — NOT from `impound_date`, and NOT from Letter 2 at all. ⚠ CORRECTED 2026-07-31 (WP-2, CP-CLOCK, `COMPLIANCE-TRUTH.md`) — reverses the prior `max(impound_date+60, letter2.sent_date+30)` formula, which a statute check (ORC 4505.101(B)(3)/4513.601(F)) found no basis for. Enforced in `Vehicle.title_eligible_date`/`title_blocked_reason` (`models.py`), reading the new `CertifiedLetter.delivery_or_undeliverable_date` property. **POLICE title eligibility is UNCHANGED and still unresolved** — still `Letter1.sent_date + 30`, no floor (`models.py:813-816`) — pending counsel confirmation (`COMPLIANCE-TRUTH.md` item 5). Do not assume the PPI fix also applies to POLICE.
- **Letter 2 is due 30 days after Letter 1 is SENT** (sent-anchored). ⚠ CORRECTED 2026-07-29 — reverses the old delivery-anchored design (de71135). Delivery is still recorded (POD/proof) but does NOT drive the 2nd-letter timer. Enforced in `task_engine.compute_task`, `models.letter_send_block_reason`/`next_action_label`/`stoplight_color`, and `blueprints/audit.py` (all use `l1.sent_date + 30`). DO NOT revert to delivery-anchoring. **This rule is separate from and unaffected by the 2026-07-31 title-eligibility correction above** — Letter 2's SEND timing didn't change, only when title may be sought afterward did.
- Electronic POD (UPS POD or scanned DELIVERED envelope) satisfies certified-mail requirement.
- NADA wholesale value must be less than total fees owed.
- B&J BMV vendor #: 25-186078.
- BMV 4202 = private property; BMV 4205 = police. PO Box → compliance flag. Out-of-state → court process.
- `impound_date` is the permanent 60-day clock — NEVER use as a restart source (`restart_date` re-anchors letters only).
- **PPI fees:** tow flat $144 (editable per ticket). Daily storage by vehicle class — **light $22 / medium $37 / heavy $82** (`Vehicle.PPI_STORAGE_RATE_BY_CLASS`; seeded on intake, editable per ticket, feeds both the bill and the letter). POLICE fees come from the requesting department (`police_departments` table); a POLICE ticket with no department shows RATE PENDING.
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
- **NEW Vehicle columns (queue-clearing session):** `released_at`, `released_by` (final-release stamp → Daily Release List), `vehicle_class` (light/medium/heavy → PPI storage fee). All auto-migrate on boot. Vendored `static/js/jsQR.min.js` for the /driver QR scanner.
- **Tables from the disposition session:** `ups_poll_log`, `custody_events`, `auction_events`.
- **Vehicle columns (disposition session):** `tina_stage_at`, `disposition_outcome`; auction (`auctioneer`, `auction_lot`, `auction_date`, `auction_venue`, `auction_event_id`); converter (`converter_present`, `converter_checked_by/at`, `converter_photo`, `converter_notes`); custody (`custody_location*`, `key_location*`); key (`key_made`, `key_type`, `key_cost`, `key_made_by/at`); inspection (`inspection_claimed_by/at`, `inspection_done`, `inspection_diagnosis`, `inspection_notes`, `inspected_by/at`); repair (`repair_estimate`, `repair_notes`, `repair_approved`, `repair_decided_by/at`).
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
