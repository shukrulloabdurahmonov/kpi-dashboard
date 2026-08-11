"""Date/period helpers. Monthly periods are 'YYYY-MM'; weekly periods are the
ISO date of the week's MONDAY (matching Redshift DATE_TRUNC('week', ...));
daily are 'YYYY-MM-DD'. All sort lexicographically, so range filters work on
string keys (weekly and daily share a format — the store's grain column
disambiguates)."""

from datetime import date, timedelta


def month_start(d):
    return d.replace(day=1)


def week_start(d):
    """Monday of d's week — same convention as Redshift DATE_TRUNC('week')."""
    return d - timedelta(days=d.weekday())


def add_weeks(d, n):
    return week_start(d) + timedelta(weeks=n)


def week_key(d):
    return week_start(d).isoformat()


def week_range(start, end_exclusive):
    """Monday dates from start's week up to (not including) end."""
    cur = week_start(start)
    while cur < end_exclusive:
        yield cur
        cur += timedelta(weeks=1)


def add_months(d, n):
    """Shift d by n months, returning the first day of the resulting month."""
    y, m = divmod(d.year * 12 + (d.month - 1) + n, 12)
    return date(y, m + 1, 1)


def month_key(d):
    return d.strftime("%Y-%m")


def day_key(d):
    return d.isoformat()


def period_key(d, grain):
    if grain == "monthly":
        return month_key(d)
    if grain == "weekly":
        return week_key(d)
    return day_key(d)


def month_range(start, end_exclusive):
    """Month-start dates from start's month up to (not including) end's month."""
    cur = month_start(start)
    while cur < end_exclusive:
        yield cur
        cur = add_months(cur, 1)


def day_range(start, end_exclusive):
    cur = start
    while cur < end_exclusive:
        yield cur
        cur += timedelta(days=1)
