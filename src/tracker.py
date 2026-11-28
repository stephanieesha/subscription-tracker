"""
Core logic for the subscription tracker: normalizing costs across billing
cycles, computing totals, flagging upcoming renewals, and advancing
renewal dates correctly across month/year boundaries.

Kept entirely separate from Flask and storage so it's testable with plain
data, no server or file I/O needed.
"""

import calendar
from datetime import date, timedelta

CYCLES = ("weekly", "monthly", "yearly")


def add_months(d: date, months: int) -> date:
    """Adds calendar months to a date, correctly handling month-end
    rollovers (e.g. Jan 31 + 1 month = Feb 28, or Feb 29 in a leap year) -
    a naive `timedelta(days=30)` gets this wrong."""
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def add_cycles(d: date, cycle: str, count: int) -> date:
    """Steps a date forward by `count` billing cycles from the date given, not one cycle at a time, so a
    monthly renewal on the 31st stays on the 31st in months that have one."""
    if cycle == "weekly":
        return d + timedelta(days=7 * count)
    if cycle == "monthly":
        return add_months(d, count)
    if cycle == "yearly":
        return add_months(d, 12 * count)
    raise ValueError(f"Unknown billing cycle: {cycle}")


def add_cycle(d: date, cycle: str) -> date:
    return add_cycles(d, cycle, 1)


def normalize_to_monthly(amount: float, cycle: str) -> float:
    if cycle == "weekly":
        return amount * 52 / 12
    if cycle == "monthly":
        return amount
    if cycle == "yearly":
        return amount / 12
    raise ValueError(f"Unknown billing cycle: {cycle}")


def normalize_to_yearly(amount: float, cycle: str) -> float:
    if cycle == "weekly":
        return amount * 52
    if cycle == "monthly":
        return amount * 12
    if cycle == "yearly":
        return amount
    raise ValueError(f"Unknown billing cycle: {cycle}")


def compute_totals(subscriptions: list) -> dict:
    active = [s for s in subscriptions if s["status"] == "active"]
    monthly_total = sum(normalize_to_monthly(s["amount"], s["billing_cycle"]) for s in active)
    yearly_total = sum(normalize_to_yearly(s["amount"], s["billing_cycle"]) for s in active)
    return {
        "monthly_total": round(monthly_total, 2),
        "yearly_total": round(yearly_total, 2),
        "active_count": len(active),
    }


def upcoming_renewals(subscriptions: list, within_days: int = 7, today: date = None) -> list:
    """Active subscriptions renewing within the next `within_days` days,
    soonest first. `today` is injectable so this is testable without
    depending on the real current date."""
    if today is None:
        today = date.today()
    cutoff = today + timedelta(days=within_days)

    active = [s for s in subscriptions if s["status"] == "active"]
    upcoming = [
        s for s in active
        if today <= date.fromisoformat(s["next_renewal_date"]) <= cutoff
    ]
    upcoming.sort(key=lambda s: s["next_renewal_date"])
    return upcoming


def renew_subscription(sub: dict) -> dict:
    """Bumps a subscription's renewal date forward by one billing cycle -
    call this once a renewal has actually happened, so dates don't go stale."""
    current = date.fromisoformat(sub["next_renewal_date"])
    sub["next_renewal_date"] = add_cycle(current, sub["billing_cycle"]).isoformat()
    return sub


def roll_forward(sub: dict, today: date) -> bool:
    """Moves an active subscription's renewal date forward by whole billing cycles until it is today or later,
    so a renewal that passed without being marked as renewed counts as having happened. Returns True if the
    date changed."""
    if sub["status"] != "active":
        return False
    current = date.fromisoformat(sub["next_renewal_date"])
    if current >= today:
        return False
    steps = 1
    while add_cycles(current, sub["billing_cycle"], steps) < today:
        steps += 1
    sub["next_renewal_date"] = add_cycles(current, sub["billing_cycle"], steps).isoformat()
    return True


def roll_forward_all(subscriptions: list, today: date = None) -> bool:
    """Rolls every subscription forward. Returns True if any renewal date changed."""
    today = today or date.today()
    changes = [roll_forward(sub, today) for sub in subscriptions]
    return any(changes)


def cancel_subscription(sub: dict, today: date = None) -> dict:
    """Marks a subscription cancelled as of `today` (the real date if none is given) and records that date.
    Cancelling again keeps the first date. A subscription is never cancelled before it started, so a start
    date after `today`, which a change of time zone can cause, is used instead."""
    if sub.get("status") == "cancelled":
        return sub
    today = today or date.today()
    start = sub.get("start_date")
    if start and date.fromisoformat(start) > today:
        today = date.fromisoformat(start)
    sub["status"] = "cancelled"
    sub["cancelled_date"] = today.isoformat()
    return sub
