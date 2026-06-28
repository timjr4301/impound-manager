"""
B&J Impound Manager — 5-Task Pipeline Engine

Task 1 — BMV Search
  Opens day 1. Pending until Heather marks complete.
  If No Record Found: triggers Task 5 URGENT.

Task 2 — 1st Notice Letter
  Opens day 5 from impound. NOT before.
  Locked until Task 1 complete.
  Due by day 5. OVERDUE if no letter sent and days_held > 5.
  For historical/Towbook vehicles: if days_held > 5 and no letter, show as Task 2 OVERDUE
  regardless of Task 1 state (backfill assumption: BMV was done offline).

Task 3 — 2nd Notice Letter
  Opens 10 days after Task 2 delivery confirmed OR return-to-sender.
  Locked until Task 2 complete.

Task 4 — Ready to File
  Opens 45 days after Task 3 complete.
  Auto-moves vehicle to Tina's queue on trigger.

Task 5 — No Record Found URGENT
  Manual flag. Bright red everywhere. Never auto-clears. Tim must resolve.
"""
from datetime import date, timedelta

TASK2_OPEN_DAYS     = 5     # Task 2 not available before this many days from impound
TASK3_DELAY_DAYS    = 10    # Task 3 opens this many days after delivery/attempt
TASK4_DELAY_DAYS    = 45    # Task 4 opens this many days after letter2 sent
YELLOW_WARN_DAYS    = 3     # Flag YELLOW this many days before a deadline


def _letter_delivery_date(l1):
    """Return the date that counts as 'delivered or attempted' for Task 3 timing."""
    if not l1:
        return None
    if l1.delivery_confirmed_date:
        return l1.delivery_confirmed_date
    if l1.return_to_sender and l1.sent_date:
        return l1.sent_date + timedelta(days=3)   # estimate: RTS arrives ~3 days after send
    return None


def compute_task(v, today: date) -> dict:
    """
    Compute the current pipeline task for a single Vehicle.

    Returns dict:
      task_num   : int  1–4  (5 = flagged via task_no_record, shown separately)
      task_label : str  human-readable label
      task_due   : date | None  when this task is due / opens
      urgency    : 'RED' | 'YELLOW' | 'GREEN' | 'NA'
      locked     : bool  task not yet unlocked
      action     : str  one-line instruction for Heather
    """
    # NA guard
    if not v.impound_date or v.status not in ('ACTIVE', 'TITLE_FILED'):
        return _na()

    try:
        letters = v.letters or []
        l1 = next((l for l in letters if l.letter_number == 1), None)
        l2 = next((l for l in letters if l.letter_number == 2), None)
    except Exception:
        l1 = l2 = None

    days_held = (today - v.impound_date).days

    # ── Derived flags ─────────────────────────────────────────────────────────
    task1_done = bool(v.heather_complete or (v.bmv_stage == 'COMPLETE'))
    letter1_sent = bool(l1 and l1.sent_date)
    letter2_sent = bool(l2 and l2.sent_date)
    delivery_date = _letter_delivery_date(l1)
    l1_due = v.impound_date + timedelta(days=TASK2_OPEN_DAYS)

    # ── TASK 4: Ready to File ─────────────────────────────────────────────────
    if letter2_sent:
        task4_open = l2.sent_date + timedelta(days=TASK4_DELAY_DAYS)
        if today >= task4_open:
            return dict(
                task_num=4,
                task_label='Ready to File',
                task_due=task4_open,
                urgency='YELLOW',
                locked=False,
                action=f'45-day hold complete — move to Tina ({(today - task4_open).days}d ago)',
            )
        days_left = (task4_open - today).days
        return dict(
            task_num=4,
            task_label='Waiting — 45-Day Hold',
            task_due=task4_open,
            urgency='GREEN',
            locked=True,
            action=f'2nd notice sent. File eligible in {days_left}d on {task4_open.strftime("%m/%d/%Y")}',
        )

    # ── TASK 3: 2nd Notice Letter ─────────────────────────────────────────────
    if letter1_sent:
        if delivery_date:
            task3_open = delivery_date + timedelta(days=TASK3_DELAY_DAYS)
            days_to_open = (task3_open - today).days
            if today >= task3_open:
                return dict(
                    task_num=3,
                    task_label='2nd Notice Letter',
                    task_due=task3_open,
                    urgency='RED',
                    locked=False,
                    action=f'Send 2nd notice letter NOW — available since {task3_open.strftime("%m/%d/%Y")}',
                )
            elif days_to_open <= YELLOW_WARN_DAYS:
                return dict(
                    task_num=3,
                    task_label='2nd Notice Letter',
                    task_due=task3_open,
                    urgency='YELLOW',
                    locked=True,
                    action=f'2nd notice available in {days_to_open}d (delivery: {delivery_date.strftime("%m/%d/%Y")})',
                )
            else:
                return dict(
                    task_num=3,
                    task_label='2nd Notice Letter',
                    task_due=task3_open,
                    urgency='GREEN',
                    locked=True,
                    action=f'Waiting — 2nd notice available {task3_open.strftime("%m/%d/%Y")} ({days_to_open}d)',
                )
        else:
            # Letter 1 sent but no delivery/RTS yet
            return dict(
                task_num=2,
                task_label='1st Notice — Awaiting Delivery',
                task_due=None,
                urgency='GREEN',
                locked=True,
                action='Waiting for USPS delivery confirmation or return-to-sender',
            )

    # ── TASK 2: 1st Notice Letter ─────────────────────────────────────────────
    # Backfill rule: vehicles with days_held >= 5 + no letter → Task 2 overdue
    # (regardless of Task 1 completion — historical vehicles assumed BMV was done offline)
    if days_held >= TASK2_OPEN_DAYS:
        days_overdue = (today - l1_due).days
        if days_overdue > 0:
            return dict(
                task_num=2,
                task_label='1st Notice Letter',
                task_due=l1_due,
                urgency='RED',
                locked=False,
                action=f'Send 1st notice letter ASAP — overdue {days_overdue}d (due {l1_due.strftime("%m/%d/%Y")})',
            )
        else:
            days_left = abs(days_overdue)
            return dict(
                task_num=2,
                task_label='1st Notice Letter',
                task_due=l1_due,
                urgency='YELLOW' if days_left <= YELLOW_WARN_DAYS else 'GREEN',
                locked=False,
                action=f'Send 1st notice letter by {l1_due.strftime("%m/%d/%Y")}',
            )

    # ── TASK 1: BMV Search (days_held < 5, letter not sent) ──────────────────
    days_until_task2 = (l1_due - today).days
    if task1_done:
        urgency = 'YELLOW' if days_until_task2 <= YELLOW_WARN_DAYS else 'GREEN'
        return dict(
            task_num=2,
            task_label='1st Notice Letter',
            task_due=l1_due,
            urgency=urgency,
            locked=True,
            action=f'BMV complete. Letter 1 opens in {days_until_task2}d on {l1_due.strftime("%m/%d/%Y")}',
        )
    else:
        urgency = 'YELLOW' if days_held >= (TASK2_OPEN_DAYS - YELLOW_WARN_DAYS) else 'GREEN'
        return dict(
            task_num=1,
            task_label='BMV Search',
            task_due=l1_due,
            urgency=urgency,
            locked=False,
            action=f'Complete BMV search — letter 1 due in {days_until_task2}d',
        )


