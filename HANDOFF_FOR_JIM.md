# Impound Manager — How It Runs (Survival Guide for Jim)

_Plain English. No tech background needed. Last updated: July 29, 2026._

---

## Start here — if you're reading this and Tim isn't around

**Don't panic. Everything you need is in this one document, in plain English.** Impound Manager keeps
running on its own — nobody has to "operate the computer" day to day. Your job is small: keep a short
list of things from lapsing (Section 2), and know where to get help if something breaks (Section 7).
That's it. You do **not** need to learn to code, and you will **not** get blindsided by something
quietly expiring — this guide shows you exactly how to keep track. **You've got this.**

**What it is:** the software that runs the impound lot. Staff log into a website and use it to track
every towed car, send the legally-required certified letters, track their delivery through UPS, handle
titles and auctions, and log releases. It replaced the old paper process. It lives here:

> **https://impound-manager.onrender.com**

---

## 2. Keep it alive — the simple routine (this is the important part)

Digital things "disappear" for **one main reason: a bill quietly fails** (an expired credit card, a
lapsed payment) and, after a few warning emails nobody read, the service shuts off. That's almost
certainly what happened with the domain before. **Here's how that never happens to Impound Manager:**

**Once a month — 5 minutes, put it on your calendar:**
1. Open **https://impound-manager.onrender.com** and log in. Does it load? ✅ Good.
2. Check your email for anything from **Render**, **Anthropic**, or **UPS** that says *"payment failed"*
   or *"action required."* If you see one → go update the credit card on that account. That's the
   whole emergency.

**Whenever a credit card expires or is replaced:** update it on **Render**, **Anthropic**, and **UPS**
(the three paid accounts in Section 3). A dead card is the #1 way these things silently stop.

**Set ONE recurring calendar reminder now:** *"Impound Manager — is it loading? Any payment-failed
emails?"* — monthly. That single reminder **is** your whole tracking system. That's how you stay on top
of it without being technical.

> If you keep valid cards on those three accounts, this system does not disappear. Period.

---

## 3. Where it lives — the accounts behind it

Like the utilities behind a building. You don't touch them daily, but they must stay on. **Passwords
and secret keys are NOT written in this document** — they live safely inside Render. This is just the
map.

| Account | What it does | Website | Login |
|---|---|---|---|
| **Render** | Hosts the app AND the database. The "building" everything lives in. Most important account. | render.com | _[FILL IN: account email]_ |
| **GitHub** | Stores the code (`timjr4301/impound-manager`). Changes there update the live site automatically. | github.com | _[FILL IN]_ |
| **UPS** | Prints certified-mail labels and tracks delivery. Account **#81Y7X1**. | ups.com | _[FILL IN]_ |
| **Anthropic (Claude)** | Powers the smart features — reading VINs from photos, damage photos, scanning returned envelopes. | console.anthropic.com | _[FILL IN]_ |
| **Towbook** | Your dispatch system (you already use it). Impound Manager pulls the daily tow list from a Towbook export. | towbook.com | _[you already have this]_ |

Inside **Render** there are two pieces: **impound-manager** (the app, Standard plan) and
**impound-manager-db** (the database where every record is stored, Basic-4gb plan, Oregon).

---

## 4. What it costs each month

Keep a valid card on each of these and it stays alive.

| Bill | Roughly | Notes |
|---|---|---|
| Render — app (Standard) | ~$25/mo | _[CONFIRM exact on Render → Billing]_ |
| Render — database (Basic-4gb) | ~$_[CONFIRM]_/mo | _[CONFIRM exact on Render → Billing]_ |
| Anthropic (Claude AI) | Varies with use | Usage-based; check console.anthropic.com. Modest for this volume. |
| UPS | Postage per letter | Your normal certified-mail cost, not a software fee. Billed to UPS #81Y7X1. |
| GitHub | $0 | Free. |
| Web address | $0 | Free `onrender.com` address — nothing to renew (see Section 5). |

