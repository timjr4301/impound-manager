from datetime import timedelta

MONTH_NAMES = ['Jan','Feb','Mar','Apr','May','Jun',
               'Jul','Aug','Sep','Oct','Nov','Dec']


def days_by_month(start, end):
    """Return dict of {(year, month): day_count} for date range inclusive."""
    buckets = {}
    current = start
    while current <= end:
        key = (current.year, current.month)
        buckets[key] = buckets.get(key, 0) + 1
        current += timedelta(days=1)
    return buckets


def calculate_storage(impound_date, end_date, daily_rate):
    """
    Compute storage fees from impound_date to end_date (inclusive).
    Returns (total_days, total_amount, breakdown)
    where breakdown is a list of (year, month, month_abbr, days, amount) tuples.
    """
    if not impound_date or not end_date or not daily_rate:
        return 0, 0.0, []
    total_days = max(0, (end_date - impound_date).days + 1)
    total_amount = round(total_days * daily_rate, 2)
    monthly = days_by_month(impound_date, end_date)
    breakdown = [
        (year, month, MONTH_NAMES[month - 1], days, round(days * daily_rate, 2))
        for (year, month), days in sorted(monthly.items())
    ]
    return total_days, total_amount, breakdown