def _na():
    return dict(task_num=0, task_label='N/A', task_due=None, urgency='NA', locked=False, action='')


def recalculate_all() -> dict:
    """
    Batch recalculate all active vehicles. Stores:
      - letter_urgency (RED/YELLOW/GREEN/NA) on Vehicle
      - current_task_num, current_task_label, current_task_due on Vehicle
    Also triggers Task 4 auto-handoff to Tina where eligible.
    Returns count dict.
    """
    from models import db, Vehicle
    from datetime import datetime

    today = date.today()
    counts = {'RED': 0, 'YELLOW': 0, 'GREEN': 0, 'COMPLETE': 0, 'NA': 0}

    vehicles = Vehicle.query.filter(
        Vehicle.status.in_(['ACTIVE', 'TITLE_FILED'])
    ).all()

    chunk = 0
    for v in vehicles:
        try:
            task = compute_task(v, today)
            urgency = task['urgency']

            v.letter_urgency   = urgency
            v.current_task_num = task['task_num']
            v.current_task_label = task['task_label']
            v.current_task_due = task['task_due']

            # Task 4 auto-handoff: trigger if urgency is YELLOW and task_num is 4
            # and vehicle hasn't already been handed to Tina
            if (task['task_num'] == 4
                    and not task['locked']
                    and not v.task4_triggered
                    and v.status == 'ACTIVE'):
                v.task4_triggered = True
                v.task4_triggered_date = today
                if not v.tina_stage:
                    v.tina_stage = 'QUEUED'
                v.updated_at = datetime.utcnow()

            counts[urgency] = counts.get(urgency, 0) + 1

        except Exception as exc:
            counts['NA'] = counts.get('NA', 0) + 1

        chunk += 1
        if chunk >= 250:
            db.session.flush()
            chunk = 0

    db.session.commit()
    return counts


def recalculate_vehicle(vehicle) -> dict:
    """Recalculate and persist task info for a single Vehicle. Returns task dict."""
    from models import db
    task = compute_task(vehicle, date.today())
    vehicle.letter_urgency    = task['urgency']
    vehicle.current_task_num  = task['task_num']
    vehicle.current_task_label = task['task_label']
    vehicle.current_task_due  = task['task_due']
    return task
