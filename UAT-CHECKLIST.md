# UAT CHECKLIST — Staging

Run this on the **staging** URL, never on production. The 8 functional tests are adapted from `templates/guides/letter-workflow-guide.html`'s existing End-to-End Test Checklist (already used to verify production) — same tests, staging environment.

## Before you start

- [ ] The STAGING banner (yellow bar, top of every page) is visible on every page you load. If it's missing, stop — you may be looking at production by mistake.
- [ ] Log in with a demo/staff account (`test` / `BandJDemo!`, or any staff account at its default password). If a real-looking password doesn't work, that's expected — staging passwords were reset separately from production's.
- [ ] Confirm production is untouched: check `git log -1 --format=%H` on `main` matches the SHA you expect, and that nothing you do on staging shows up on the production site.

## The 8 functional tests

- [ ] **1 · Import Test** — Upload a Towbook CSV via Daily Intake. Confirm result shows correct new/updated/skipped counts. A new vehicle appears in Vehicles → Active with correct stock number and impound date.
- [ ] **2 · First Letter + UPS Label Test** — Find a vehicle with no sent Letter 1. Click Mark Sent → Create UPS Label. Confirm: label appears with a 1Z tracking number. After submit: vehicle detail shows sent date + tracking, and Letter 2 is auto-scheduled ~30 days out.
- [ ] **3 · Dashboard Stoplight Test** — Spot-check a RED vehicle — confirm its letter due date is actually in the past. Spot-check a GREEN vehicle — confirm it has a sent letter or no letters due. Click "Recalculate" and confirm colors don't change unexpectedly.
- [ ] **4 · Envelope Scan Test** — Scan a real or test envelope image via the Scan Envelope camera. Confirm: AI reads tracking number and outcome. System proposes the correct vehicle match. After confirming: vehicle detail shows delivery confirmed date.
- [ ] **5 · Unmatched Envelope Test** — Scan an envelope that doesn't match any active vehicle. Confirm: it appears in Envelopes → Unmatched tab (not silently lost). Heather's dashboard shows a yellow unmatched-scans warning banner.
- [ ] **6 · UPS Refresh Test** — Click "Refresh from UPS" on a letter with a real tracking number. Confirm status updates. If delivered, `delivery_confirmed_date` is set (proof of delivery). Letter 2's due date does NOT change — it stays 30 days from Letter 1's **sent** date.
- [ ] **7 · Ghost Vehicle Test** — Find a Possible Release vehicle. Confirm: it does NOT appear in letter queues. Confirm: clicking Mark Sent or Generate Letter for that vehicle shows a blocking error, not a form.
- [ ] **8 · Second Letter Timer Test** — Check a vehicle with Letter 1 sent. Confirm: Letter 2 due date = ~30 days from Letter 1's **sent** date (not delivery). It shows a due date even if Letter 1 hasn't been delivered yet.

## Two checks specific to tonight's compliance fix (WP-2)

- [ ] **9 · PPI Title-Eligibility Test** — Find (or create) a PPI vehicle with Letter 1 sent and delivery confirmed. Confirm the title-eligible date shown = delivery date + 60 days, NOT impound date + 60.
- [ ] **10 · Returned-to-Sender Choice Test** — Mark a sent Letter 1 as Returned to Sender. Confirm the form requires choosing a reason (address error vs. valid return) before it will save. Pick "valid return" — confirm the letter is NOT superseded and no new Letter 1 round starts. Pick "address error" on a different letter — confirm it IS superseded and a fresh Letter 1 round starts.

## When you're done

- [ ] Note anything that failed, with the exact step and what you saw instead of what was expected.
- [ ] Confirm again that production (`main`, the live `impound-manager.onrender.com` site) shows no trace of anything you did on staging.
