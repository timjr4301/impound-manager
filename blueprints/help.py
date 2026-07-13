"""
Help system — context-sensitive ? modal content and printable quick-start guides.
"""
from flask import Blueprint, render_template, request, Response
from flask_login import login_required, current_user

bp = Blueprint('help', __name__, url_prefix='/help')

# Role-specific help content
_HELP = {
    'heather': {
        'title': "Heather's Quick Reference",
        'color': '#0d6efd',
        'sections': [
            {
                'heading': 'Daily Morning Routine',
                'items': [
                    'Log in → My Dashboard shows red/yellow/green stoplight for every active vehicle.',
                    'RED = letter is overdue. Handle these first.',
                    'YELLOW = letter due in 1–3 days. Get them ready.',
                    'GREEN = on track. No action today.',
                ],
            },
            {
                'heading': 'Sending a Letter',
                'items': [
                    '1. Click the vehicle name on the dashboard.',
                    '2. Scroll to "Letter 1" (or Letter 2). Click "Print Letter" to get the certified mail form.',
                    '3. Take it to the post office with the certified mail slip.',
                    '4. Back here — click "Mark as Sent" and enter the tracking number from the receipt.',
                    'PPI vehicles need 2 letters. Police holds need 1.',
                ],
            },
            {
                'heading': 'Scanning a Returned Envelope',
                'items': [
                    '1. Click Letters in the top nav.',
                    '2. Click "Scan Envelope" (green button, top right).',
                    '3. Hold the envelope in front of the IPEVO camera and click Capture.',
                    'Claude reads it automatically: Delivered → marked confirmed. Returned → flagged red.',
                    'You can also click the camera icon next to any letter in the "Awaiting Delivery" tab.',
                ],
            },
            {
                'heading': 'BMV Search Queue',
                'items': [
                    'Vehicles waiting for owner info show under "BMV Search Queue" on your dashboard.',
                    'Look up the owner and lienholder in the Ohio BMV system.',
                    'Enter the results on the vehicle page under Owner / Lienholder.',
                    'Click "BMV Complete" when done — vehicle moves to Tina\'s queue automatically.',
                    'If BMV shows "No Record Found" click "Mark No Record" — this alerts Tim immediately.',
                ],
            },
            {
                'heading': 'UPS Notices (Wally)',
                'items': [
                    'Click Notices in the top nav to find a vehicle.',
                    'Fill in the recipient address and click Send — Wally generates the UPS label.',
                    'Print the label and attach to the envelope.',
                    'Tim and Lawrence get a Wally alert automatically when a notice goes out.',
                ],
            },
            {
                'heading': 'If Something Looks Wrong',
                'items': [
                    'Add a note to the vehicle record (Notes section at the bottom of the vehicle page).',
                    'Text or call Tim directly for anything urgent.',
                    'The system is backed up daily — no data is ever lost.',
                ],
            },
        ],
    },

    'tina': {
        'title': "Tina's Quick Reference",
        'color': '#198754',
        'sections': [
            {
                'heading': 'Your Dashboard at a Glance',
                'items': [
                    '"FROM HEATHER" = vehicles Heather finished BMV search on. Start title work on these.',
                    '"IN PROGRESS" = your active title cases.',
                    '"READY TO FILE" = vehicles legally eligible for title. File these at BMV.',
                    '"NEED DECISION" = cars that need Sell / Junk / Hold decision.',
                    '"DRIVER DAMAGE REPORTS" = new damage reports from drivers (bottom of dashboard).',
                ],
            },
            {
                'heading': 'Reviewing a Damage Report',
                'items': [
                    'All driver damage reports appear at the bottom of your dashboard.',
                    'Click "View" to see photos, dots on the car diagram, driver signature, and Claude\'s AI analysis.',
                    'AI analysis shows severity (Minor / Moderate / Severe / Total Loss) and estimated repair cost.',
                    'Click the green checkmark ✓ to mark a report reviewed — it moves off the dashboard.',
                    'Dispute reports are highlighted in red — these need immediate attention.',
                ],
            },
            {
                'heading': 'Moving a Vehicle Through Your Pipeline',
                'items': [
                    '1. Click "Move" on any vehicle in the "From Heather" section.',
                    '2. Choose: Title Work / Court Process / Police Affidavit / Ready / Complete.',
                    '3. Add notes about what you did.',
                    'Tip: Use "Court Process" stage when you need a hearing date.',
                ],
            },
            {
                'heading': 'Disposition Pipeline (title → auction or junk)',
                'items': [
                    'Once you file the title, the vehicle lands on the Disposition Pipeline board at the "Title Filed" stage.',
                    'Set the disposition (Sell or Junk) and drag the card down its track: Auction Prep → Auction Ready → At Auction → Sold, or Junk Prep → Junked. Use Hold to park a car for court/lien.',
                    'To finish a car, use the "record outcome" link (or the Invoice button) — it captures the sale/scrap details (auctioneer, lot, price, or yard/weight) and marks it Sold or Junked.',
                    'This replaces the old external Base44 tracker — everything now lives in-app. The Disposition Report shows where every post-title vehicle stands and total proceeds.',
                ],
            },
            {
                'heading': 'Creating an Invoice',
                'items': [
                    'Click "Invoice" next to any vehicle.',
                    'Choose SALE (auction/private) or JUNK (scrap yard).',
                    'Fill in the buyer/yard and amounts. Net proceeds are calculated automatically.',
                    'Print the invoice from the print button — it has all the legal details.',
                ],
            },
            {
                'heading': 'NADA Value & Fallback Badge',
                'items': [
                    'A yellow "please verify" badge means the system couldn\'t get a real value and is using the $3,499 fallback.',
                    'On the vehicle detail page, type the correct value into the "Manual value" box under NADA Wholesale Value and click Save — this overrides the fallback everywhere (reports, invoices, title packets) until you clear it.',
                    'Clear the box and click Save to go back to the looked-up/fallback value.',
                ],
            },
        ],
    },

    'lawrence': {
        'title': "Lawrence's Quick Reference",
        'color': '#6f42c1',
        'sections': [
            {
                'heading': 'Invoice Camera',
                'items': [
                    'Click "Invoice Cam" in the top nav.',
                    'Click the big camera button and hold up an invoice or check.',
                    'Claude reads it automatically — review the extracted info.',
                    'Search for the vehicle by name, plate, or stock number.',
                    'Confirm the payment — it logs against the vehicle record instantly.',
                ],
            },
            {
                'heading': 'Vehicle Lookup',
                'items': [
                    'Use the search bar (top of every page) to find any vehicle by plate, VIN, or owner name.',
                    'The Vehicles list shows all active impounds — filter by status if needed.',
                    'Every vehicle shows: days in storage, balance due, letters sent, current status.',
                ],
            },
            {
                'heading': 'Accepting Payments',
                'items': [
                    'From a vehicle\'s detail page, click "Collect Payment".',
                    'Or use Invoice Camera for walk-in invoice payments.',
                    'Cash, check, and card payments all log with timestamp and your name.',
                ],
            },
            {
                'heading': 'If You Have Questions',
                'items': [
                    'Text Tim or check with Heather for anything about a specific vehicle.',
                    'The system logs every action — nothing is lost.',
                ],
            },
        ],
    },

    'lori': {
        'title': "Lori's Quick Reference",
        'color': '#6f42c1',
        'sections': [
            {
                'heading': 'Invoice Camera',
                'items': [
                    'Click "Invoice Cam" in the top nav.',
                    'Click the big camera button and hold up an invoice or check.',
                    'Claude reads it automatically — review the extracted info.',
                    'Search for the vehicle by name, plate, or stock number.',
                    'Confirm the payment — it logs against the vehicle record instantly.',
                ],
            },
            {
                'heading': 'Vehicle Lookup',
                'items': [
                    'Use the search bar (top of every page) to find any vehicle by plate, VIN, or owner name.',
                    'The Vehicles list shows all active impounds — filter by status if needed.',
                    'Every vehicle shows: days in storage, balance due, letters sent, current status.',
                ],
            },
            {
                'heading': 'Accepting Payments',
                'items': [
                    'From a vehicle\'s detail page, click "Collect Payment".',
                    'Or use Invoice Camera for walk-in invoice payments.',
                    'Cash, check, and card payments all log with timestamp and your name.',
                ],
            },
            {
                'heading': 'If You Have Questions',
                'items': [
                    'Text Tim or check with Heather for anything about a specific vehicle.',
                    'The system logs every action — nothing is lost.',
                ],
            },
        ],
    },

    'jim': {
        'title': "Jim's Quick Reference",
        'color': '#842029',
        'sections': [
            {
                'heading': 'Owner Dashboard (Hub)',
                'items': [
                    'Go to /hub to see the unified launch pad — links to all B&J systems in one place.',
                    'Impound Manager → this app (full fleet view).',
                    'TowCommand → dispatch and truck management.',
                    'BJ Books → accounting and payroll.',
                    'Disposition Pipeline → in-app title and auction/junk pipeline (replaces Tina\'s old Base44 tracker).',
                ],
            },
            {
                'heading': 'What You Can See Here',
                'items': [
                    'Overview dashboard shows total active vehicles, overdue letters, and title-eligible queue.',
                    'Vehicles list → filter by Active, Released, Title Filed.',
                    'Heather\'s dashboard → letter pipeline stoplight for every vehicle.',
                    'Tina\'s dashboard → title work and invoice history.',
                    'Drivers → payroll, timecards, SMS feedback.',
                ],
            },
            {
                'heading': 'Payments & Invoices',
                'items': [
                    'Use Invoice Camera (top nav) to scan any incoming check or invoice.',
                    'Payment history is logged per vehicle with timestamp and who processed it.',
                ],
            },
            {
                'heading': 'Override Actions',
                'items': [
                    'As owner, you can access all sections and mark any item complete.',
                    'Your actions are logged with a purple "owner" badge for the team to see.',
                ],
            },
        ],
    },

    'tim': {
        'title': "Tim's Quick Reference",
        'color': '#0dcaf0',
        'sections': [
            {
                'heading': 'Dashboard Overview',
                'items': [
                    'Overdue letters → click to go directly to each vehicle.',
                    'Urgent: "No Record Found" vehicles need your review and resolution.',
                    'Title-eligible queue shows cars ready to file right now.',
                    'Heather → Tina handoff queue shows what Tina is waiting on.',
                ],
            },
            {
                'heading': 'Towbook Sync',
                'items': [
                    'The system auto-syncs with Towbook every morning at 5 AM.',
                    'If the banner shows "Towbook not synced today", click API Pull to force it.',
                    'Or upload a CSV from Towbook\'s export manually using Upload CSV.',
                    '"Possible Release" flags appear when a vehicle drops off the Towbook list.',
                ],
            },
            {
                'heading': 'Resolving No Record Found',
                'items': [
                    'Heather flags these when BMV has no owner on file.',
                    'They show as URGENT red banners on the vehicle page and your dashboard.',
                    'Click "Resolve" on the vehicle, add your resolution notes.',
                    'Court process or alternative contact are the typical resolutions.',
                ],
            },
            {
                'heading': 'Admin',
                'items': [
                    'Admin → Users: manage staff passwords and roles.',
                    'All users have role-specific views — they only see what they need.',
                    'NADA lookup uses VinAudit API — set VINAUDIT_API_KEY in Render env vars.',
                    'If VinAudit isn\'t set up or returns no data, the vehicle shows a $3,499 fallback with a yellow "please verify" badge — enter the real value in the Manual value field on the vehicle detail page to clear it.',
                ],
            },
        ],
    },

    'brady': {
        'title': "Brady's Quick Reference",
        'color': '#fd7e14',
        'sections': [
            {
                'heading': 'What You Can Do',
                'items': [
                    'View all active vehicles and their status.',
                    'Use Invoice Camera to scan and log incoming payments.',
                    'Access Heather\'s and Tina\'s dashboards to see pipeline status.',
                    'Add notes to vehicle records.',
                ],
            },
            {
                'heading': 'Quick Tips',
                'items': [
                    'Search bar (top of page) finds any vehicle by plate, VIN, or owner name.',
                    'Invoice Camera: point at invoice or check, Claude reads it, confirm against vehicle.',
                    'If unsure about anything, add a note to the vehicle and text Tim.',
                ],
            },
        ],
    },

    'dispatcher': {
        'title': "Dispatch Quick Reference",
        'color': '#20c997',
        'sections': [
            {
                'heading': 'Dispatch Board',
                'items': [
                    'Your dashboard shows every active vehicle — newest impound first.',
                    'Use the All / Red / Yellow / Green buttons at the top to filter by letter urgency stoplight.',
                    'Columns: Vehicle, Plate/VIN, Impound date, Days in storage, Owner, Next Action, Balance, Keys, Location.',
                    'Days in storage turns orange after 14 days and red after 30 — a quick sign a car has been sitting a while.',
                ],
            },
            {
                'heading': 'Adding a New Impound',
                'items': [
                    'Click "New" (top right) to add a vehicle that isn\'t in Towbook yet.',
                    'Fill in what you know — plate, VIN, make/model, impound type (PPI or Police), storage location.',
                    'You can come back and fill in owner/BMV info later — Heather handles that step.',
                ],
            },
            {
                'heading': 'Finding a Vehicle',
                'items': [
                    'Use the search bar (top of every page) — searches plate, VIN, owner name, or stock number.',
                    'Click any vehicle row to open its full detail page.',
                ],
            },
            {
                'heading': 'If Something Looks Wrong',
                'items': [
                    'Add a note to the vehicle record from its detail page.',
                    'Text or call Tim directly for anything urgent.',
                ],
            },
        ],
    },

    'demo': {
        'title': "Demo Mode — What You're Looking At",
        'color': '#ffc107',
        'sections': [
            {
                'heading': 'This Is a Read-Only Preview',
                'items': [
                    'The yellow "DEMO MODE" banner means nothing you do here is saved — no edits, no letters sent, no payments logged, no imports.',
                    'Every button and form is real and clickable — you\'ll see how the workflow works, but any save/submit attempt is blocked and shows a "read-only" message.',
                    'Feel free to click around — there\'s nothing to break.',
                ],
            },
            {
                'heading': 'What You Can Explore',
                'items': [
                    'Hub — the full launch pad with tiles for every system.',
                    'Overview, Heather, and Tina dashboards — see the letter/title pipeline stoplight system.',
                    'Pipeline and Letters — how certified mail tracking and the 4-task deadline engine work.',
                    'Vehicles — the full active-impound list and a sample vehicle detail page.',
                    'Invoice Camera — try scanning a check or invoice; Claude will read it (the read works), you just can\'t confirm/save the payment.',
                ],
            },
            {
                'heading': 'Not Included in This Preview',
                'items': [
                    'Driver payroll, timecards, and HR — restricted to the owner/admin accounts, not part of this demo.',
                    'The Admin panel (user management) — same reason.',
                ],
            },
        ],
    },
}

_DEFAULT_HELP = {
    'title': 'Impound Manager Help',
    'color': '#6c757d',
    'sections': [
        {
            'heading': 'Getting Started',
            'items': [
                'Use the navigation bar at the top to access your dashboard.',
                'Search any vehicle by plate, VIN, or owner name using the search bar.',
                'Click any vehicle name to see its full detail page.',
                'Questions? Contact Tim or Heather for help.',
            ],
        },
    ],
}


@bp.route('/content')
@login_required
def modal_content():
    """AJAX endpoint: return role-specific help as HTML fragment."""
    page = request.args.get('page', '')
    role = current_user.role if current_user.is_authenticated else 'dispatcher'
    data = _HELP.get(role, _DEFAULT_HELP)
    return render_template('help/modal_content.html', data=data, page=page, role=role)


@bp.route('/guide/<role>')
@login_required
def printable_guide(role):
    """Printable quick-start guide page (browser print-to-PDF)."""
    data = _HELP.get(role, _DEFAULT_HELP)
    return render_template('help/printable_guide.html', data=data, role=role)
