"""
Hatch — the Letters & Titles assistant (Heather + Tina dashboards).

Two jobs, both computed fresh from PostgreSQL every time — nothing is stored:
  • build_alerts(mode)       — the "Agent Alerts" list, run on every dashboard load
  • build_context(mode, msg) — the live vehicle data injected into every chat message

Everything here reads the SAME model properties the dashboards read
(letter1, title_eligible_date, letter_send_block_reason, next_action_label,
file_complete_for_tina, TitleFiling.days_waiting …) so Hatch can never
disagree with the page it sits on. If a rule changes in models.py, Hatch
follows automatically.

Modes: 'heather' (letters), 'tina' (titles), 'tim' (full oversight — both).
"""
import logging
import re
from datetime import date, timedelta

from sqlalchemy.orm import selectinload

from models import (db, Vehicle, PPI_LETTER1_DAYS, PPI_LETTER2_DAYS,
                    POLICE_LETTER1_DAYS, PPI_TITLE_FROM_LETTER1_DELIVERY,
                    POLICE_TITLE_FROM_LETTER1)
from task_engine import TASK3_DELAY_DAYS, TASK4_DELAY_DAYS

logger = logging.getLogger(__name__)

HATCH_MODEL = 'claude-sonnet-4-6'   # house rule: Sonnet for logic, never Opus here

LETTER_DUE_SOON_DAYS = 3     # Heather: "Letter 2 due soon (within 3 days)"
TITLE_APPROACHING_DAYS = 7   # Heather: heads-up that a car is about to go to Tina
TITLE_STALE_DAYS = 60        # Tina: title application out this long = stale
MAX_ALERTS = 10              # no alert spam — the rest roll into one "+N more" line
MAX_CONTEXT_ROWS = 80        # per section, keeps the prompt a sane size

MODES = ('heather', 'tina', 'tim')
OVERSIGHT_ROLES = ('tim', 'jim')   # full-oversight Hatch, incl. the main dashboard


# ── Helpers ──────────────────────────────────────────────────────────────────

def _mdy(d):
    return d.strftime('%m/%d/%Y') if d else '—'


def _days(n):
    return f'{n} day' if n == 1 else f'{n} days'


def describe(v):
    """Short, specific vehicle description used in alerts and in the prompt."""
    bits = [v.display_name]
    if v.color:
        bits.insert(0, v.color)
    tag = f'stock {v.stock_number}' if v.stock_number else f'ID {v.id}'
    if v.plate:
        tag += f', plate {v.plate}'
    return f'{" ".join(bits)} ({tag}, {v.impound_type})'


def _queue_vehicles(statuses=('ACTIVE',), heather_cutoff=False):
    """Same exclusions the dashboards apply: Possible Release (ghost) cars,
    snoozed cars and anomaly-flagged cars are out of the work queues."""
    q = (Vehicle.query
         .options(selectinload(Vehicle.letters), selectinload(Vehicle.title_filing))
         .filter(Vehicle.status.in_(list(statuses)))
         .filter(Vehicle.possible_release.isnot(True))
         .filter(Vehicle.not_snoozed_filter())
         .filter(Vehicle.not_anomaly_filter()))
    if heather_cutoff:
        from blueprints.heather import _after_cutoff
        q = q.filter(_after_cutoff())
    return q.all()


def _letter1_days(v):
    return POLICE_LETTER1_DAYS if v.impound_type == 'POLICE' else PPI_LETTER1_DAYS


def pending_letter_items(v):
    """Unsent letters for this vehicle as (label, due_date, block_reason) tuples.
    Includes a *virtual* Letter 1 when no Letter 1 row exists yet — the Towbook
    import doesn't create one (COMPLIANCE-TRUTH item 6), and a missing row must
    not hide a missed deadline."""
    items = []
    for l in v.letters:
        if l.superseded or l.sent_date is not None or l.due_date is None:
            continue
        items.append((l.label, l.due_date, v.letter_send_block_reason(l)))
    if v.letter1 is None and v.impound_type in ('PPI', 'POLICE'):
        due = v.letter_clock_start + timedelta(days=_letter1_days(v))
        block = None if v.bmv_search_complete else 'BMV Search must be completed first.'
        name = 'Notification Letter' if v.impound_type == 'POLICE' else 'Letter 1'
        items.append((f'{name} (not created yet)', due, block))
    return sorted(items, key=lambda i: i[1])


