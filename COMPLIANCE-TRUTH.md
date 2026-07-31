---
type: wp2a-output
status: DRAFT — awaiting Tim's typed confirmation at CP-CLOCK below. Nothing in this document is enforced until he signs off. WP-2b (the code change) does not start until this file carries his confirmation.
produced_by: Claude Code, read-only session, 2026-07-31
---

# COMPLIANCE-TRUTH — the one page Tim reads at CP-CLOCK

This resolves spec §5 WP-2a's six questions. **Read the "IMPORTANT — how to read the statute findings" box below before anything else** — I hit a real problem sourcing the exact legal text, and I'm not going to paper over it.

## IMPORTANT — how to read the statute findings in this document

I pulled the Ohio Revised Code text through an automated web-fetch tool, not by reading a raw PDF myself line by line. On repeated fetches of the *same* statute section, the tool gave me **inconsistent answers** — different subsection labels for the same clause, and on one attempt it invented a "notices at 30 and 45 days" requirement that appears nowhere else and that I'm confident is fabricated (I've discarded it; do not act on it). Where two or more independent fetches (different source sites) converged on the same substance, I'm treating that as a reasonably solid finding. Where they didn't converge, I say so explicitly and mark it **NEEDS COUNSEL**.

**This document is not a substitute for your named escalation path.** Two of the findings below (title-eligibility anchor for PPI, and the POLICE clock/chain) are significant enough — and my sourcing confidence is low enough — that I'd get a real lawyer or the towing-association seminar materials to confirm before you type your answer below, not just take my word for it.

---

## 1. What the code actually does (I read this directly — 100% confidence)

| Rule | Code | Citation |
|---|---|---|
| PPI Letter 1 deadline | 5 days from impound | `models.py:12` (`PPI_LETTER1_DAYS = 5`) |
| PPI Letter 2 trigger | 30 days after Letter 1 **sent** | `task_engine.py:32`, `models.py:436-442`, `letter_triggers.py:68`, `blueprints/audit.py:36` — four separate constants, currently consistent (V-6) |
| PPI title eligibility | `max(impound_date + 60, Letter2.sent_date + 30)` | `models.py:805-812` |
| POLICE Letter 1 deadline | 10 days from impound | `models.py:17` (`POLICE_LETTER1_DAYS = 10`) |
| POLICE title eligibility | `Letter1.sent_date + 30`, **no floor off impound_date** | `models.py:813-816` |
| POLICE letter chain | 1 (Notice of Lien) → BMV complete triggers 3 (+5 if lienholder) → Letter 3 sent triggers 4 (+6 if lienholder), same 30-day gap as PPI | `letter_triggers.py:50-76` (`on_bmv_complete`, `on_letter_sent`) |
| MASTER_CONTEXT's documented rule | "60 days from impound_date + 30 days after Letter 2" | `MASTER_CONTEXT.md:198` — matches the code above exactly |

## 2. What Tim said on the 07/30 call (verbal, not verifiable against the repo)

- Title eligibility: 60 days after Letter 1 is **delivered or returned** (not impound_date, not Letter 2).
- POLICE impounds take **one** letter, not a multi-letter chain.

## 3. What the statute appears to say (web-fetch sourced — see the confidence box above)

