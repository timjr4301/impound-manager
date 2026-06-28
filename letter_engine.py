"""
Letter urgency calculation engine.

Stores results in Vehicle.letter_urgency ('RED', 'YELLOW', 'GREEN', 'COMPLETE', 'NA')
so the dashboard can query the DB directly instead of filtering in Python.

Ohio ORC 4513.601 / 4513.61 rules applied:
  PPI: Letter 1 within 5 days, Letter 2 within 30 days of Letter 1
  POLICE: Notification within 10 days
"""
from datetime import date, timedelta

PPI_L1_DAYS    = 5
PPI_L2_DAYS    = 30
POLICE_L1_DAYS = 10
YELLOW_WARN    = 3   # flag yellow this many days BEFORE deadline


def _calc_urgency(v, today: date) -> str:
    """
    Compute urgency string for a single Vehicle object.
    Never raises — returns 'NA' if data is missing or ambiguous.
    """
    try:
        if not v.impound_date:
            return 'NA'

        if v.status not in ('ACTIVE', 'TITLE_FILED'):
            return 'NA'

        itype = (v.impound_type or 'PPI').upper().strip()
        if itype not in ('PPI', 'POLICE'):
            itype = 'PPI'

        deadline_days = PPI_L1_DAYS if itype == 'PPI' else POLICE_L1_DAYS

        # Safe relationship access — l1 / l2 may be None
        try:
            l1 = next((l for l in v.letters if l.letter_number == 1), None) if v.letters else None
            l2 = next((l for l in v.letters if l.letter_number == 2), None) if v.letters else None
        except Exception:
            l1 = l2 = None

        l1_due = v.impound_date + timedelta(days=deadline_days)
        days_held = (today - v.impound_date).days

        # ── Letter 1 not yet sent ─────────────────────────────────────
        if not l1 or not l1.sent_date:
            if days_held > deadline_days:
                return 'RED'
            elif (l1_due - today).days <= YELLOW_WARN:
                return 'YELLOW'
            return 'GREEN'

        # ── Letter 1 was sent — PPI needs Letter 2 also ───────────────
        if itype == 'PPI':
            if not l2 or not l2.sent_date:
                if l2 and l2.due_date:
                    days_to_l2 = (l2.due_date - today).days
                    if days_to_l2 < 0:
                        return 'RED'
                    elif days_to_l2 <= YELLOW_WARN:
                        return 'YELLOW'
                return 'GREEN'

        # ── All required letters sent — check title eligibility ───────
        if v.status == 'TITLE_FILED':
            return 'COMPLETE'

        try:
            if v.is_title_eligible:
                return 'YELLOW'   # eligible but not filed — nudge Tina
        except Exception:
            pass

        return 'GREEN'

    except Exception:
        return 'NA'


def recalculate_all() -> dict:
    """
    Batch-recalculate letter_urgency for every active vehicle.
    Commits to DB. Returns count dict {RED, YELLOW, GREEN, COMPLETE, NA}.
    """
    from models import db, Vehicle

    today = date.today()
    counts = {'RED': 0, 'YELLOW': 0, 'GREEN': 0, 'COMPLETE': 0, 'NA': 0}

    vehicles = Vehicle.query.filter(
        Vehicle.status.in_(['ACTIVE', 'TITLE_FILED'])
    ).all()

    chunk = 0
    for v in vehicles:
        urgency = _calc_urgency(v, today)
        v.letter_urgency = urgency
        counts[urgency] = counts.get(urgency, 0) + 1
        chunk += 1
        if chunk >= 250:
            db.session.flush()
            chunk = 0

    db.session.commit()
    return counts


def recalculate_vehicle(vehicle) -> str:
    """Recalculate and save urgency for a single Vehicle. Returns urgency string."""
    from models import db
    urgency = _calc_urgency(vehicle, date.today())
    vehicle.letter_urgency = urgency
    return urgency