def _is_bmv_block(reason):
    return bool(reason) and 'BMV' in reason


def _missing_file_items(v):
    missing = []
    if not v.lka_document_confirmed:
        missing.append('LKA')
    if not v.title_search_confirmed:
        missing.append('title search')
    if not v.ups_delivery_confirmed:
        missing.append('UPS delivery proof')
    if not v.return_receipt_filed:
        missing.append('return receipt')
    return missing


def _finish(issues_by_vehicle, summary_endpoint, capped=True):
    """One alert per vehicle (its most urgent issue leads, the rest go in the
    detail line), most urgent first, capped at MAX_ALERTS unless capped=False."""
    alerts = []
    for v, issues in issues_by_vehicle.items():
        issues.sort(key=lambda i: -i['score'])
        top = issues[0]
        detail = [top['detail']] if top.get('detail') else []
        detail += [i['title'] for i in issues[1:]]
        alerts.append({
            'level': top['level'],
            'score': top['score'],
            'kind': top['kind'],
            'title': top['title'],
            'vehicle': describe(v),
            'detail': ' · '.join(detail),
            'vehicle_id': v.id,
        })
    alerts.sort(key=lambda a: (-a['score'], a['vehicle_id']))
    return _cap(alerts, summary_endpoint) if capped else alerts


def _cap(alerts, summary_endpoint):
    extra = len(alerts) - MAX_ALERTS
    alerts = alerts[:MAX_ALERTS]
    if extra > 0:
        alerts.append({
            'level': 'info', 'score': 0, 'kind': 'more',
            'title': f'+{extra} more need attention',
            'vehicle': '', 'detail': 'Lower priority than the ones above.',
            'vehicle_id': None, 'endpoint': summary_endpoint,
        })
    return alerts


def _add(bucket, v, level, score, kind, title, detail=''):
    bucket.setdefault(v, []).append(
        dict(level=level, score=score, kind=kind, title=title, detail=detail))


# ── Alerts ───────────────────────────────────────────────────────────────────

def heather_alerts(today=None, vehicles=None, capped=True):
    today = today or date.today()
    if vehicles is None:
        vehicles = _queue_vehicles(heather_cutoff=True)
    bucket = {}
    for v in vehicles:
        if v.task_no_record and not v.task_no_record_resolved:
            _add(bucket, v, 'critical', 950, 'no_record',
                 'No Record Found — Tim must resolve before letters can go out')

        for label, due, block in pending_letter_items(v):
            delta = (due - today).days
            if delta < 0:
                late = -delta
                if _is_bmv_block(block):
                    _add(bucket, v, 'critical', 1000 + late, 'bmv_blocker',
                         f'{label} overdue {_days(late)} — BMV search is blocking it',
                         f'Was due {_mdy(due)}. Finish the BMV search to unlock the letter.')
                else:
                    _add(bucket, v, 'critical', 1000 + late, 'overdue',
                         f'{label} overdue {_days(late)}',
                         f'Was due {_mdy(due)}.' + (f' {block}' if block else ''))
            elif delta <= LETTER_DUE_SOON_DAYS:
                when = 'TODAY' if delta == 0 else f'in {_days(delta)}'
                if _is_bmv_block(block):
                    _add(bucket, v, 'warning', 700 - delta, 'bmv_blocker',
                         f'BMV search is blocking {label} — due {when}',
                         f'Due {_mdy(due)}. Run the BMV search first.')
                else:
                    _add(bucket, v, 'warning', 600 - delta, 'due_soon',
                         f'{label} due {when}',
                         f'Due {_mdy(due)}.' + (f' {block}' if block else ''))

        elig = v.title_eligible_date
        if elig and v.title_filing is None:
            left = (elig - today).days
            if 0 <= left <= TITLE_APPROACHING_DAYS:
                when = 'TODAY' if left == 0 else f'in {_days(left)}'
                missing = _missing_file_items(v)
                _add(bucket, v, 'warning' if missing else 'info', 300 - left, 'title_soon',
                     f'Title eligible {when} ({_mdy(elig)})',
                     ('File not ready for Tina — missing ' + ', '.join(missing) + '.')
                     if missing else 'File is complete for Tina.')
    return _finish(bucket, 'heather.today_view', capped)