> **Exact total anytime:** Render → left sidebar → **Billing**.

---

## 5. Can anything expire or get bought out from under us? (the domain question)

**Short answer: no — not the way it happened before.** Right now the app lives at a **free
`onrender.com` web address.** There is **no domain name** on this that can expire, lapse, or be bought
by someone else. The thing that spooked us before literally cannot happen to this system as it stands.

The only "disappear" risk here is the billing one from Section 2 — a failed payment — and the monthly
5-minute check catches that.

**If you ever decide to add a custom web address** (like `impound.bjtowing.com`), *then* you'd have one
domain that renews once a year. If you do that: **turn on auto-renew** and put the renewal date on the
calendar. Until then, there's nothing to track.

---

## 6. How staff use it day to day

- **Website:** https://impound-manager.onrender.com — everyone logs in here.
- **Built-in instructions:** the app has printable step-by-step guides inside it (Guides menu):
  the Letter Workflow guide, Heather's guide, Tina's guide.
- **New staff logins:** created by an admin (you) under **Admin / Users** inside the app.

---

## 7. If something looks broken — what to do (no tech needed)

Go in order. You'll almost never get past step 2.

1. **Is it a person problem?** 9 times out of 10 it's "someone doesn't know which button." Check the
   built-in guides, or ask another staff member.
2. **Undo the app (one click).** Render → **impound-manager** → **Events** → find the last version that
   worked → click **Rollback**. The site snaps back to that working version in under a minute.
3. **Restore the data.** Render → **impound-manager-db** → **Recovery** → restore to any point in the
   last 7 days. (Backups are automatic.)
4. **Worst case — the business never stops.** If the app is down entirely, staff fall back to the OLD
   way: run tows through **Towbook**, make certified-mail labels directly on **ups.com**, keep paper
   notes. That's how it worked before. You are never stuck.

---

## 8. If it needs an actual fix or change (rare)

You don't do this yourself, and you rarely need to. When you do:

- **The magic sentence for any developer:** *"It's a **Python / Flask** web app on **Render**, with a
  **PostgreSQL** database, and the code is on **GitHub**."* Any competent web developer understands
  that — it's standard, common technology, not exotic.
- **The full technical manual already exists.** In the code is a file called **`MASTER_CONTEXT.md`** —
  it explains how every part works. Hand that one file to any developer and they're up to speed fast.
- **Tim built it with **Claude Code** (an AI coding assistant).** Many changes can be made the same way,
  without hiring anyone.
- **Render and UPS both have support.** You're not alone.

---

## 9. The "if Tim is gone tomorrow" checklist

1. **Keep the bills paid** — valid card on Render, Anthropic, UPS. Do the monthly 5-minute check
   (Section 2). This is 90% of everything.
2. **Make sure you have your own logins** to Render, GitHub, UPS, Anthropic so you're never locked out.
   _[TO DO: Tim to add Jim as an owner/admin on each account.]_
3. **The app keeps running on its own** — no daily tech babysitting.
4. **For any fix or change:** hire a Python/Flask/Render developer (Section 8) or use Claude Code, and
   hand them `MASTER_CONTEXT.md`.
5. **If all else fails**, the old Towbook + ups.com process still works. The doors stay open.

---

## 10. Blanks for Tim to fill in

- [ ] Account login emails for Render, GitHub, UPS, Anthropic (Section 3)
- [ ] Exact monthly costs from Render → Billing (Section 4)
- [ ] Add Jim as an owner/admin on each account (Section 9 #2)
- [ ] Jim's own Impound Manager username/password (created under Admin / Users)
- [ ] An emergency contact for tech help (a trusted developer's name/number), if you have one
- [ ] Put the monthly calendar reminder on Jim's calendar (Section 2)

> **Rule:** never write actual passwords or secret keys in this document. Those live inside Render
> (Environment settings) and each account's own login. This is the *map*, not the keys.
