# Next session starter prompt — paste this to kick off

Read MASTER_CONTEXT.md first (the "August 2, 2026 (late evening session)" entry has full detail on
everything from last time), then let's pick up where we left off. Three things open:

1. **Check in on the 159-vehicle Towbook/IM letter-status mismatch list** — I was working through it by
   hand with real Towbook evidence per vehicle. Ask me how far I got and whether any new patterns showed up
   worth turning into another automatic guard (like the transport/Goose-PVG ones from last session).

2. **Impound-slip-vs-BMV-owner feature** — this was fully spec'd but not built: for POLICE impounds, check
   the physical impound slip's Owner field against what BMV search finds, and if they're different people,
   the impound-slip owner also needs a letter (auto-fill into the existing "2nd owner" slot, which already
   triggers a letter automatically). Before building it, I still need to answer: does this replace Tina's
   current manual Towbook workaround, or does that keep happening regardless? Ask me that first.

3. **USPS / AutoDataDirect certified mail** — before building a fresh USPS API integration, I should have
   checked with AutoDataDirect (the app already has an "Import from ADD123" button) whether their certified-
   mail service is something we can just turn on with the existing account. Ask if I've done that yet.

Also worth a quick status check: did the UPS 400 error ever get retried on a real letter, and did tonight's
error-surfacing fix actually reveal what was wrong?