def tina_alerts(today=None, vehicles=None, capped=True):
    today = today or date.today()
    if vehicles is None:
        vehicles = _queue_vehicles(statuses=('ACTIVE', 'TITLE_FILED'))
    bucket = {}
    for v in vehicles:
        filing = v.title_filing
        elig = v.title_eligible_date
        if elig and filing is None and v.status == 'ACTIVE':
            ago = (today - elig).days
            if ago == 0:
                _add(bucket, v, 'critical', 900, 'eligible_today',
                     'Title eligible TODAY — ready to file', f'Eligible {_mdy(elig)}.')
            elif ago > 0:
                _add(bucket, v, 'warning', 500 + min(ago, 300), 'eligible_unfiled',
                     f'Eligible {_days(ago)} ago — not filed yet', f'Eligible since {_mdy(elig)}.')
            if ago >= 0 and v.impound_type == 'POLICE' and not v.affidavit_filed_date:
                _add(bucket, v, 'critical', 920 + min(ago, 50), 'affidavit',
                     'Police affidavit missing on a title-eligible car',
                     f'Eligible since {_mdy(elig)} — record the affidavit before filing.')
            if v.is_afo and ago >= -TITLE_APPROACHING_DAYS:
                _add(bucket, v, 'warning', 450, 'afo',
                     'AFO (Accident for Owner) — special handling before filing',
                     f'Title eligible {_mdy(elig)}.')
        if filing is not None:
            if filing.is_rejected:
                why = f' Reason: {filing.rejection_reason}' if filing.rejection_reason else ''
                _add(bucket, v, 'critical', 880, 'rejected',
                     'Title application REJECTED — needs fixing and re-filing',
                     f'Rejected {_mdy(filing.rejected_date)}.{why}')
            elif filing.days_waiting is not None and filing.days_waiting >= TITLE_STALE_DAYS:
                _add(bucket, v, 'warning', 400 + min(filing.days_waiting, 300), 'stale_filing',
                     f'Title application out {_days(filing.days_waiting)} — chase the title office',
                     f'Filed {_mdy(filing.filed_date)}'
                     + (f' at {filing.title_office}' if filing.title_office else '') + '.')
    return _finish(bucket, 'tina.title_eligibility', capped)


def oversight_alerts(today=None):
    """Letters + titles together for the main (Tim/Jim) dashboard. A car with
    both a letter and a title problem shows once, under its more urgent one."""
    best = {}
    for a in heather_alerts(today, capped=False) + tina_alerts(today, capped=False):
        cur = best.get(a['vehicle_id'])
        if cur is None or a['score'] > cur['score']:
            best[a['vehicle_id']] = a
    merged = sorted(best.values(), key=lambda a: (-a['score'], a['vehicle_id']))
    return _cap(merged, 'heather.today_view')


def build_alerts(dashboard, today=None):
    """Alerts for a dashboard: 'heather', 'tina', or 'main' (Tim/Jim oversight).
    On Heather's/Tina's pages Tim sees that page's list — his extra reach there
    is in the chat context."""
    if dashboard == 'main':
        return oversight_alerts(today)
    if dashboard == 'tina':
        return tina_alerts(today)
    return heather_alerts(today)


# ── Chat context ─────────────────────────────────────────────────────────────

def _letter_line(v):
    parts = []
    for l in v.letters:
        if l.superseded:
            continue
        if l.sent_date:
            s = f'{l.label} sent {_mdy(l.sent_date)}'
            if l.delivery_confirmed_date:
                s += f', delivered {_mdy(l.delivery_confirmed_date)}'
            elif l.return_to_sender:
                s += ', RETURNED TO SENDER'
            else:
                s += ', delivery not confirmed'
            parts.append(s)
    return '; '.join(parts)


def _heather_rows(today):
    rows = []
    for v in _queue_vehicles(heather_cutoff=True):
        pending = pending_letter_items(v)
        if not pending and v.bmv_search_complete:
            continue
        soonest = pending[0][1] if pending else date.max
        bits = [describe(v),
                f'impounded {_mdy(v.impound_date)} ({_days(v.days_in_storage)} on lot)',
                'BMV search COMPLETE' if v.bmv_search_complete
                else f'BMV search NOT done (stage {v.bmv_stage or "PENDING"})']
        if v.letter_round > 1:
            bits.append(f'letter round {v.letter_round} (restarted after return-to-sender)')
        for label, due, block in pending:
            d = (due - today).days
            state = (f'OVERDUE {_days(-d)}' if d < 0 else
                     'due TODAY' if d == 0 else f'due in {_days(d)}')
            s = f'{label} NOT sent, due {_mdy(due)} ({state})'
            if block:
                s += f' [blocked: {block}]'
            bits.append(s)
        sent = _letter_line(v)
        if sent:
            bits.append(sent)
        if v.task_no_record and not v.task_no_record_resolved:
            bits.append('NO RECORD FOUND (Task 5, Tim must resolve)')
        if v.vin_check_blocked:
            bits.append('VIN MISMATCH — letters hard-blocked')
        if v.is_afo:
            bits.append('AFO flagged')
        rows.append((soonest, ' | '.join(bits)))
    rows.sort(key=lambda r: r[0])
    return [r[1] for r in rows]