**PPI (ORC 4513.601(F), title mechanics actually live in ORC 4505.101(B)(3)):**
- First notice: within 5 business days (of removal or of the owner's identity being confirmed — fetches disagreed on which; both land on "5 business days," just anchored slightly differently).
- Second notice: if unclaimed 30 days after the first notice is **sent** — this matches the code's current Letter-2 trigger exactly. **No open question here.**
- Title process may begin: **"sixty days after the date the earliest notice required by division (F) of section 4513.601 is received, as evidenced by a receipt signed by any person, or the towing service... has been notified that delivery was not possible"** — quoted from ORC 4505.101(B)(3) via two independent fetches that agreed with each other. One fetch explicitly added: *"not from the initial impoundment date or when notice was sent."*

**If that quote is accurate, it directly contradicts the current code and MASTER_CONTEXT** — both of which anchor off `impound_date` and off Letter 2's *sent* date, not off Letter 1's *received/delivery-failed* date. It lines up closely with **Tim's verbal rule**, not with what's implemented. This is the opposite of what the independent validation report (V-1) concluded — V-1 found the code and MASTER_CONTEXT "agree" and treated Tim's verbal memory as the likely-stale side. My statute research suggests it may be Tim's memory that's right and the implementation that's wrong. **I am not certain enough of this to say so definitively — this is exactly the kind of finding that needs your named escalation before anyone touches code.**

**POLICE (ORC 4513.61 + 4505.101):** fetches consistently describe a 10-day claim window after notice is sent, then an affidavit-based salvage-title process — no second notice, no 30-day gap mentioned anywhere in 4513.61 itself. Separately, 4505.101(B) may impose the *same* 60-days-after-receipt rule found for PPI, regardless of tow type — my fetches weren't able to cleanly separate "does the POLICE waiting period differ from PPI's" from "the tool re-serving me the PPI answer when I asked about POLICE." **NEEDS COUNSEL** — I don't have a reliable answer here, and I'd rather tell you that than guess.

## 4. The POLICE letter-chain question (V-3, confirmed at the code level)

Code holds two beliefs at once: it builds POLICE a 1→3→4(+lienholder) chain (`letter_triggers.py`) while `title_eligible_date` for POLICE reads *only* Letter 1 (`models.py:813-816`) — so the second POLICE notice currently has no effect on title timing even though it's generated and sent. Whether ORC 4513.61 requires a second POLICE notice at all is unclear from what I could source (see §3 above) — it may be a belt-and-suspenders practice rather than a statutory requirement, or the statute may require it and my fetches simply didn't surface it.

## 5. The six questions — my proposed answers, with confidence flagged

| # | Question | Proposed answer | Confidence |
|---|---|---|---|
| 1 | Letter 1 deadline | Keep as-is: PPI 5 days, POLICE 10 days | High — no conflict found anywhere |
| 2 | Letter 2 timing | Keep as-is: 30 days after Letter 1 **sent** | High — statute text (§3) and code agree exactly |
| 3 | POLICE letter chain | Keep the chain, but need confirmation it's statutorily required (or intentionally a stricter internal practice) rather than assuming it | **NEEDS COUNSEL** |
| 4 | Title-eligibility date (PPI) | Candidate: 60 days after Letter 1's **delivery-confirmed or delivery-failure date** (not impound_date, not Letter 2) — this matches Tim's verbal rule and the strongest statute reading I could source | **NEEDS COUNSEL — this would reverse the current implementation, not just patch it** |
| 5 | Does the 60-day-from-impound floor apply to POLICE? | Reframe: the real question may not be "does PPI's floor also apply to POLICE" but "does POLICE title eligibility use the *same* receipt-anchored 60-day rule as PPI, via ORC 4505.101, rather than the current sent+30-no-floor formula" | **NEEDS COUNSEL** |
| 6 | Should the daily Towbook import auto-create Letter 1? | Yes, recommended — `towbook_import.py` creates `Vehicle` rows but never a `CertifiedLetter` row (confirmed, no `CertifiedLetter` reference anywhere in that file), which is the exact mechanism that produced the July-outage 56-letter gap (`MASTER_CONTEXT.md:73`). Auto-creating an unsent Letter 1 row (due date per item 1) on every new insert would put every new car on Heather's queue from day one instead of relying on someone noticing it's missing. | Medium-high — this one isn't a legal question, it's an operational gap I can verify directly in code |

## 6. CP-CLOCK — Tim's confirmation

**STOP. Do not edit any code based on this document until Tim has typed his answer below and it's committed to the repo.**

If you're not certain on items 3, 4, or 5 — and given what I found, you shouldn't sign off on those from my research alone — the named escalation is Ohio counsel or your towing-association seminar materials, not a guess (yours or mine).

```
Tim's confirmation (paste below, then this doc is done):

1. Letter 1 deadline: [ ]
2. Letter 2 timing: [ ]
3. POLICE letter chain: [ ]
4. PPI title-eligibility date: [ ]
5. POLICE 60-day floor / same-rule-as-PPI question: [ ]
6. Auto-create Letter 1 on import: [ ]

Confirmed by: ______________   Date: ______________
```
