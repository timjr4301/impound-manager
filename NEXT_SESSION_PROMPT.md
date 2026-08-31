# Next session starter prompt — paste this to kick off

Read `MASTER_CONTEXT.md` first — the **August 25, 2026** entry (parties build) is the most recent and
covers what just shipped. Then let's pick up here.

---

## Main goal this session: Tina's title part

**Step 0 — ask me which part before writing anything.** "Tina's title part" could mean several different
builds, and they're not the same job. Ask me to pick, don't guess:

1. **The title-eligibility screen** (`/tina/title-eligibility`) — Ready / Upcoming / Blocked / Recently
   Filed. Is it showing her the right things in the right order? Is "blocked" actually actionable?
2. **The filing step itself** — `file_title` (app.py), which takes a BMV receipt number + notes, writes a
   `TitleFiling` row and flips the vehicle to `TITLE_FILED`. Thin today: `filed_date`,
   `bmv_receipt_number`, `status`, `notes` and nothing else.
3. **The title packet / BMV 4202** — `titlebot/pdf_gen.py` + `titlebot/BlankTitlePacket.pdf`, the
   "Generate BMV 4202" button on the vehicle page. What's on it, what's missing, what she still fills in
   by hand.
4. **Something else entirely** that Tim has in his head and hasn't written down.

### One thing worth raising with him unprompted

The parties build (08/25) made every interested party a first-class record — owner, 2nd owner, lienholder,
2nd lienholder, driver, anyone. **The BMV 4202 has a lienholder field, and the title packet was built when
the vehicle only really knew about one owner and one lienholder.** Ask whether the packet now needs to
reflect all parties on file, and which ones BMV actually wants named. This is a natural follow-on and he
may not have connected the two.

### What already exists (don't rediscover it)

- `Vehicle.title_eligible_date` / `is_title_eligible` / `title_blocked_reason` — PPI anchors on Letter 1's
  delivery-or-undeliverable date + 60; POLICE on Letter 1's sent date + its own window. **These read the
  registered owner's chain only** (`party_id IS NULL`) and that is deliberate — a non-owner party's letter
  never moves the title clock. Don't "fix" that without asking.
- `blueprints/tina.py::title_eligibility` — the four-bucket view.
- `app.py::file_title` + `templates/vehicles/file_title.html` — the filing form.
- `app.py::title_packet_pdf` + the `titlebot/` package (`pdf_gen`, `parser`, `nada`, `damages`, `storage`).
- `TitleFiling` model (models.py) — deliberately thin, 5 real fields.

---

## Carry-over from 08/25 — Tim's calls to make, not builds to start

- **POLICE lienholder notices are required but never auto-opened — probable compliance backlog.**
  Tim already settled the policy on 2026-08-04 (real case: 2022 Dodge Charger / Ally Financial): a police
  impound's lienholder gets their own separately sent, separately tracked Notice of Lien (letter_number 5).
  **Do not re-ask whether they should get one — they should.** The gap is that nothing opens it:
  `letter_triggers.on_vehicle_created` auto-creates the lienholder letter for **PPI only**, so on a POLICE
  impound letter 5 exists only once a human generates it from the Generate Letters hub. Found live on
  vehicle 11374 (Wells Fargo Dealer Services: "No letters opened yet"). Pre-existing, not a regression —
  the parties card just made it visible. **Count how many POLICE vehicles have a lienholder and no letter 5
  before proposing anything**, then offer the fix (extend `on_vehicle_created` + backfill existing).
- **PO Box addresses per party.** Same vehicle: Wells Fargo's address is a PO Box, and UPS won't deliver
  there. Every party now has its own PO Box checkbox that switches them to the USPS path. Worth a sweep
  to flag existing party addresses that look like PO boxes.

## Still open from before (unchanged)

- Impound-slip-vs-BMV-owner comparison for POLICE — spec'd, never built; **the unanswered question is
  whether it replaces Tina's manual Towbook workaround.** Ask before building.
- Owner-info backfill for pre-07/30 POLICE vehicles (owner trapped in `bmv_search_notes` free text) —
  directly blocks title packets for those cars, so it may fold into this session's work.
- Full USPS API / AutoDataDirect certified-mail integration.
- 159-vehicle Towbook/IM letter-status mismatch list — Tim working through by hand.
- Image backup + monthly purge — BLOCKED on Tim's IT dept picking a storage destination.

## Standing rules that bite on this project

- **New DB columns go in `app.py::run_migrations()`** (the boot one), never `blueprints/admin.py`'s
  admin-route version. That mistake caused a full production outage on 2026-07-22.
- Hand Tim the `[RENDER SHELL]` SQL for any schema change, labeled, even though boot auto-migrates.
- Never commit to `main` — branch, PR, merge.
- Pushing works again as of 08/25 (`gh` is authenticated as **timjr4301**). If a 403 returns, check
  `gh auth status` first.
- **A re-sent spec is not automatically stale, and not automatically current.** Inspect the live code,
  show Tim a delta, and let him decide. Specs on this project have repeatedly asserted columns, files and
  routes that don't exist — verify by enumerating `Model.__table__.columns`, never by grepping a name.