def _tina_rows(today):
    rows = []
    horizon = today + timedelta(days=30)
    for v in _queue_vehicles(statuses=('ACTIVE', 'TITLE_FILED')):
        elig = v.title_eligible_date
        filing = v.title_filing
        relevant = (filing is not None and not filing.is_complete) or \
                   (elig is not None and elig <= horizon) or v.is_afo
        if not relevant:
            continue
        bits = [describe(v), f'status {v.status}']
        if elig:
            d = (elig - today).days
            bits.append(f'title eligible {_mdy(elig)} '
                        + (f'(eligible {_days(-d)} ago)' if d < 0 else
                           '(TODAY)' if d == 0 else f'(in {_days(d)})'))
        else:
            bits.append(f'no eligibility date yet — {v.title_blocked_reason or "waiting"}')
        if filing is not None:
            if filing.is_rejected:
                bits.append(f'filing REJECTED {_mdy(filing.rejected_date)}'
                            + (f': {filing.rejection_reason}' if filing.rejection_reason else ''))
            elif filing.is_awaiting:
                bits.append(f'filed {_mdy(filing.filed_date)}, waiting {_days(filing.days_waiting or 0)}'
                            + (f' at {filing.title_office}' if filing.title_office else ''))
        else:
            bits.append('not filed')
        if v.impound_type == 'POLICE':
            bits.append(f'affidavit filed {_mdy(v.affidavit_filed_date)}'
                        if v.affidavit_filed_date else 'police affidavit NOT filed')
        if v.is_afo:
            bits.append('AFO (Accident for Owner) flagged')
        if v.tina_stage:
            bits.append(f'pipeline stage {v.stage_label}')
        if v.disposition:
            bits.append(f'disposition {v.disposition}')
        rows.append((elig or date.max, ' | '.join(bits)))
    rows.sort(key=lambda r: r[0])
    return [r[1] for r in rows]


def _capped(title, rows):
    out = [f'{title} ({len(rows)}):']
    out += [f'- {r}' for r in rows[:MAX_CONTEXT_ROWS]]
    if len(rows) > MAX_CONTEXT_ROWS:
        out.append(f'- …and {len(rows) - MAX_CONTEXT_ROWS} more not shown (lowest urgency).')
    if not rows:
        out.append('- none')
    return '\n'.join(out)


def _alert_lines(alerts):
    if not alerts:
        return 'No urgent alerts — pipeline looks good.'
    return '\n'.join(f'- [{a["level"].upper()}] {a["vehicle"]}: {a["title"]}'
                     + (f' — {a["detail"]}' if a['detail'] else '') for a in alerts)


def mentioned_vehicles(text, limit=5):
    """Vehicles named in the message by stock #, plate or VIN — looked up
    regardless of queue filters so Hatch can answer about any car."""
    tokens = {t.upper() for t in re.findall(r'[A-Za-z0-9-]{4,20}', text or '')}
    if not tokens:
        return []
    return (Vehicle.query
            .options(selectinload(Vehicle.letters), selectinload(Vehicle.title_filing))
            .filter(db.or_(db.func.upper(Vehicle.stock_number).in_(tokens),
                           db.func.upper(Vehicle.plate).in_(tokens),
                           db.func.upper(Vehicle.vin).in_(tokens)))
            .limit(limit).all())


