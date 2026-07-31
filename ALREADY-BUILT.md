# ALREADY-BUILT — features that answer a 07/30 ask, already shipped

Plain-language map, WP-0. Try these tonight — this is the fastest win in the whole build.

| Feature | Where it is | What it does | Try it tonight |
|---|---|---|---|
| **UPS postage tracking** | Letters page → **Labels** tab | Shows the lifecycle and status of every printed UPS label, with a Void action for ones that never shipped. Shipped in PRs #11/#12 (merged the morning of 07/30, hours before the call — Tim hasn't seen it). `templates/heather/letters.html:378`. | Open Letters, click the Labels tab, and see every label that's been printed. |
| ⚠ Labels tab window (partial) | same tab | The UI copy says void is "possible within **~90 days**" (`letters.html:383`) and shows "No labels printed in the last **90 days**" when empty (`letters.html:463`) — two different 90-day mentions, one for void eligibility and one for the display window. Neither is a single confirmed constant in code (no `days=90` found) — treat both as approximate until confirmed live. | Same tab — note whether the 90-day cutoff behaves as expected with real data. |
| **UPS connection test** | Letters page → **Test UPS Connection** button | Checks, in plain English: credentials present → UPS login works → optional live tracking lookup. Shipped commit `1c59b00` (`MASTER_CONTEXT.md:60`). | Click it once — should show green all the way down. |
| **Daily work list** | Morning Workflow → **Today** (`/heather/today`) | One flat, date-sorted task list — Overdue / Today / Upcoming-7d — built from live vehicle/letter state (nothing to check off by hand, rows clear themselves). Shipped PR #18. | Open Today and work straight down the Overdue tab. |
| **Date-change audit** | Letters & Titles → **Date Change Log** (`/reports/date-changes`) | Every letter-clock date change — manual Restart Letter Clock and automatic delivery-failure restarts — with who/when/why, back to day one. Shipped PR #21. | Open it and scan for anything unexpected. |
| **Truck reclassification** | Management → **Find Trucks (VIN scan)** (`/admin/reclassify`) | Scans active PPI trucks by VIN (NHTSA lookup), flags only the ones that need a light/medium/heavy correction — the ~90% that are already right stay hidden. Also a **Detect from VIN** button on the new/edit vehicle forms. Shipped commit `36837a1`. | Open Find Trucks and review whatever's flagged. |
| **BMV quick-link** | Heather dashboard BMV queue rows, and each vehicle's Task 1 card | One click copies the VIN and opens the Ohio BMV Last-Known-Address search in a new tab (the $5 state portal can't be pre-filled further than that). Shipped PR #18. | From any vehicle's Task 1 card, click the BMV link. |
| **Vehicle class is editable** | Any vehicle's **Edit** page | Light/medium/heavy selector drives the daily storage rate (`$22`/`$37`/`$82`), which feeds both the bill and the letter. This already exists in code — see `DEFECT-LEDGER.md` D12 for why it may not have been visible on 07/30. | Edit a vehicle, change Vehicle Class, save, and confirm the storage rate updates. |

## Not on the original "minimum" list, but answers a 07/30 ask

None found beyond the above during this pass — the six items above (plus the vehicle-class note) are what the code confirms.