def _vehicle_brief(v, today):
    bits = [describe(v), f'status {v.status}',
            f'impounded {_mdy(v.impound_date)} ({_days(v.days_in_storage)} on lot)',
            'BMV search complete' if v.bmv_search_complete else 'BMV search NOT done']
    if v.possible_release:
        bits.append('FLAGGED POSSIBLE RELEASE — no letters until verified')
    if v.is_snoozed:
        bits.append(f'snoozed ({v.snooze_days_remaining} days left)')
    if v.is_anomaly:
        bits.append('anomaly-flagged (hidden from queues until Tim/Jim clear it)')
    for label, due, block in pending_letter_items(v):
        bits.append(f'{label} NOT sent, due {_mdy(due)}' + (f' [blocked: {block}]' if block else ''))
    sent = _letter_line(v)
    if sent:
        bits.append(sent)
    elig = v.title_eligible_date
    bits.append(f'title eligible {_mdy(elig)}' if elig
                else f'no title date yet — {v.title_blocked_reason or "n/a"}')
    if v.title_filing is not None:
        bits.append(f'title filing status {v.title_filing.status}')
    if v.impound_type == 'POLICE':
        bits.append(f'affidavit {_mdy(v.affidavit_filed_date)}' if v.affidavit_filed_date
                    else 'affidavit not filed')
    if v.is_afo:
        bits.append('AFO flagged')
    nxt = v.next_action_label
    if nxt:
        bits.append(f'next action: {nxt}')
    return ' | '.join(bits)


def build_context(mode, message, today=None):
    """Live data block for the system prompt. Rebuilt on EVERY message."""
    today = today or date.today()
    sections = [f'Today is {today.strftime("%A")} {_mdy(today)}.']
    if mode in ('heather', 'tim'):
        sections.append('ALERTS (letters):\n' + _alert_lines(heather_alerts(today)))
        sections.append(_capped('LETTER PIPELINE — vehicles with letter or BMV work open, '
                                'most urgent first (impounded on/after the queue cutoff)',
                                _heather_rows(today)))
    if mode in ('tina', 'tim'):
        sections.append('ALERTS (titles):\n' + _alert_lines(tina_alerts(today)))
        sections.append(_capped('TITLE PIPELINE — eligible, eligible within 30 days, '
                                'filed-but-not-received, or AFO', _tina_rows(today)))
    if mode == 'tim':
        active = Vehicle.query.filter_by(status='ACTIVE').count()
        ghosts = Vehicle.query.filter_by(status='ACTIVE').filter(Vehicle.possible_release == True).count()
        anomalies = Vehicle.query.filter_by(status='ACTIVE').filter(Vehicle.is_anomaly == True).count()
        snoozed = (Vehicle.query.filter_by(status='ACTIVE')
                   .filter(Vehicle.snoozed_until.isnot(None))
                   .filter(Vehicle.snoozed_until >= today).count())
        sections.append(f'LOT TOTALS: {active} ACTIVE; {ghosts} flagged Possible Release; '
                        f'{anomalies} anomaly-flagged; {snoozed} snoozed (those three groups are '
                        'excluded from the queues above).')
    named = mentioned_vehicles(message)
    if named:
        sections.append('VEHICLES NAMED IN THIS MESSAGE:\n'
                        + '\n'.join(f'- {_vehicle_brief(v, today)}' for v in named))
    return '\n\n'.join(sections)


# ── System prompts ───────────────────────────────────────────────────────────

_PERSONA = {
    'heather': (
        'You are Hatch, the letters and compliance assistant for Broad & James Towing. '
        'You know every vehicle on the lot and the full Ohio ORC 4513.61 impound pipeline. '
        'Heather handles letter generation and mailing. Help her know exactly what needs '
        'to happen next, in what order, and why. Be specific — use vehicle descriptions, '
        'dates, and days remaining. Never be vague. If a letter is overdue tell her how '
        'overdue. If a BMV lookup is blocking a letter tell her exactly which vehicle.'
    ),
    'tina': (
        'You are Hatch, the title pipeline assistant for Broad & James Towing. '
        'You know every vehicle on the lot and the full Ohio title filing requirements. '
        'Tina handles title eligibility, filing, junking, and auction decisions. '
        'Help her know exactly which vehicles are ready to file, which need affidavits, '
        'and which have special handling (AFO, police vs PPI differences). '
        'Be specific — use vehicle descriptions, eligibility dates, and filing deadlines.'
    ),
    'tim': (
        'You are Hatch, the letters and titles oversight assistant for Broad & James Towing. '
        'You are talking to {name} (owner side), who sees everything: Heather\'s letter '
        'pipeline AND Tina\'s title pipeline. Give them the honest picture — what is late, '
        'what is blocked, who owns the next step (Heather = letters/BMV, Tina = titles), '
        'and where compliance risk is building. Be specific — vehicle descriptions, '
        'dates, days overdue, counts. Never be vague.'
    ),
}


def _rules():
    return f"""HOW THE PIPELINE WORKS (these are the rules the app itself enforces):

5-task letter pipeline:
- Task 1 BMV Search — opens day 1. Letter 1 CANNOT be sent until the BMV search is complete (server-enforced).
- Task 2 1st Notice (Letter 1) — due {PPI_LETTER1_DAYS} days (PPI) / {POLICE_LETTER1_DAYS} days (POLICE) from the letter-clock start. The clock starts at the impound date, or at the restart date if the letter process was restarted after a return-to-sender. It is LATE (red) past that date even while the BMV search is still blocking it.
- Task 3 2nd Notice (Letter 2, PPI only) — unlocks {TASK3_DELAY_DAYS} days after Letter 1 was SENT (sent date, not delivery).
- Task 4 Ready to File — internal handoff to Tina, {TASK4_DELAY_DAYS} days after Letter 2 is sent. The title eligibility date below is the legal clock.
- Task 5 No Record Found — urgent flag; only Tim can resolve it.

PPI (private property impound, ORC 4513.601 / 4505.101):
- Letter 1 within {PPI_LETTER1_DAYS} days; Letter 2 {PPI_LETTER2_DAYS} days after Letter 1 is sent.
- Title eligible {PPI_TITLE_FROM_LETTER1_DELIVERY} days after Letter 1 is DELIVERED or confirmed UNDELIVERABLE (returned to sender) — not from the impound date and not from Letter 2. No eligibility date exists until that delivery/return is recorded.

POLICE (ORC 4513.61):
- ONE notice (Notification / Notice of Lien) within {POLICE_LETTER1_DAYS} days. No second notice for police cars.
- Title eligible {POLICE_TITLE_FROM_LETTER1} days after that notice is SENT.
- Police titles go through the affidavit process — Tina records the police affidavit before filing.

Lienholders and extra parties: a lienholder found by the BMV search gets its own notice. Extra-party letters run their own sequence and never move the registered owner's title clock.

AFO = "Accident for Owner" — a flag set from the envelope's Reference #2 (by Heather or the envelope scanner). It never blocks letters, but flag it to Tina so she handles it specially at title time.

Hard stops: a car flagged Possible Release gets NO letters until someone verifies it is still on the lot. A VIN photo mismatch hard-blocks letters until resolved.

Title filing is two steps: SUBMITTED (application at the title office) → TITLE_RECEIVED. A REJECTED application must be fixed and re-filed.

Ground rules for you:
- Answer ONLY from the live data below. If a vehicle isn't in it, say so and suggest the vehicle's page or the right dashboard. Never invent a vehicle, date or count.
- You give guidance; you can't click buttons or change records. Point to the vehicle's page in Impound Manager for the action.
- The PPI {PPI_TITLE_FROM_LETTER1_DELIVERY}-day and POLICE {POLICE_TITLE_FROM_LETTER1}-day title rules are B&J's working rules (locked 07/31/2026, still being confirmed with counsel). Don't present anything as legal advice; for legal questions beyond these rules, say to check with counsel.
- Lead with the most urgent item. Dates as MM/DD/YYYY.
- Format for a small chat box: plain sentences and short '-' lists; **bold** is fine. No # headings, tables, horizontal rules or emoji."""


def system_prompt(mode, message, today=None, name='Tim'):
    return '\n\n'.join([_PERSONA[mode].replace('{name}', name), _rules(),
                        'LIVE DATA (pulled from the database just now):\n'
                        + build_context(mode, message, today)])


def resolve_mode(user, dashboard):
    """Tim, Wally (role tim) and Jim always get full oversight."""
    if user.role in OVERSIGHT_ROLES:
        return 'tim'
    return 'tina' if dashboard == 'tina' else 'heather'


def dashboard_panel(user, dashboard):
    """Template variables for the Agent Alerts + Hatch chat panel. Fresh on
    every dashboard load; a failure here must never take the dashboard down
    with it (alerts=None renders as "alerts unavailable")."""
    try:
        alerts = build_alerts(dashboard)
    except Exception as exc:
        db.session.rollback()
        logger.warning('Hatch alerts failed: %s', exc)
        alerts = None
    return dict(hatch_dashboard=dashboard,
                hatch_mode=resolve_mode(user, dashboard),
                hatch_alerts=alerts)
